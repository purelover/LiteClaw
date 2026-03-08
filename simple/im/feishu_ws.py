"""
飞书长连接：WebSocket 接收事件，无需公网 URL
"""
import json
import re
from datetime import datetime
from pathlib import Path

import lark_oapi as lark

from util.log import log
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from im.feishu import (
    parse_message_content,
    download_message_image,
    download_message_file,
    image_bytes_to_data_url,
    _is_duplicate_message,
)


def create_client(app_id: str, app_secret: str):
    """创建飞书 API 客户端（用于发送消息）"""
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


def _safe_filename(name: str) -> str:
    """生成安全的文件名，避免路径注入"""
    base = re.sub(r'[^\w\u4e00-\u9fff\-\.]', '_', name)[:80]
    return base or "file"


def create_ws_client(
    app_id: str,
    app_secret: str,
    on_message,
    workspace_path: Path | str | None = None,
    log_level=lark.LogLevel.INFO,
):
    """
    创建飞书 WebSocket 长连接客户端。

    on_message(receive_id: str, message, receive_id_type: str)
    message: str 或 dict {"text": str, "images": [data_url, ...]}
    receive_id_type: "open_id"(单聊) 或 "chat_id"(群聊)
    workspace_path: 工作区路径，用于保存用户发送的文件到 inbox/
    """
    wp = Path(workspace_path) if workspace_path else None

    def _handle(data):
        log("feishu_ws", "收到事件, type=%s", type(data).__name__)
        try:
            event = getattr(data, "event", data)
            msg = event.message
            # message_id 可能在 data/event/msg 不同层级，SDK 结构因版本而异
            message_id = str(
                getattr(data, "message_id", None)
                or getattr(event, "message_id", None)
                or getattr(msg, "message_id", None)
                or getattr(msg, "id", None)
                or ""
            )
            if _is_duplicate_message(message_id):
                log("feishu_ws", "重复消息已跳过 message_id=%s", message_id[:20] if message_id else "")
                return
            chat_type = getattr(msg, "chat_type", "p2p")
            log("feishu_ws", "chat_type=%s", chat_type)
            text, image_key, file_key, file_name = parse_message_content(msg.content or "")
            log("feishu_ws", "解析文本: %r", text[:50] + "..." if len(text) > 50 else text)
            if not text and not image_key and not file_key:
                log("feishu_ws", "文本、图片、文件均为空，跳过")
                return
            images = []
            if image_key and message_id:
                raw = download_message_image(app_id, app_secret, str(message_id), image_key)
                if raw:
                    images.append(image_bytes_to_data_url(raw))
                    log("feishu_ws", "已下载图片, len=%d", len(raw))
            if file_key and message_id and wp:
                raw = download_message_file(app_id, app_secret, str(message_id), file_key)
                if raw:
                    inbox = wp / "inbox"
                    inbox.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    safe_name = _safe_filename(file_name)
                    (inbox / f"{ts}_{safe_name}").write_bytes(raw)
                    file_path_rel = f"inbox/{ts}_{safe_name}"
                    text = (text or "用户发送了一个文件") + f"，已保存到 workspace/{file_path_rel}，请根据文件内容处理。"
                    log("feishu_ws", "已下载文件 %s -> %s, len=%d", file_name, file_path_rel, len(raw))
                else:
                    text = (text or "用户发送了一个文件") + f"（{file_name}，下载失败）"
            elif file_key and not wp:
                text = (text or "用户发送了一个文件") + f"（{file_name}，未配置工作区无法保存）"
            if chat_type == "p2p":
                sender = event.sender
                receive_id = sender.sender_id.open_id if sender and sender.sender_id else ""
                receive_id_type = "open_id"
            else:
                receive_id = getattr(msg, "chat_id", "") or ""
                receive_id_type = "chat_id"
            log("feishu_ws", "receive_id=%s receive_id_type=%s", receive_id, receive_id_type)
            if not receive_id:
                log("feishu_ws", "receive_id 为空，跳过")
                return
            from im.feishu import add_message_reaction
            add_message_reaction(app_id, app_secret, message_id)
            payload = {"text": text, "images": images} if images else text
            log("feishu_ws", "调用 on_message(receive_id=%s, type=%s)", receive_id, receive_id_type)
            on_message(receive_id, payload, receive_id_type)
        except Exception as e:
            log("feishu_ws", "处理异常: %s", e)
            import traceback
            traceback.print_exc()

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")  # 长连接下填空字符串
        .register_p2_im_message_receive_v1(_handle)
        .build()
    )

    return lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=log_level,
    )
