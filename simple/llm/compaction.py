"""
Context 压缩与 memory flush（共享 compaction 结果）
- 增量 compaction：按 chunk 压缩，避免 O(n²) attention
- memory flush：基于 compaction 摘要 + 现有 memory 文件，单次 LLM 调用追加
"""
from util.log import log


NO_REPLY = "NO_REPLY"

# 增量 compaction：每 chunk 输入为「本块内容 + 当前摘要」，输出为新摘要（≤summary_max）
COMPACTION_CHUNK_PROMPT = """请将以下「本块对话」与「当前摘要」合并为一段简洁摘要（200字以内），保留：关键决策、用户偏好、未完成事项、重要事实、失败/错误信息。只输出摘要，不要其他内容。

本块对话：
---
{chunk}
---

当前摘要：
---
{prev_summary}
---

合并后的新摘要："""

# memory flush：基于 compaction 摘要 + 现有 memory，追加到 memory/YYYY-MM-DD.md 与 MEMORY.md
MEMORY_FLUSH_FROM_SUMMARY_PROMPT = """你正在根据「对话摘要」与「现有记忆内容」决定是否追加到记忆文件。

【重要】仅追加摘要中有价值且现有记忆中尚未包含的内容。严禁重复追加 memory 中已有的内容。

请调用 memory_append 工具，path 填 memory/YYYY-MM-DD.md（当日）或 MEMORY.md，content 填要追加的纯文本。
- memory/YYYY-MM-DD.md：当日笔记、进度、临时事实
- MEMORY.md：长期事实、用户偏好、重要决策

若摘要中无新内容可写，或内容已存在于下方 memory 中，请回复 NO_REPLY。不要输出纯文本，仅 tool call 或 NO_REPLY。"""


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
