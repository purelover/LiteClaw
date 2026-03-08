"""
Session 管理：per-channel-peer 隔离，维护会话状态与对话历史
"""
from dataclasses import dataclass
from typing import Any

from storage.db import Session, get_storage


@dataclass
class ChannelInfo:
    """渠道信息，用于回复路由"""
    channel: str
    peer_id: str
    extra: dict = None

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


class SessionManager:
    """会话管理器"""

    def __init__(self):
        self._storage = get_storage()

    def get_or_create(
        self,
        session_key: str,
        channel_info: dict,
    ) -> Session:
        """获取或创建 Session，per-channel-peer 隔离"""
        session = self._storage.get_session(session_key)
        if session is None:
            session = Session(
                session_key=session_key,
                channel_info=channel_info,
            )
            self._storage.save_session(session)
        else:
            # 更新 channel_info（如 receive_id 等）
            session.channel_info.update(channel_info)
            self._storage.save_session(session)
        return session

    def get(self, session_key: str) -> Session | None:
        return self._storage.get_session(session_key)

    def append_history(self, session_key: str, role: str, content: str):
        self._storage.append_message(session_key, role, content)

    def set_state(self, session_key: str, state: str):
        """更新 session 状态。从 DB 重新加载再保存，避免覆盖 Agent 已 append 的 conversation_history"""
        session = self._storage.get_session(session_key)
        if session:
            session.state = state
            self._storage.save_session(session)
