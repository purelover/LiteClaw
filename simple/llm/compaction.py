"""
Context 压缩与 pre-compaction memory flush
对齐 OpenClaw 的 compaction 与 memoryFlush
"""
from util.log import log


NO_REPLY = "NO_REPLY"

MEMORY_FLUSH_SYSTEM = "Session nearing compaction. Store durable memories now. You MUST respond with a tool call to memory_append (path + content), not plain text. Write to memory/YYYY-MM-DD.md for daily notes, MEMORY.md for long-term facts, TODO.md for task lists, or NOTES.md for structured notes."
MEMORY_FLUSH_PROMPT = "Call memory_append tool with path and content. Extract key facts, preferences, task progress, or a brief summary. Use memory/YYYY-MM-DD.md (today), MEMORY.md, TODO.md, or NOTES.md. Reply with NO_REPLY only if there is truly nothing worth storing. Do not output plain text—only a tool call or NO_REPLY."

COMPACTION_SUMMARY_PROMPT = """请将以下对话历史压缩为一段简洁摘要（200字以内），保留：关键决策、用户偏好、未完成事项、重要事实、失败/错误信息（便于后续自我修正）。只输出摘要，不要其他内容。

对话历史：
---
{history}
---
摘要："""


def should_flush(
    context_tokens: int,
    context_window: int,
    reserve_tokens: int,
    soft_threshold_tokens: int,
    last_flush_compaction_count: int,
    compaction_count: int,
) -> bool:
    """是否应触发 memory flush（每 compaction 周期最多一次）"""
    threshold = context_window - reserve_tokens - soft_threshold_tokens
    if context_tokens < threshold:
        return False
    if last_flush_compaction_count >= compaction_count:
        return False  # 本周期已 flush
    return True


def should_compact(
    context_tokens: int,
    context_window: int,
    reserve_tokens: int,
) -> bool:
    """是否应触发 compaction"""
    return context_tokens > context_window - reserve_tokens


def is_no_reply(text: str) -> bool:
    """检查是否为 NO_REPLY（不交付用户）"""
    return (text or "").strip().upper().startswith(NO_REPLY)
