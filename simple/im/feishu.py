"""
飞书机器人：Webhook 接收消息 + API 发送回复
"""
import base64
import io
import json
import re
from pathlib import Path
from typing import Optional

import lark_oapi as lark
import requests
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from util.log import log


def add_message_reaction(
    app_id: str, app_secret: str, message_id: str, emoji_type: str = "SMILE"
) -> bool:
    """给消息添加表情回复（如 😊 表示正在处理），失败时静默返回 False"""
    if not message_id:
        return False
    token = _get_tenant_token(app_id, app_secret)
    if not token:
        return False
    try:
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"reaction_type": {"emoji_type": emoji_type}},
            timeout=5,
        )
        if r.status_code in (200, 201):
            return True
        err_body = r.text[:200] if r.text else ""
        log("feishu", "add_message_reaction 失败 message_id=%s status=%d body=%s",
            message_id[:24] if message_id else "", r.status_code, err_body)
        return False
    except Exception as e:
        log("feishu", "add_message_reaction 异常 message_id=%s: %s", message_id[:24] if message_id else "", e)
        return False


def create_client(app_id: str, app_secret: str):
    """创建飞书客户端"""
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .build()
    )


def send_text(client, receive_id: str, receive_id_type: str, text: str):
    """发送文本消息"""
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        .build()
    )
    return client.im.v1.message.create(req)


def is_rich_content(text: str) -> bool:
    """判断内容是否含富文本特征（标题、表格、代码块等），适合用 interactive 卡片 lark_md 发送"""
    if not text or len(text) < 60:
        return False
    patterns = (
        r"^#{1,6}\s+",
        r"\*\*[^*]+\*\*",
        r"^\|.+\|",
        r"^```",
        r"^---+$",
    )
    for p in patterns:
        if re.search(p, text, re.MULTILINE):
            return True
    return False


def send_post(client, receive_id: str, receive_id_type: str, text: str) -> bool:
    """
    发送富文本消息。使用 interactive 卡片的 lark_md 标签，飞书会直接渲染 Markdown，
    内容原样发送不做转换。成功返回 True；超限或失败时 fallback 到 send_text 并返回 False。
    """
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "div",
                "text": {
                    "content": text,
                    "tag": "lark_md",
                },
            },
        ],
    }
    body_str = json.dumps(card, ensure_ascii=False)
    if len(body_str.encode("utf-8")) > 28 * 1024:
        log("feishu", "send_post 超 28KB，fallback 文本")
        send_text(client, receive_id, receive_id_type, text)
        return False

    try:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(body_str)
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.create(req)
        if not resp.success():
            log("feishu", "send_post API 失败 code=%s msg=%s", getattr(resp, "code", ""), getattr(resp, "msg", resp))
            send_text(client, receive_id, receive_id_type, text)
            return False
        log("feishu", "send_post 成功 (interactive+lark_md)")
        return True
    except Exception as e:
        log("feishu", "send_post 异常: %s", e)
        send_text(client, receive_id, receive_id_type, text)
        return False


# 文件上传：飞书 im/v1/files 返回 file_key，用于 msg_type=file
# file_type 映射：扩展名 -> 飞书类型（stream 为通用二进制）
_FILE_TYPE_MAP = {
    ".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls", ".xlsx": "xls",
    ".ppt": "ppt", ".pptx": "ppt", ".mp4": "mp4", ".mp3": "mp3", ".wav": "mp3",
    ".txt": "stream", ".csv": "stream", ".json": "stream", ".xml": "stream",
    ".zip": "stream", ".tar": "stream", ".gz": "stream",
    ".jpg": "stream", ".jpeg": "stream", ".png": "stream", ".gif": "stream",
    ".webp": "stream", ".bmp": "stream", ".ico": "stream",
}


def _upload_file_to_feishu(
    app_id: str, app_secret: str, file_path: Path, file_type: str, file_name: str
) -> Optional[str]:
    """上传文件到飞书，返回 file_key 或 None"""
    token = _get_tenant_token(app_id, app_secret)
    if not token:
        return None
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        # 飞书 im/v1/files 要求 multipart/form-data
        files = {"file": (file_name, io.BytesIO(data), "application/octet-stream")}
        payload = {"file_type": file_type, "file_name": file_name}
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data=payload,
            files=files,
            timeout=30,
        )
        resp = r.json()
        if resp.get("code") == 0 and resp.get("data", {}).get("file_key"):
            return resp["data"]["file_key"]
    except Exception:
        pass
    return None


