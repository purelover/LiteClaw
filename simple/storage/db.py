"""
SQLite 存储：Session 与对话历史持久化
"""
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

from threading import Lock


@dataclass
class Session:
    """会话：channelInfo、state、context、conversationHistory"""
    session_key: str
    channel_info: dict
    state: str = "idle"  # idle | processing | waiting
    context: dict = field(default_factory=dict)
    conversation_history: list[dict] = field(default_factory=list)

    def to_row(self) -> tuple:
        return (
            self.session_key,
            json.dumps(self.channel_info, ensure_ascii=False),
            self.state,
            json.dumps(self.context, ensure_ascii=False),
            json.dumps(self.conversation_history, ensure_ascii=False),
        )

    @classmethod
    def from_row(cls, row: tuple) -> "Session":
        return cls(
            session_key=row[0],
            channel_info=json.loads(row[1] or "{}"),
            state=row[2] or "idle",
            context=json.loads(row[3] or "{}"),
            conversation_history=json.loads(row[4] or "[]"),
        )


class Storage:
    """SQLite 存储"""

    def __init__(self, db_path: str | Path, max_turns: int = 20):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self.max_turns = max_turns
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_key TEXT PRIMARY KEY,
                        channel_info TEXT,
                        state TEXT DEFAULT 'idle',
                        context TEXT,
                        conversation_history TEXT,
                        updated_at INTEGER
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sessions_updated
                    ON sessions(updated_at)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        receive_id TEXT NOT NULL,
                        receive_id_type TEXT NOT NULL DEFAULT 'open_id',
                        message TEXT NOT NULL,
                        trigger_at INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at INTEGER
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reminders_trigger
                    ON reminders(status, trigger_at)
                """)
                conn.commit()
            finally:
                conn.close()

    def get_session(self, session_key: str) -> Session | None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT session_key, channel_info, state, context, conversation_history FROM sessions WHERE session_key = ?",
                    (session_key,),
                ).fetchone()
                if row:
                    return Session.from_row(row)
                return None
            finally:
                conn.close()

    def save_session(self, session: Session):
        import time
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO sessions
                       (session_key, channel_info, state, context, conversation_history, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (*session.to_row(), int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    def append_message(self, session_key: str, role: str, content: Union[str, list]):
        """追加一条对话消息。content 可为 str 或 multimodal list"""
        session = self.get_session(session_key)
        if not session:
            return
        session.conversation_history.append({"role": role, "content": content})
        # 限制历史长度，保留最近 N 轮（compaction 启用时 max_turns 会更大）
        if len(session.conversation_history) > self.max_turns * 2 + 1:
            session.conversation_history = session.conversation_history[-(self.max_turns * 2 + 1):]
        self.save_session(session)

    def add_reminder(
        self,
        receive_id: str,
        receive_id_type: str,
        message: str,
        trigger_at: int,
    ) -> int | None:
        """添加定时提醒，返回 reminder id"""
        import time
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute(
                    """INSERT INTO reminders (receive_id, receive_id_type, message, trigger_at, status, created_at)
                       VALUES (?, ?, ?, ?, 'pending', ?)""",
                    (receive_id, receive_id_type, message, trigger_at, int(time.time())),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def get_pending_reminders(self, now_ts: int) -> list[tuple]:
        """获取未触发的提醒 (id, receive_id, receive_id_type, message, trigger_at)"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                rows = conn.execute(
                    """SELECT id, receive_id, receive_id_type, message, trigger_at
                       FROM reminders WHERE status = 'pending' AND trigger_at > ? ORDER BY trigger_at""",
                    (now_ts,),
                ).fetchall()
                return [tuple(r) for r in rows]
            finally:
                conn.close()

    def mark_reminder_sent(self, reminder_id: int):
        """标记提醒已发送"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
                conn.commit()
            finally:
                conn.close()

    def search_conversation_history(
        self, session_key: str, query: str, limit: int = 5
    ) -> str:
        """
        在当前会话的对话历史中搜索关键词，返回匹配的摘录。
        用于「咱们之前聊过xxx，翻一下当时的结论」类请求。
        """
        session = self.get_session(session_key)
        if not session or not session.conversation_history:
            return "(无历史对话)"
        query_lower = query.lower().strip()
        if not query_lower:
            return "(请提供搜索关键词)"
        results = []
        for m in session.conversation_history:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in content
                )
            else:
                text = str(content or "")
            if query_lower in text.lower():
                excerpt = text.strip()[:400] + ("..." if len(text) > 400 else "")
                results.append(f"[{role}] {excerpt}")
                if len(results) >= limit:
                    break
        return "\n\n".join(results) if results else "(未找到匹配)"


_storage: Storage | None = None


def init_storage(db_path: str | Path = "data/liteclaw.db", max_turns: int = 20):
    global _storage
    _storage = Storage(db_path, max_turns=max_turns)


def get_storage() -> Storage:
    if _storage is None:
        raise RuntimeError("Storage not initialized")
    return _storage
