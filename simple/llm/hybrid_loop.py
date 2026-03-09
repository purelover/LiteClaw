"""
本地 + 云端大模型协同 Loop

流程：
1. 默认先用本地模型处理
2. fallback 条件：content 为空且 tool_calls=0；或根据工具执行结果（连续失败 2 次 / exec_python 失败 1 次）
3. 不再依赖模型自报 [STATUS:fail]
"""
from typing import Callable

from util.log import log


def run_hybrid_parse(
    instruction: str,
    system_prompt: str,
    *,
    call_local: Callable[[list[dict]], str],
    call_cloud: Callable[[str, list[dict]], str],
    cloud_chain: list[str],
) -> str:
    """
    执行协同 parse（无对话历史）。
    call_local(messages) -> str
    call_cloud(model_id, messages) -> str
    cloud_chain: [model_id, ...]，按优先级排列
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": instruction},
    ]
    return run_hybrid_chat(
        messages,
        call_local=call_local,
        call_cloud=call_cloud,
        cloud_chain=cloud_chain,
    )


def run_hybrid_chat(
    messages: list[dict],
    *,
    call_local: Callable[[list[dict]], str],
    call_cloud: Callable[[str, list[dict]], str],
    cloud_chain: list[str],
) -> str:
    """
    执行协同 chat（支持对话历史）。无工具时，content 为空则 fallback。
    """
    has_cloud = bool(cloud_chain)
    log("hybrid", "本地 parse...")
    result = call_local(messages)
    content = (result or "").strip()
    log("hybrid", "本地返回 len=%d", len(result) if result else 0)
    if has_cloud and not content:
        log("hybrid", "本地 content 为空，fallback 云端")
    elif content:
        return content

    for i, model_id in enumerate(cloud_chain):
        log("hybrid", "云端 model[%d]=%s...", i, model_id[:20] + "..." if len(model_id) > 20 else model_id)
        result = call_cloud(model_id, messages)
        content = (result or "").strip()
        log("hybrid", "云端返回 len=%d", len(result) if result else 0)
        if content:
            return content

    return content if content else (call_local(messages) or "")


def run_hybrid_chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    call_local: Callable[[list[dict], list[dict]], dict],
    call_cloud: Callable[[str, list[dict], list[dict]], dict],
    cloud_chain: list[str],
    cloud_index: int = -1,
) -> dict:
    """
    执行协同 chat_with_tools，支持链式 fallback。

    call_local(messages, tools) -> {"content": str, "tool_calls": list}
    call_cloud(model_id, messages, tools) -> {"content": str, "tool_calls": list}

    cloud_index: -1=先试本地；>=0 则跳过本地，直接用 cloud_chain[cloud_index]（超长时取最后一个）
    链式 fallback：本地 -> cloud[0] -> cloud[1] -> ... -> cloud[last]，每个模型单独累计失败条件
    """
    has_cloud = bool(cloud_chain)

    # cloud_index >= 0：工具失败触发 fallback，跳过本地，用云端指定模型
    if cloud_index >= 0 and has_cloud:
        idx = min(cloud_index, len(cloud_chain) - 1)
        model_id = cloud_chain[idx]
        log("hybrid", "chat_with_tools fallback 到云端 model[%d]=%s...",
            idx, model_id[:20] + "..." if len(model_id) > 20 else model_id)
        resp = call_cloud(model_id, messages, tools)
        content = resp.get("content", "") or ""
        tool_calls = resp.get("tool_calls") or []
        log("hybrid", "chat_with_tools 云端返回 len=%d tool_calls=%d", len(content), len(tool_calls))
        return {"content": (content or "").strip(), "tool_calls": tool_calls}

    # Step 1: 本地模型
    log("hybrid", "chat_with_tools 本地...")
    resp = call_local(messages, tools)
    content = resp.get("content", "") or ""
    tool_calls = resp.get("tool_calls") or []
    content_stripped = (content or "").strip()

    log("hybrid", "chat_with_tools 本地返回 len=%d tool_calls=%d", len(content), len(tool_calls))
    # 保留：content 为空且 tool_calls=0 时 fallback
    should_fallback = has_cloud and not tool_calls and not content_stripped
    if not should_fallback:
        return {"content": content_stripped, "tool_calls": tool_calls}
    log("hybrid", "chat_with_tools 本地 content 为空且无 tool_calls，fallback 云端")

    # Step 2: 云端链条
    for i, model_id in enumerate(cloud_chain):
        log("hybrid", "chat_with_tools 云端 model[%d]=%s...", i, model_id[:20] + "..." if len(model_id) > 20 else model_id)
        resp = call_cloud(model_id, messages, tools)
        content = resp.get("content", "") or ""
        tool_calls = resp.get("tool_calls") or []
        content_stripped = (content or "").strip()
        log("hybrid", "chat_with_tools 云端返回 len=%d tool_calls=%d", len(content), len(tool_calls))
        return {"content": content_stripped, "tool_calls": tool_calls}

    return {"content": content_stripped, "tool_calls": []}
