"""
当前请求的渠道上下文，供 send_image 等 IM 工具使用。
Worker 在处理每条消息前设置，工具执行时读取。
"""
from contextvars import ContextVar
from typing import Any

_channel_ctx: ContextVar[dict | None] = ContextVar("channel_context", default=None)
_tool_failure_state: ContextVar[dict] = ContextVar("tool_failure_state", default=None)
_session_key: ContextVar[str | None] = ContextVar("session_key", default=None)


def set_channel_context(
    channel_info: dict,
    feishu_client: Any,
    feishu_creds: dict | None = None,
) -> None:
    """设置当前请求的渠道上下文（在 worker 中调用）。feishu_creds 可选，含 app_id/app_secret 供 send_file 上传用"""
    ctx = {"channel_info": channel_info, "feishu": feishu_client}
    if feishu_creds:
        ctx["feishu_app_id"] = feishu_creds.get("app_id")
        ctx["feishu_app_secret"] = feishu_creds.get("app_secret")
    _channel_ctx.set(ctx)


def clear_channel_context() -> None:
    """清除渠道上下文"""
    try:
        _channel_ctx.set(None)
    except LookupError:
        pass


def get_channel_context() -> dict | None:
    """获取当前渠道上下文，供工具调用"""
    return _channel_ctx.get()


def set_tool_failure_state(state: dict) -> None:
    """设置当前请求的工具失败状态（供 hybrid fallback），按请求/线程隔离"""
    _tool_failure_state.set(state)


def get_tool_failure_state() -> dict:
    """获取当前请求的工具失败状态，不存在时返回空 dict"""
    s = _tool_failure_state.get()
    return s if s is not None else {}


def set_session_key(session_key: str | None) -> None:
    """设置当前请求的 session_key，供 history_search 等工具使用"""
    _session_key.set(session_key)


def get_session_key() -> str | None:
    """获取当前请求的 session_key"""
    return _session_key.get()


def clear_session_key() -> None:
    """清除 session_key"""
    try:
        _session_key.set(None)
    except LookupError:
        pass
