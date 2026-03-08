"""
定时提醒调度器：持久化到 SQLite，进程重启后恢复
"""
import threading
import time

from util.log import log
from storage.db import get_storage
from im.feishu import send_text as feishu_send_text


_feishu_client = None


def set_feishu_client(client):
    """注入飞书客户端，供发送提醒使用"""
    global _feishu_client
    _feishu_client = client


def schedule(receive_id: str, receive_id_type: str, message: str, delay_minutes: int) -> int | None:
    """
    添加定时提醒并创建 Timer。返回 reminder_id。
    持久化到 DB，进程重启后会恢复。
    """
    trigger_at = int(time.time()) + delay_minutes * 60
    storage = get_storage()
    rid = storage.add_reminder(receive_id, receive_id_type, message, trigger_at)
    if rid is None:
        return None

    def _fire():
        client = _feishu_client
        if client:
            try:
                feishu_send_text(client, receive_id, receive_id_type, f"⏰ 提醒：{message}")
            except Exception as e:
                log("reminder", "发送提醒失败 rid=%d: %s", rid, e)
        storage.mark_reminder_sent(rid)

    timer = threading.Timer(delay_minutes * 60.0, _fire)
    timer.daemon = True
    timer.start()
    return rid


def start_scheduler():
    """
    启动时加载未触发的提醒，为每个创建 Timer。
    需在 set_feishu_client 之后调用。
    """
    client = _feishu_client
    if not client:
        log("reminder", "start_scheduler: feishu 未注入，跳过恢复提醒")
        return
    storage = get_storage()
    now = int(time.time())
    pending = storage.get_pending_reminders(now)
    if not pending:
        return
    log("reminder", "恢复 %d 个未触发的提醒", len(pending))
    for row in pending:
        rid, receive_id, receive_id_type, message, trigger_at = row
        delay_sec = max(0, trigger_at - now)

        def _fire(r=rid, recv=receive_id, recv_type=receive_id_type, msg=message):
            c = _feishu_client
            if c:
                try:
                    feishu_send_text(c, recv, recv_type, f"⏰ 提醒：{msg}")
                except Exception as e:
                    log("reminder", "发送提醒失败 rid=%d: %s", r, e)
            get_storage().mark_reminder_sent(r)

        timer = threading.Timer(delay_sec, _fire)
        timer.daemon = True
        timer.start()
