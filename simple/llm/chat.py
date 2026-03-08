"""
统一 LLM 调用：支持 tools，返回 content + tool_calls
"""
from typing import Callable

from openai import OpenAI

from util.log import log
from llm.tool_call_parser import parse_tool_calls_from_text


def chat_with_tools(
    client: OpenAI,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    extra_body: dict | None = None,
) -> dict:
    """
    调用 LLM，支持工具。
    返回 {"content": str, "tool_calls": list}，tool_calls 可能为空。
    extra_body: 透传给 create（如 Ollama 的 think=False）
    """
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if extra_body:
        kwargs["extra_body"] = extra_body

    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    content = msg.content or ""
    tool_calls = []
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        for tc in msg.tool_calls:
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            })
    # tool_calls 为空时，尝试从 reasoning/thinking 解析（Ollama Qwen 等可能把 tool call 放此处，即使 content 有文本）
    if not tool_calls:
        try:
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else vars(msg)
            for field in ("reasoning", "thinking", "content"):
                raw = msg_dict.get(field) if isinstance(msg_dict, dict) else getattr(msg, field, None)
                if raw:
                    parsed = parse_tool_calls_from_text(str(raw))
                    if parsed:
                        tool_calls = parsed
                        log("llm", "chat_with_tools 从 %s 解析到 %d 个 tool_calls", field, len(parsed))
                        break
        except Exception as e:
            log("llm", "chat_with_tools 解析 reasoning 失败: %s", e)

    # 仍为空时记录诊断
    if not content and not tool_calls:
        choice = resp.choices[0] if resp.choices else None
        finish = getattr(choice, "finish_reason", None) if choice else None
        usage = getattr(resp, "usage", None)
        usage_str = f"prompt={usage.prompt_tokens} completion={usage.completion_tokens}" if usage else ""
        log("llm", "chat_with_tools 空响应: finish_reason=%r %s", finish, usage_str or "")
        try:
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else vars(msg)
            keys = list(msg_dict.keys()) if isinstance(msg_dict, dict) else str(msg_dict)
            log("llm", "chat_with_tools 空响应 message keys: %s", keys)
            for k in ("thinking", "reasoning", "refusal", "content"):
                if k in msg_dict and msg_dict.get(k):
                    v = msg_dict[k]
                    preview = (str(v)[:300] + "...") if len(str(v)) > 300 else str(v)
                    log("llm", "chat_with_tools 空响应 message.%s: %s", k, preview)
        except Exception as e:
            log("llm", "chat_with_tools 空响应 解析 message 失败: %s", e)
    return {"content": content, "tool_calls": tool_calls}


def chat_stream(
    client: OpenAI,
    model: str,
    messages: list[dict],
    stream_callback: Callable[[str], None],
) -> str:
    """流式调用，通过 callback 推送每个 chunk，返回完整内容"""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
    )
    full = ""
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            c = chunk.choices[0].delta.content
            full += c
            stream_callback(c)
    return full
