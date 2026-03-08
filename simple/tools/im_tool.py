"""
IM 工具：send_image、send_file、schedule_reminder - 发送图片/文件、定时提醒（飞书）
依赖渠道上下文，需在 worker 中设置 set_channel_context(channel_info, feishu, feishu_creds)
"""
from pathlib import Path

from util.channel_context import get_channel_context
from im.feishu import (
    send_image as feishu_send_image,
    send_file as feishu_send_file,
)
from tools.reminder_scheduler import schedule as reminder_schedule


def send_image(file_path: str, workspace: Path | None = None) -> str:
    """
    将图片发送给当前对话用户。file_path 可为 workspace 内相对路径或绝对路径。
    用户说「截图发给我」「把图发给我」时，先 browser_screenshot 保存，再调用本工具发送。
    """
    ctx = get_channel_context()
    if not ctx:
        return "[错误] 当前渠道不支持发送图片"
    channel_info = ctx.get("channel_info", {})
    feishu = ctx.get("feishu")
    if not feishu:
        return "[错误] 飞书客户端未就绪"
    receive_id = channel_info.get("receive_id") or channel_info.get("peer_id")
    receive_id_type = channel_info.get("receive_id_type", "open_id")
    if not receive_id:
        return "[错误] 无法获取接收者 ID"
    # 解析路径：若为相对路径且提供 workspace，则相对于 workspace
    p = Path(file_path)
    if not p.is_absolute() and workspace:
        p = (workspace / p).resolve()
    else:
        # 若传入 /workspace/xxx 形式，视为 workspace 内相对路径
        if p.is_absolute() and workspace and str(p).startswith("/workspace/"):
            rel = str(p)[len("/workspace/"):].lstrip("/")
            p = (workspace / rel).resolve()
        else:
            p = p.resolve()
    err = feishu_send_image(feishu, receive_id, receive_id_type, p)
    if err:
        return err
    return f"已发送图片: {file_path}"


def send_file(file_path: str, workspace: Path | None = None) -> str:
    """
    将文件发送给当前对话用户。支持 pdf、doc、xls、ppt、mp4、mp3、图片及常见格式（≤30M）。
    用户说「把这个文件发给我」「把报告发给我」时使用。
    """
    ctx = get_channel_context()
    if not ctx:
        return "[错误] 当前渠道不支持发送文件"
    channel_info = ctx.get("channel_info", {})
    feishu = ctx.get("feishu")
    app_id = ctx.get("feishu_app_id")
    app_secret = ctx.get("feishu_app_secret")
    if not feishu or not app_id or not app_secret:
        return "[错误] 飞书客户端或凭证未就绪"
    receive_id = channel_info.get("receive_id") or channel_info.get("peer_id")
    receive_id_type = channel_info.get("receive_id_type", "open_id")
    if not receive_id:
        return "[错误] 无法获取接收者 ID"
    p = Path(file_path)
    if not p.is_absolute() and workspace:
        p = (workspace / p).resolve()
    else:
        # 若传入 /workspace/xxx 形式，视为 workspace 内相对路径
        if p.is_absolute() and workspace and str(p).startswith("/workspace/"):
            rel = str(p)[len("/workspace/"):].lstrip("/")
            p = (workspace / rel).resolve()
        else:
            p = p.resolve()
    err = feishu_send_file(
        feishu, receive_id, receive_id_type, p, app_id, app_secret
    )
    if err:
        return err
    return f"已发送文件: {file_path}"


def schedule_reminder(delay_minutes: int, message: str) -> str:
    """
    设置定时提醒：N 分钟后向当前对话发送提醒消息。
    用户说「5分钟后提醒我站起来」「10分钟后叫我」时使用。
    持久化到 SQLite，进程重启后会自动恢复。
    """
    ctx = get_channel_context()
    if not ctx:
        return "[错误] 当前渠道不支持定时提醒"
    channel_info = ctx.get("channel_info", {})
    receive_id = channel_info.get("receive_id") or channel_info.get("peer_id")
    receive_id_type = channel_info.get("receive_id_type", "open_id")
    if not receive_id:
        return "[错误] 无法获取接收者信息"
    if delay_minutes < 1 or delay_minutes > 1440:  # 1 分钟 ~ 24 小时
        return "[错误] 延迟需在 1~1440 分钟之间"
    text = (message or "时间到了").strip() or "时间到了"
    rid = reminder_schedule(receive_id, receive_id_type, text, delay_minutes)
    if rid is None:
        return "[错误] 保存提醒失败"
    return f"已设置 {delay_minutes} 分钟后的提醒：{text}"


def get_tools_definitions(workspace: Path | None = None) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "schedule_reminder",
                "description": "设置定时提醒：N 分钟后向当前对话发送提醒消息。用户说「5分钟后提醒我站起来」「10分钟后叫我」「半小时后提醒我开会」时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delay_minutes": {"type": "integer", "description": "延迟分钟数，如 5、10、30"},
                        "message": {"type": "string", "description": "提醒内容，如「站起来」「开会」"},
                    },
                    "required": ["delay_minutes", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_image",
                "description": "将图片文件发送给当前对话用户（飞书）。用户说「截图发给我」「把图发给我」时，先 browser_screenshot 保存到 workspace，再调用本工具传入保存路径发送。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "图片路径，可为 workspace 内相对路径（如 screenshot.png）或绝对路径",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_file",
                "description": "将文件发送给当前对话用户（飞书）。支持 pdf、doc、xls、ppt、mp4、mp3、图片及常见格式，≤30M。用户说「把这个文件发给我」「把报告发给我」时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径，可为 workspace 内相对路径（如 report.pdf）或绝对路径",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        },
    ]


def _make_executors(workspace: Path | None = None) -> dict:
    wp = Path(workspace) if workspace else None
    return {
        "schedule_reminder": lambda **kw: schedule_reminder(
            int(kw.get("delay_minutes", 0) or 0),
            kw.get("message", "") or "",
        ),
        "send_image": lambda **kw: send_image(kw.get("file_path", ""), workspace=wp),
        "send_file": lambda **kw: send_file(kw.get("file_path", ""), workspace=wp),
    }
