"""
Gateway 控制平面：接收 Channel 推送，管理 Session，调度队列，协调 Agent
"""
from util.log import log
from dataclasses import dataclass
from typing import Callable

from .session import SessionManager
from .queue import LaneQueue, Job


@dataclass
class GatewayConfig:
    max_concurrent_lanes: int = 10


class Gateway:
    """Gateway：会话管理 + 消息队列 + 路由"""

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig()
        self.session_manager = SessionManager()
        self._queue: LaneQueue | None = None
        self._reply_sender: Callable[[dict, str], None] | None = None
        self._agent_factory: Callable[[], Callable[[Job], None]] | None = None

    def set_reply_sender(self, fn: Callable[[dict, str], None]):
        """设置回复发送器：fn(channel_info, reply_text)"""
        self._reply_sender = fn

    def set_agent_factory(self, factory: Callable[["Gateway"], Callable[[Job], None]]):
        """设置 Agent 工厂，接收 Gateway 实例，返回 worker(job) 函数"""
        self._agent_factory = factory

    def start(self):
        """启动队列 worker"""
        if not self._agent_factory:
            raise RuntimeError("Agent factory not set")
        worker = self._create_worker()
        self._queue = LaneQueue(worker, self.config.max_concurrent_lanes)

    def _create_worker(self) -> Callable[[Job], None]:
        agent_fn = self._agent_factory(self)

        def worker(job: Job):
            self.session_manager.get_or_create(
                job.session_key,
                job.channel_info,
            )
            self.session_manager.set_state(job.session_key, "processing")
            try:
                reply = agent_fn(job)
                if self._reply_sender:
                    self._reply_sender(job.channel_info, reply or "")
                # 对话历史由 Agent 负责持久化，此处不再重复 append
            finally:
                self.session_manager.set_state(job.session_key, "idle")

        return worker

    def on_inbound(self, session_key: str, channel_info: dict, message: str):
        """Channel 入站：将消息加入队列"""
        msg_preview = (message[:50] if isinstance(message, str) else str(message)[:80]) if message else ""
        log("gateway", "on_inbound session_key=%s message=%r", session_key, msg_preview)
        if not self._queue:
            raise RuntimeError("Gateway not started")
        job = Job(
            session_key=session_key,
            message=message,
            channel_info=channel_info,
            reply_callback=lambda r: self._reply_sender(channel_info, r) if self._reply_sender else None,
        )
        log("gateway", "enqueue job")
        self._queue.enqueue(job)