def send_file(
    client,
    receive_id: str,
    receive_id_type: str,
    file_path: str | Path,
    app_id: str,
    app_secret: str,
) -> str | None:
    """
    上传文件并发送给用户。支持 pdf、doc、xls、ppt、mp4、mp3、图片及常见格式。返回 None 表示成功。
    需权限：im:message:send_as_bot、获取与上传图片或文件资源
    文件大小不超过 30M。
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return f"[错误] 文件不存在: {file_path}"
    size = p.stat().st_size
    if size > 30 * 1024 * 1024:
        return "[错误] 文件超过 30M 限制"
    if size == 0:
        return "[错误] 不能上传空文件"
    suffix = p.suffix.lower()
    file_type = _FILE_TYPE_MAP.get(suffix, "stream")
    file_name = p.name
    file_key = _upload_file_to_feishu(app_id, app_secret, p, file_type, file_name)
    if not file_key:
        return "[错误] 上传文件失败"
    try:
        content = json.dumps({"file_key": file_key})
        msg_req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("file")
                .content(content)
                .build()
            )
            .build()
        )
        msg_resp = client.im.v1.message.create(msg_req)
        if not msg_resp.success():
            return f"[错误] 发送文件失败: {msg_resp.msg}"
        return None
    except Exception as e:
        return f"[错误] 发送文件异常: {e}"


def send_image(client, receive_id: str, receive_id_type: str, file_path: str | Path) -> str | None:
    """
    上传图片并发送给用户。返回 None 表示成功，否则返回错误信息。
    需权限：im:message:send_as_bot
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return f"[错误] 文件不存在: {file_path}"
    suffix = p.suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".ico", ".tiff", ".tif"):
        return f"[错误] 不支持的图片格式: {suffix}"
    try:
        with open(p, "rb") as f:
            create_req = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                )
                .build()
            )
            create_resp = client.im.v1.image.create(create_req)
        if not create_resp.success():
            return f"[错误] 上传图片失败: {create_resp.msg}"
        msg_req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("image")
                .content(lark.JSON.marshal(create_resp.data))
                .build()
            )
            .build()
        )
        msg_resp = client.im.v1.message.create(msg_req)
        if not msg_resp.success():
            return f"[错误] 发送图片失败: {msg_resp.msg}"
        return None
    except Exception as e:
        return f"[错误] 发送图片异常: {e}"


