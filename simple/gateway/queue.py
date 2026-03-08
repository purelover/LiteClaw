"""
Lane-based 消息队列：同 sessionKey 串行，不同 sessionKey 可并行
"""
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Callable, Any, Union

from util.log import log


@dataclass
class Job:
    session_key: str
    message: Union[str, dict]  # str 或 {"text": str, "images": [data_url, ...]}
    channel_info: dict
    reply_callback: Callable[[str], None]


class LaneQueue:
    """
    Lane 队列：每个 sessionKey 一个 lane，同 lane 内串行执行。
    不同 lane 可并行，受 max_concurrent_lanes 限制。
    """

    def __init__(self, worker: Callable[[Job], None], max_concurrent_lanes: int = 10):
        self.worker = worker
        self.max_concurrent_lanes = max_concurrent_lanes
        self._lane_queues: dict[str, queue.Queue] = {}
        self._lane_locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._active_lanes: set[str] = set()
        self._waiting_lanes: list[str] = []

    def _get_lane_queue(self, session_key: str) -> queue.Queue:
        with self._global_lock:
            if session_key not in self._lane_queues:
                self._lane_queues[session_key] = queue.Queue()
                self._lane_locks[session_key] = threading.Lock()
            return self._lane_queues[session_key]

    def enqueue(self, job: Job):
        """将任务加入对应 lane 的队列"""
        log("queue", "enqueue session_key=%s", job.session_key)
        q = self._get_lane_queue(job.session_key)
        q.put(job)
        self._schedule(job.session_key)

    def _schedule(self, session_key: str):
        """尝试调度：若该 lane 空闲且全局未满，则启动 worker"""
        with self._global_lock:
            if session_key in self._active_lanes:
                return
            q = self._lane_queues.get(session_key)
            if not q or q.empty():
                return
            if len(self._active_lanes) >= self.max_concurrent_lanes:
                if session_key not in self._waiting_lanes:
                    self._waiting_lanes.append(session_key)
                return
            self._active_lanes.add(session_key)
            threading.Thread(target=self._process_lane, args=(session_key,), daemon=True).start()

    def _process_lane(self, session_key: str):
        """处理单个 lane 的队列（串行）"""
        log("queue", "_process_lane 开始 session_key=%s", session_key)
        q = self._lane_queues[session_key]
        while True:
            try:
                job = q.get_nowait()
            except queue.Empty:
                break
            msg_preview = (job.message[:30] if isinstance(job.message, str) else str(job.message)[:50]) if job.message else ""
            log("queue", "worker 处理 job message=%r", msg_preview)
            try:
                self.worker(job)
                log("queue", "worker 完成")
            except Exception as e:
                log("queue", "worker 异常: %s", type(e).__name__)
                log("queue", "worker 异常详情: %s", e)
                traceback.print_exc()
                try:
                    job.reply_callback(f"[错误] {e}")
                except Exception:
                    pass
            finally:
                q.task_done()

        with self._global_lock:
            self._active_lanes.discard(session_key)
            # 若本 lane 又有新任务，继续调度
            if not q.empty():
                self._schedule(session_key)
            # 否则尝试调度等待中的 lane
            elif self._waiting_lanes and len(self._active_lanes) < self.max_concurrent_lanes:
                next_key = self._waiting_lanes.pop(0)
                self._schedule(next_key)
