"""
Token 估算：用于 context 管理与 compaction 触发
对齐 OpenClaw 的 token 统计
"""
import re


def estimate_tokens(text: str) -> int:
    """估算文本 token 数。中英混合约 1 token ≈ 2 字符"""
    if not text:
        return 0
    return max(1, len(text) // 2)


def _estimate_image_url_tokens(part: dict) -> int:
    """估算 image_url 的 token 数。图片由模型单独处理，不计入 context 文本估算"""
    return 0


def estimate_message_tokens(m: dict) -> int:
    """估算单条 message 的 token 数"""
    role = m.get("role", "")
    content = m.get("content", "") or ""
    total = estimate_tokens(f"role:{role}")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    total += _estimate_image_url_tokens(part)
    else:
        total += estimate_tokens(str(content))
    for tc in m.get("tool_calls") or []:
        total += estimate_tokens(str(tc.get("function", {}).get("arguments", "")))
    return total


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算 messages 列表的总 token 数。支持 multimodal content（list）"""
    total = 0
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        if isinstance(content, list):
            # multimodal: [{type: "text", text: "..."}, {type: "image_url", ...}]
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += _estimate_image_url_tokens(part)
        else:
            total += estimate_tokens(str(content))
        total += estimate_tokens(f"role:{role}")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += estimate_tokens(str(fn.get("arguments", "")))
    return total


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    """估算 tools 定义的 token 数"""
    if not tools:
        return 0
    total = 0
    for t in tools:
        total += estimate_tokens(str(t))
    return total


TOOL_RESULT_MAX_CHARS = 1200  # 超长 tool 结果截断，保留关键信息（路径、错误等）


def _summarize_tool_result(content: str, max_chars: int = TOOL_RESULT_MAX_CHARS) -> str:
    """超长 tool 结果截断，保留错误信息、文件路径等关键信息"""
    if not content or len(content) <= max_chars:
        return content
    # 错误信息必须保留，便于模型自我修正（Manus: keep the wrong stuff in）
    if "[错误]" in content or "执行失败" in content or "Error" in content:
        head = content[: max_chars - 50]
        return head.rstrip() + f"\n\n[... 已截断，原长 {len(content)} 字]"
    # 成功结果：保留首部（通常含路径）+ 含路径/关键信息的行
    head = content[: max_chars - 100].rstrip()
    lines = content.split("\n")
    path_lines = [ln for ln in lines if re.search(r"(文件路径|path|Path|\.pptx|\.pdf|\.xlsx|\.docx|workspace/|/workspace/)", ln, re.I)]
    path_block = "\n".join(path_lines[:5]) if path_lines else ""
    if path_block and path_block.strip() not in head:
        return head + f"\n\n[关键路径信息]\n{path_block}\n[原长 {len(content)} 字]"
    return head + f"\n\n[... 已截断，原长 {len(content)} 字]"


def _strip_image_urls(content) -> str | list:
    """将 content 中的 image_url 替换为占位符，用于截断时减小体积"""
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                out.append({"type": "text", "text": "[图片已省略]"})
            else:
                out.append(part)
        return out
    return content


def truncate_messages_to_fit(
    messages: list[dict],
    max_tokens: int,
    *,
    keep_system_head_chars: int = 6000,
    keep_system_tail_chars: int = 1500,
    system_truncate_fn=None,
) -> list[dict]:
    """
    截断 messages 使其不超过 max_tokens。
    优先保留：system（含工具指令、skills）、最近对话。
    system_truncate_fn(max_tokens)->str: 若提供，按段落截断 system（仅截记忆+摘要，保留 skills）；否则用 head+tail。
    对含 image_url 的消息，若会超限则用占位符替换以保留结构。
    """
    total = estimate_messages_tokens(messages)
    if total <= max_tokens:
        return messages
    reserve = 500
    target = max_tokens - reserve
    result = []
    tokens_used = 0

    if messages and messages[0].get("role") == "system":
        sys_msg = messages[0].copy()
        sys_content = sys_msg.get("content", "") or ""
        if isinstance(sys_content, str):
            sys_tokens = estimate_tokens(sys_content)
            max_for_system = target // 2
            if sys_tokens > max_for_system:
                if system_truncate_fn:
                    sys_content = system_truncate_fn(max_for_system)
                else:
                    head = sys_content[:keep_system_head_chars]
                    tail = sys_content[-keep_system_tail_chars:] if len(sys_content) > keep_system_tail_chars else ""
                    if len(sys_content) > keep_system_head_chars + keep_system_tail_chars:
                        sys_content = head + "\n\n[... 中间部分已截断 ...]\n\n" + tail
                    else:
                        sys_content = sys_content[: (target // 2) * 2]
                sys_msg["content"] = sys_content
        result.append(sys_msg)
        tokens_used += estimate_message_tokens(sys_msg)
        rest = messages[1:]
    else:
        rest = list(messages)

    kept = []
    rest_list = list(rest)
    for i, m in enumerate(reversed(rest_list)):
        # Tool result clearing: 超长 tool 结果用占位符替换
        m_use = m
        if m.get("role") == "tool":
            content = m.get("content") or ""
            if isinstance(content, str) and len(content) > TOOL_RESULT_MAX_CHARS:
                m_use = m.copy()
                m_use["content"] = _summarize_tool_result(content)
                t = estimate_message_tokens(m_use)
            else:
                t = estimate_message_tokens(m)
        else:
            t = estimate_message_tokens(m)
        if tokens_used + t <= target:
            kept.insert(0, m_use)
            tokens_used += t
        else:
            # 超限：最后一条（当前 user）必须保留，用占位符或截断
            is_last = i == 0
            if is_last:
                content = m.get("content") or ""
                has_img = isinstance(content, list) and any(
                    p.get("type") == "image_url" for p in content if isinstance(p, dict)
                )
                if has_img:
                    stripped = m.copy()
                    stripped["content"] = _strip_image_urls(content)
                    t_stripped = estimate_message_tokens(stripped)
                    if tokens_used + t_stripped <= target:
                        kept.insert(0, stripped)
                else:
                    # 纯文本过长：截断至可容纳
                    room = max(100, target - tokens_used)
                    truncated = m.copy()
                    txt = str(content)[: room * 2]
                    if len(str(content)) > len(txt):
                        txt = txt.rstrip() + "\n[... 已截断]"
                    truncated["content"] = txt
                    kept.insert(0, truncated)
            break
    result.extend(kept)
    return result