def _get_tenant_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取 tenant_access_token"""
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = r.json()
        return data.get("tenant_access_token") if data.get("code") == 0 else None
    except Exception:
        return None


def download_message_image(
    app_id: str,
    app_secret: str,
    message_id: str,
    file_key: str,
) -> Optional[bytes]:
    """
    下载消息中的图片。使用「获取消息中的资源文件」API。
    file_key: 即 content 中的 image_key
    """
    return _download_message_resource(app_id, app_secret, message_id, file_key, "image")


def download_message_file(
    app_id: str,
    app_secret: str,
    message_id: str,
    file_key: str,
) -> Optional[bytes]:
    """
    下载消息中的文件。使用「获取消息中的资源文件」API，type=file。
    支持文档、音频、视频等，单文件 ≤100MB。
    """
    return _download_message_resource(app_id, app_secret, message_id, file_key, "file")


def _download_message_resource(
    app_id: str,
    app_secret: str,
    message_id: str,
    file_key: str,
    resource_type: str,
) -> Optional[bytes]:
    """下载消息中的资源（image 或 file）"""
    token = _get_tenant_token(app_id, app_secret)
    if not token:
        return None
    try:
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"type": resource_type},
            timeout=60,
        )
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


# 消息去重：飞书可能对同一消息推送多次（WebSocket/Webhook 共用）
# 仅基于 message_id，不按 image_key 去重（用户可能多次发送同一张图）
_SEEN_MESSAGE_IDS: set[str] = set()
_MAX_SEEN = 500


def _is_duplicate_message(message_id: str) -> bool:
    """返回 True 表示重复，应跳过"""
    if not message_id:
        return False
    if message_id in _SEEN_MESSAGE_IDS:
        return True
    if len(_SEEN_MESSAGE_IDS) >= _MAX_SEEN:
        _SEEN_MESSAGE_IDS.clear()
    _SEEN_MESSAGE_IDS.add(message_id)
    return False


def image_bytes_to_data_url(data: bytes, media_type: str = "image/jpeg") -> str:
    """将图片字节转为 data URL，供 vision 模型使用"""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{b64}"


def parse_message_content(content: str) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """
    解析消息 content JSON。
    返回 (text, image_key, file_key, file_name)。
    文本: {"text": "xxx"} -> (text, None, None, None)
    图片: {"image_key": "img_xxx"} -> ("用户发送了一张图片", image_key, None, None)
    文件: {"file_key": "xxx", "file_name": "a.pdf"} -> ("用户发送了一个文件", None, file_key, file_name)
    """
    try:
        body = json.loads(content)
        text = (body.get("text") or "").strip()
        image_key = body.get("image_key")
        file_key = body.get("file_key")
        file_name = body.get("file_name") or "file"
        if not text and image_key:
            text = "用户发送了一张图片"
        if not text and file_key:
            text = f"用户发送了一个文件：{file_name}"
        return (text or "", image_key, file_key, file_name)
    except Exception:
        return ("", None, None, None)


def build_event_handler(
    feishu_client,
    app_id: str,
    app_secret: str,
    verification_token: str,
    encrypt_key: str,
    on_message,
    workspace_path: Optional[Path] = None,
):
    """
    构建事件处理器。支持单聊和群聊（@机器人）。
    on_message(receive_id: str, message, receive_id_type: str)
    message: str 或 dict {"text": str, "images": [data_url, ...]}
    workspace_path: 工作区路径，用于保存用户发送的文件到 inbox/
    """
    from datetime import datetime
    from pathlib import Path
    import re

    def _safe_filename(name: str) -> str:
        base = re.sub(r'[^\w\u4e00-\u9fff\-\.]', '_', name)[:80]
        return base or "file"

    def _handle(data):
        event = data.event
        msg = event.message
        message_id = str(
            getattr(data, "message_id", None)
            or getattr(event, "message_id", None)
            or getattr(msg, "message_id", None)
            or getattr(msg, "id", None)
            or ""
        )
        if _is_duplicate_message(message_id):
            return
        chat_type = getattr(msg, "chat_type", "p2p")
        text, image_key, file_key, file_name = parse_message_content(msg.content or "")
        if not text and not image_key and not file_key:
            return
        images = []
        if image_key and message_id:
            raw = download_message_image(app_id, app_secret, str(message_id), image_key)
            if raw:
                images.append(image_bytes_to_data_url(raw))
        if file_key and message_id and workspace_path:
            raw = download_message_file(app_id, app_secret, str(message_id), file_key)
            if raw:
                wp = Path(workspace_path)
                inbox = wp / "inbox"
                inbox.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = _safe_filename(file_name)
                (inbox / f"{ts}_{safe_name}").write_bytes(raw)
                file_path_rel = f"inbox/{ts}_{safe_name}"
                text = (text or "用户发送了一个文件") + f"，已保存到 workspace/{file_path_rel}，请根据文件内容处理。"
            else:
                text = (text or "用户发送了一个文件") + f"（{file_name}，下载失败）"
        elif file_key and not workspace_path:
            text = (text or "用户发送了一个文件") + f"（{file_name}，未配置工作区无法保存）"
        if chat_type == "p2p":
            sender = event.sender
            receive_id = sender.sender_id.open_id if sender and sender.sender_id else ""
            receive_id_type = "open_id"
        else:
            receive_id = getattr(msg, "chat_id", "") or ""
            receive_id_type = "chat_id"
        if not receive_id:
            return
        add_message_reaction(app_id, app_secret, message_id)
        payload = {"text": text, "images": images} if images else text
        on_message(receive_id, payload, receive_id_type)

    return (
        lark.EventDispatcherHandler.builder(verification_token, encrypt_key)
        .register_p2_im_message_receive_v1(_handle)
        .build()
    )
