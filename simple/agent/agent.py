"""
Agent 执行平面：LLM 推理 + 工具调用
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from storage.db import get_storage
from util.log import log
from util.channel_context import (
    set_tool_failure_state,
    get_tool_failure_state,
    set_session_key,
    clear_session_key,
)
from util.tokens import (
    estimate_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tools_tokens,
    truncate_messages_to_fit,
)
from storage.workspace import (
    load_workspace_prompt,
    build_memory_context,
    load_recitation_context,
)
from skills.loader import build_skills_prompt
from tools.registry import get_definitions, get_executors
from llm.compaction import (
    should_flush,
    should_compact,
    is_no_reply,
    MEMORY_FLUSH_PROMPT,
    COMPACTION_SUMMARY_PROMPT,
)


class Agent:
    def __init__(
        self,
        chat_fn,
        tools_enabled: bool = False,
        exec_timeout: int = 30,
        use_hybrid: bool = False,
        hybrid_chat_fn=None,
        workspace_path: Path | None = None,
        compaction_cfg: dict | None = None,
        skills_prompt: str | None = None,
        max_tool_rounds: int = 10,
        tool_failure_state: dict | None = None,
    ):
        self.chat_fn = chat_fn
        self.tool_failure_state = tool_failure_state or {}
        self.tools_enabled = tools_enabled
        self.exec_timeout = exec_timeout
        self.use_hybrid = use_hybrid
        self.hybrid_chat_fn = hybrid_chat_fn
        self.workspace_path = Path(workspace_path) if workspace_path else None
        self.compaction_cfg = compaction_cfg or {}
        self.skills_prompt = (skills_prompt or "").strip() if skills_prompt else ""
        self.max_tool_rounds = max_tool_rounds

    def _build_system_prompt(
        self,
        compaction_summary: str | None = None,
        current_task_reminder: str | None = None,
    ) -> str:
        """构建 system prompt：工作区+skills+日期 + 记忆 + 历史摘要 + Recitation(TODO/NOTES)"""
        preserved, memory, summary, recitation = self._build_system_segments(compaction_summary)
        parts = [preserved]
        if current_task_reminder:
            parts.append("## 当前用户待办（务必执行，勿仅回复确认）\n" + current_task_reminder)
        if memory:
            parts.append(memory)
        if summary:
            parts.append(summary)
        if recitation:
            parts.append(recitation)
        return "\n\n".join(p for p in parts if p)

    def _get_current_task_reminder(
        self, history: list[dict], user_content: str
    ) -> str | None:
        """当用户说「做吧」「继续」等简短跟进时，从历史中提取上一个实质性请求；或当用户发来任务型请求时强化「务必执行」"""
        uc = (user_content or "").strip()
        # 简短跟进：从历史提取上一个请求
        if len(uc) <= 30:
            follow_ups = ("做吧", "做啊", "你做", "继续", "快点", "别光说", "不要光打嘴炮")
            if any(f in uc for f in follow_ups):
                for h in reversed(history):
                    if h.get("role") == "user":
                        prev = (h.get("content") or "")
                        prev = prev if isinstance(prev, str) else str(prev)[:500]
                        if len(prev) > 15:
                            return prev
            return None
        # 任务型请求：强化务必执行，避免模型只回复不干活
        task_keywords = ("分析", "做成", "ppt", "发给我", "发给", "生成", "写一份", "做一份")
        if any(k in uc for k in task_keywords) and len(uc) > 20:
            return f"{uc}\n\n【务必执行】请调用工具完成上述请求，第一步应使用 serper_search 或 exec_python，不要只回复确认。"
        return None

    def _build_system_segments(self, compaction_summary: str | None = None) -> tuple[str, str, str, str]:
        """返回 (preserved, memory, summary, recitation)。preserved=工作区+skills+日期；recitation=TODO+NOTES 放末尾引导注意力"""
        preserved_parts = []
        if self.workspace_path and self.workspace_path.exists():
            prompt = load_workspace_prompt(self.workspace_path)
            if prompt:
                preserved_parts.append(prompt)
        if self.skills_prompt:
            preserved_parts.append(self.skills_prompt)
        if not preserved_parts:
            preserved_parts.append("你是 LiteClaw 助手，简洁友好地回答问题。")
        # 仅日期，避免时分破坏 KV-cache 命中率（Manus）
        preserved_parts.append(f"当前系统日期：{datetime.now().strftime('%Y-%m-%d')}")
        preserved = "\n\n".join(preserved_parts)

        memory = ""
        if self.workspace_path and self.workspace_path.exists():
            mem = build_memory_context(self.workspace_path)
            if mem:
                memory = mem

        summary = ""
        if compaction_summary:
            summary = "## 历史摘要\n" + compaction_summary

        recitation = ""
        if self.workspace_path and self.workspace_path.exists():
            recitation = load_recitation_context(self.workspace_path)

        return preserved, memory, summary, recitation

    def _truncate_system_by_segments(
        self,
        preserved: str,
        memory: str,
        summary: str,
        recitation: str,
        max_tokens: int,
    ) -> str:
        """仅截断 memory、summary、recitation，preserved（含 skills）完整保留"""
        preserved_tokens = estimate_tokens(preserved)
        room = max(0, max_tokens - preserved_tokens)
        if room <= 0:
            return preserved
        # memory : summary : recitation 按 2 : 1 : 1 分配
        mem_room = min(estimate_tokens(memory), room * 2 // 4)
        sum_room = min(estimate_tokens(summary), room // 4)
        rec_room = min(estimate_tokens(recitation), room - mem_room - sum_room)
        if len(memory) > mem_room * 2:
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
            hint = f"可 memory_get 读取: MEMORY.md, memory/{today}.md, memory/{yesterday}.md"
            mem_trunc = memory[: mem_room * 2].rstrip() + f"\n\n[... 记忆已截断，{hint}]"
        else:
            mem_trunc = memory
        sum_trunc = (
            summary[: sum_room * 2].rstrip() + "\n[... 更早摘要已截断]"
            if len(summary) > sum_room * 2
            else summary
        )
        rec_trunc = (
            recitation[: rec_room * 2].rstrip() + "\n[... 已截断]"
            if len(recitation) > rec_room * 2
            else recitation
        )
        parts = [preserved]
        if mem_trunc:
            parts.append(mem_trunc)
        if sum_trunc:
            parts.append(sum_trunc)
        if rec_trunc:
            parts.append(rec_trunc)
        return "\n\n".join(parts)

    def _make_system_truncate_fn(
        self,
        compaction_summary: str | None,
        suffix: str = "",
        current_task_reminder: str | None = None,
    ):
        """生成 system 截断函数：仅截 memory+summary+recitation，保留 preserved（含 skills）。suffix 为需追加的固定内容"""
        def fn(max_tokens: int) -> str:
            reserved = estimate_tokens(suffix) + (4 if suffix else 0)
            if current_task_reminder:
                reserved += estimate_tokens(current_task_reminder) + 80
            preserved, memory, summary, recitation = self._build_system_segments(compaction_summary)
            base = self._truncate_system_by_segments(
                preserved, memory, summary, recitation, max_tokens - reserved
            )
            if current_task_reminder:
                base += "\n\n## 当前用户待办（务必执行，勿仅回复确认）\n" + current_task_reminder
            return base + ("\n\n" + suffix if suffix else "")
        return fn

    def _run_tool(self, name: str, arguments: str) -> str:
        """执行单个工具调用"""
        executors = get_executors()
        if name not in executors:
            log("agent", "tool %s: 未知工具", name)
            return f"[错误] 未知工具: {name}"
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError:
            args = {}
        try:
            result = str(executors[name](**args))
            preview = result[:200] + "..." if len(result) > 200 else result
            if "[错误]" in result:
                log("agent", "tool %s: 返回错误, result_preview=%r", name, preview)
            else:
                log("agent", "tool %s: 成功, result_preview=%r", name, preview)
            return result
        except Exception as e:
            log("agent", "tool %s: 执行异常, error=%s", name, e)
            return f"[错误] 执行失败: {e}"

    def _run_memory_flush(self, session) -> None:
        """Pre-compaction memory flush：静默一轮让模型写入记忆"""
        mf = self.compaction_cfg.get("memory_flush", {}) or {}
        prompt = mf.get("prompt") or MEMORY_FLUSH_PROMPT
        system = self._build_system_prompt(
            compaction_summary=session.context.get("compaction_summary")
        )
        messages = [
            {"role": "system", "content": system + "\n\n" + prompt},
            *[{"role": h["role"], "content": h["content"]} for h in (session.conversation_history or [])],
            {"role": "user", "content": prompt},
        ]
        # 截断至窗口内，避免含 base64 图片等大体积内容时发送 2M+ tokens
        max_ctx = self.compaction_cfg.get("max_context_tokens") or self.compaction_cfg.get("context_window")
        if max_ctx:
            reserve = self.compaction_cfg.get("reserve_tokens", 4000)
            tools = get_definitions() if self.tools_enabled else None
            tools_tokens = estimate_tools_tokens(tools) if tools else 0
            max_for_msgs = max_ctx - reserve - tools_tokens
            if estimate_messages_tokens(messages) > max_for_msgs:
                compaction_summary = session.context.get("compaction_summary")
                sys_fn = self._make_system_truncate_fn(compaction_summary, suffix=prompt)
                messages = truncate_messages_to_fit(
                    messages, max_for_msgs, system_truncate_fn=sys_fn
                )
                log("agent", "memory_flush 截断至 %d 条 (max=%d)", len(messages), max_ctx)
        tools = get_definitions() if self.tools_enabled else None
        max_rounds = 5
        for _ in range(max_rounds):
            # 每轮前再次截断（tool 结果可能很大）
            if max_ctx and estimate_messages_tokens(messages) > max_for_msgs:
                compaction_summary = session.context.get("compaction_summary")
                sys_fn = self._make_system_truncate_fn(compaction_summary)
                messages = truncate_messages_to_fit(
                    messages, max_for_msgs, system_truncate_fn=sys_fn
                )
            resp = self.chat_fn(messages, tools)
            content = (resp.get("content") or "").strip()
            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                if is_no_reply(content):
                    log("agent", "memory_flush 模型返回 NO_REPLY，未写入记忆")
                else:
                    # Fallback：模型返回文本但未调用工具时，将内容作为当日摘要写入
                    if len(content) > 50 and self.workspace_path:
                        try:
                            from datetime import datetime
                            from storage.workspace import memory_append
                            path = f"memory/{datetime.now().strftime('%Y-%m-%d')}.md"
                            memory_append(path, f"[memory_flush 摘要]\n{content}", workspace=self.workspace_path)
                            log("agent", "memory_flush fallback: 已将模型回复(%d字)写入 %s", len(content), path)
                        except Exception as e:
                            log("agent", "memory_flush fallback 写入失败: %s", e)
                    else:
                        log("agent", "memory_flush 模型有回复(非 NO_REPLY)但无 tool_calls，已忽略")
                # 标记本周期已 flush，避免重复
                session.context["memory_flush_compaction_count"] = session.context.get(
                    "compaction_count", 0
                )
                get_storage().save_session(session)
                return
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                result = self._run_tool(fn.get("name", ""), fn.get("arguments", "{}"))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
        log("agent", "memory_flush 完成(工具轮次达上限)")

    def _run_compaction(self, session) -> None:
        """压缩历史：将旧消息摘要后保留最近部分，压缩后总 token 控制在 target 以内"""
        cfg = self.compaction_cfg
        ctx_window = cfg.get("context_window", 32000)
        reserve = cfg.get("reserve_tokens", 4000)
        keep_recent = cfg.get("keep_recent_tokens", 6000)
        target = cfg.get("target_after_compaction", 10000)
        summary_max = cfg.get("summary_max_tokens", 1500)

        history = list(session.conversation_history or [])
        if len(history) < 4:
            return

        # 估算 system 基础大小（不含 compaction_summary）
        base_system = self._build_system_prompt(compaction_summary=None)
        base_tokens = estimate_tokens(base_system)
        # keep_recent 受 target 约束：target = base + summary + recent + reserve
        keep_recent = min(keep_recent, max(2000, target - reserve - base_tokens - summary_max))

        # 从末尾取 keep_recent_tokens 内的消息
        recent: list[dict] = []
        tokens = 0
        for m in reversed(history):
            t = estimate_message_tokens(m)
            if tokens + t > keep_recent:
                break
            recent.insert(0, m)
            tokens += t

        old = history[: len(history) - len(recent)]
        if not old:
            return

        # 摘要旧消息；含错误信息的消息保留更多内容，便于模型自我修正（Manus: keep the wrong stuff in）
        def _history_snippet(m: dict, default_len: int = 500) -> str:
            content = (m.get("content") or "") if isinstance(m.get("content"), str) else str(m.get("content") or "")
            is_error = "[错误]" in content or "执行失败" in content or "Error" in content or "error" in content
            limit = 800 if is_error else default_len
            return f"{m.get('role','')}: {content[:limit]}" + ("..." if len(content) > limit else "")

        history_text = "\n".join(_history_snippet(m) for m in old)
        summary_prompt = COMPACTION_SUMMARY_PROMPT.format(history=history_text)
        summary_messages = [
            {"role": "system", "content": "你是一个对话摘要助手，只输出摘要，不要其他内容。"},
            {"role": "user", "content": summary_prompt},
        ]
        try:
            resp = self.chat_fn(summary_messages, None)
            summary = (resp.get("content") or "").strip()[:500]
        except Exception as e:
            log("agent", "compaction 摘要失败: %s", e)
            summary = "[摘要生成失败，已截断旧消息]"

        prev_summary = session.context.get("compaction_summary", "")
        new_summary = (prev_summary + "\n" + summary).strip() if prev_summary else summary
        # 限制摘要总长度，防止无限增长
        if estimate_tokens(new_summary) > summary_max:
            new_summary = new_summary[: summary_max * 2] + "\n[... 更早摘要已截断]"

        compaction_count = session.context.get("compaction_count", 0) + 1

        session.conversation_history = recent
        session.context["compaction_summary"] = new_summary
        session.context["compaction_count"] = compaction_count
        get_storage().save_session(session)
        total_est = base_tokens + estimate_tokens(new_summary) + sum(estimate_message_tokens(m) for m in recent)
        log("agent", "compaction 完成: 保留 %d 条, 约 %d tokens, compaction_count=%d", len(recent), total_est, compaction_count)

    def _build_user_content(self, message) -> str | list:
        """将 message 转为 LLM 可用的 content。支持 str 或 dict {"text", "images"}"""
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            text = (message.get("text") or "").strip()
            images = message.get("images") or []
            if not images:
                return text
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            for url in images:
                parts.append({"type": "image_url", "image_url": {"url": url}})
            return parts if parts else text
        return str(message)

    def _update_tool_failure_state(self, name: str, result: str) -> None:
        """根据工具执行结果更新 tool_failure_state，用于 hybrid fallback"""
        if self.tool_failure_state is None:
            return
        state = get_tool_failure_state()
        is_fail = "[错误]" in (result or "")
        if is_fail:
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
            if name in ("exec_python", "exec_command"):
                state["exec_failed"] = True  # 常执行 Python，1 次失败即触发 fallback
            if state.get("exec_failed") or state.get("consecutive_failures", 0) >= 2:
                state["use_cloud"] = True
                log("agent", "tool_failure_state: 触发 fallback (exec_failed=%s, consecutive=%d)",
                    state.get("exec_failed"), state.get("consecutive_failures", 0))
        else:
            # 工具成功时重置 fallback 状态，下一轮可再尝试本地
            state["consecutive_failures"] = 0
            state.pop("exec_failed", None)
            state.pop("use_cloud", None)

    def run(self, session, message) -> str:
        """处理用户消息，返回回复文本。message 可为 str 或 dict {"text", "images"}"""
        # 每轮对话开始时重置工具失败状态（用 ContextVar 按请求隔离，避免多会话并发互相覆盖）
        if self.tool_failure_state is not None:
            set_tool_failure_state({})
        set_session_key(session.session_key)
        try:
            return self._run_impl(session, message)
        finally:
            clear_session_key()

    def _run_impl(self, session, message) -> str:
        """run 的实际实现，供 try/finally 包裹"""
        storage = get_storage()
        history = list(session.conversation_history or [])
        compaction_summary = session.context.get("compaction_summary")
        user_content = self._build_user_content(message)
        task_rem = self._get_current_task_reminder(history, user_content)
        system = self._build_system_prompt(
            compaction_summary=compaction_summary,
            current_task_reminder=task_rem,
        )

        messages = [{"role": "system", "content": system}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_content})

        # Token 统计与 compaction（对齐 OpenClaw）
        compaction_enabled = self.compaction_cfg.get("enabled", False)
        if compaction_enabled:
            context_tokens = estimate_messages_tokens(messages)
            ctx_window = self.compaction_cfg.get("context_window", 32000)
            max_ctx = self.compaction_cfg.get("max_context_tokens")
            if max_ctx:
                ctx_window = min(ctx_window, max_ctx)  # 以实际窗口为准
            reserve = self.compaction_cfg.get("reserve_tokens", 4000)
            mf = self.compaction_cfg.get("memory_flush", {}) or {}
            mf_enabled = mf.get("enabled", False)
            soft = mf.get("soft_threshold_tokens", 2000)
            compaction_count = session.context.get("compaction_count", 0)
            last_flush = session.context.get("memory_flush_compaction_count", -1)

            # 用户发来新的实质性请求时，优先处理请求，推迟 memory_flush 避免目标丢失
            uc_len = len(str(user_content or ""))
            defer_flush = uc_len > 50 and mf_enabled
            if should_flush(
                context_tokens, ctx_window, reserve, soft, last_flush, compaction_count
            ) and mf_enabled and not defer_flush:
                log("agent", "memory_flush 触发 (context_tokens≈%d)", context_tokens)
                self._run_memory_flush(session)
                history = list(session.conversation_history or [])
                compaction_summary = session.context.get("compaction_summary")
                task_rem = self._get_current_task_reminder(history, user_content)
                system = self._build_system_prompt(
                    compaction_summary=compaction_summary,
                    current_task_reminder=task_rem,
                )
                messages = [{"role": "system", "content": system}]
                for h in history:
                    messages.append({"role": h["role"], "content": h["content"]})
                messages.append({"role": "user", "content": user_content})

            if should_compact(context_tokens, ctx_window, reserve):
                log("agent", "compaction 触发 (context_tokens≈%d)", context_tokens)
                self._run_compaction(session)
                history = list(session.conversation_history or [])
                compaction_summary = session.context.get("compaction_summary")
                task_rem = self._get_current_task_reminder(history, user_content)
                system = self._build_system_prompt(
                    compaction_summary=compaction_summary,
                    current_task_reminder=task_rem,
                )
                messages = [{"role": "system", "content": system}]
                for h in history:
                    messages.append({"role": h["role"], "content": h["content"]})
                messages.append({"role": "user", "content": user_content})

        tools = get_definitions() if self.tools_enabled else None
        round_count = 0
        max_ctx = self.compaction_cfg.get("max_context_tokens")
        reserve = self.compaction_cfg.get("reserve_tokens", 4000)

        while round_count < self.max_tool_rounds:
            round_count += 1
            # 超窗口时截断，保留 system 工具指令与最近对话
            if max_ctx:
                tools_tokens = estimate_tools_tokens(tools) if tools else 0
                max_for_msgs = max_ctx - reserve - tools_tokens
                if estimate_messages_tokens(messages) > max_for_msgs:
                    orig = len(messages)
                    compaction_summary = session.context.get("compaction_summary")
                    sys_fn = self._make_system_truncate_fn(
                        compaction_summary,
                        current_task_reminder=task_rem,
                    )
                    messages = truncate_messages_to_fit(
                        messages, max_for_msgs, system_truncate_fn=sys_fn
                    )
                    if len(messages) < orig:
                        log("agent", "context 截断 %d -> %d 条 (max=%d)", orig, len(messages), max_ctx)
            if tools:
                resp = self.chat_fn(messages, tools)
            else:
                if self.use_hybrid and self.hybrid_chat_fn:
                    content = self.hybrid_chat_fn(messages)
                    resp = {"content": content, "tool_calls": []}
                else:
                    resp = self.chat_fn(messages, None)

            content = resp.get("content", "") or ""
            tool_calls = resp.get("tool_calls") or []

            # 若 API 未返回 tool_calls 但 content 中有工具调用 JSON，尝试解析并执行
            if not tool_calls and content and self.tools_enabled:
                from llm.tool_call_parser import parse_tool_calls_from_text
                parsed = parse_tool_calls_from_text(content)
                if parsed:
                    tool_calls = parsed
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        log("agent", "从文本解析到 tool_call: %s args=%s", fn.get("name", ""), fn.get("arguments", "{}"))

            if not tool_calls:
                # 无工具调用，结束
                storage.append_message(session.session_key, "user", user_content)
                storage.append_message(session.session_key, "assistant", content)
                return content

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}
                log("agent", "tool_call: %s args=%s", name, args)

            # 执行工具调用
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "{}")
                result = self._run_tool(name, args)
                self._update_tool_failure_state(name, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        # 超过轮数，返回最后内容
        storage.append_message(session.session_key, "user", user_content)
        storage.append_message(session.session_key, "assistant", content)
        return content or "(工具调用轮次过多，请重试)"
