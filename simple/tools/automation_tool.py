"""
自动化工具：cron 任务列表、gateway 状态查询
"""
import subprocess
from typing import Callable, Optional


def cron_list() -> str:
    """列出当前用户的 crontab 任务"""
    try:
        r = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or "(无 crontab 任务)"
        if "no crontab" in (r.stderr or "").lower():
            return "(无 crontab 任务)"
        return f"[错误] {r.stderr or 'crontab 不可用'}"
    except FileNotFoundError:
        return "[提示] 系统无 crontab 命令（如 Windows）"
    except Exception as e:
        return f"[错误] {e}"


def gateway_status(gateway_ref: Optional[object] = None) -> str:
    """
    查询 Gateway 状态：活跃 lane 数、等待队列等。
    gateway_ref 由 main 注入 Gateway 实例。
    """
    if gateway_ref is None:
        return "Gateway 状态: 未注入（仅工具调用时可用）"
    try:
        gw = gateway_ref
        queue = getattr(gw, "_queue", None)
        if queue:
            active_count = len(getattr(queue, "_active_lanes", set()))
            waiting = len(getattr(queue, "_waiting_lanes", []))
            return f"Gateway 状态: 活跃 lane={active_count}, 等待={waiting}"
        return "Gateway 状态: 运行中"
    except Exception as e:
        return f"[错误] 获取状态失败: {e}"


# gateway_status 需要运行时注入，通过 set_gateway_ref 设置
_gateway_ref: Optional[object] = None


def set_gateway_ref(ref: object):
    global _gateway_ref
    _gateway_ref = ref


def get_tools_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "cron_list",
                "description": "列出当前用户的 crontab 定时任务。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gateway_status",
                "description": "查询 LiteClaw Gateway 运行状态（活跃 lane、等待队列等）。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def _gateway_status_impl(**kw):
    return gateway_status(_gateway_ref)


TOOL_EXECUTORS = {
    "cron_list": lambda **kw: cron_list(),
    "gateway_status": _gateway_status_impl,
}
