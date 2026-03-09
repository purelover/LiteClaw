#!/usr/bin/env python3
"""
对比 memory flush prompt 顺序对速度的影响：指令在前 vs 指令在后。
模拟 db 尚无 compaction_summary 时，用全部消息作为输入。
使用 OpenAI 客户端 + chat_with_tools，与正式 memory_flush 请求结构对齐（含 tools、think=False）。
用法：
  python scripts/bench_memory_flush_prompt.py --order front   # 指令在前（默认）
  python scripts/bench_memory_flush_prompt.py --order back    # 指令在后
  python scripts/bench_memory_flush_prompt.py --order both     # 两种都测，对比
  python scripts/bench_memory_flush_prompt.py --summary "自定义摘要"  # 覆盖，用摘要而非全部消息
  python scripts/bench_memory_flush_prompt.py --session-key feishu:xxx  # 指定 session
  python scripts/bench_memory_flush_prompt.py --workspace data/workspace  # 指定 workspace
  python scripts/bench_memory_flush_prompt.py --no-tools  # 不传 tools（对比用）
  python scripts/bench_memory_flush_prompt.py --simple-test  # 简单测试：只发 hi
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# 项目根
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

MEMORY_FLUSH_FROM_SUMMARY_PROMPT = """你正在根据「对话摘要」与「现有记忆内容」决定是否追加到记忆文件。

【重要】仅追加摘要中有价值且现有记忆中尚未包含的内容。严禁重复追加 memory 中已有的内容。

请调用 memory_append 工具，path 填 memory/YYYY-MM-DD.md（当日）或 MEMORY.md，content 填要追加的纯文本。
- memory/YYYY-MM-DD.md：当日笔记、进度、临时事实
- MEMORY.md：长期事实、用户偏好、重要决策

若摘要中无新内容可写，或内容已存在于下方 memory 中，请回复 NO_REPLY。不要输出纯文本，仅 tool call 或 NO_REPLY。"""


def load_config():
    import yaml
    for name in ("config.yaml", "config.example.yaml"):
        p = BASE / name
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            local = cfg.get("local", {}) or {}
            tools = cfg.get("tools", {}) or {}
            mem = tools.get("memory", {}) or {}
            file_cfg = tools.get("file", {}) or {}
            storage = cfg.get("storage", {}) or {}
            workspace = mem.get("workspace") or file_cfg.get("workspace", "data/workspace")
            db_path = storage.get("db_path", "data/liteclaw.db")
            if not Path(workspace).is_absolute():
                workspace = BASE / workspace
            if not Path(db_path).is_absolute():
                db_path = BASE / db_path
            return (
                local.get("base_url", "http://localhost:11434/v1"),
                local.get("model", "qwen3.5fast_longcontext"),
                Path(workspace),
                Path(db_path),
            )
    return "http://localhost:11434/v1", "qwen3.5fast_longcontext", BASE / "data" / "workspace", BASE / "data" / "liteclaw.db"


def _read_file_safe(p: Path) -> str:
    if not p.exists() or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def load_memory_for_flush(workspace: Path) -> str:
    parts = []
    mem = _read_file_safe(workspace / "MEMORY.md")
    if mem:
        parts.append("## MEMORY.md（长期记忆）\n" + mem)
    today = datetime.now().strftime("%Y-%m-%d")
    daily = _read_file_safe(workspace / "memory" / f"{today}.md")
    if daily:
        parts.append(f"## memory/{today}.md（当日记忆）\n" + daily)
    return "\n\n".join(parts) if parts else "(无现有记忆)"


def _history_snippet(m: dict, default_len: int = 500) -> str:
    content = (m.get("content") or "") if isinstance(m.get("content"), str) else str(m.get("content") or "")
    is_error = "[错误]" in content or "执行失败" in content or "Error" in content or "error" in content
    limit = 800 if is_error else default_len
    return f"{m.get('role','')}: {content[:limit]}" + ("..." if len(content) > limit else "")


def load_messages_from_db(db_path: Path, session_key: str | None = None) -> str:
    """从 DB 加载会话消息，转为文本。session_key 为空时取最近更新的 session"""
    if not db_path.exists():
        return "(DB 不存在，无可用消息)"
    conn = sqlite3.connect(db_path)
    try:
        if session_key:
            row = conn.execute(
                "SELECT conversation_history FROM sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT conversation_history FROM sessions ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return "(无会话，无可用消息)"
        history = json.loads(row[0] or "[]")
        if not history:
            return "(会话为空)"
        return "\n".join(_history_snippet(m) for m in history)
    finally:
        conn.close()


def build_prompt(order: str, content: str, existing_memory: str) -> str:
    """order: 'front' 指令在前, 'back' 指令在后。content 为摘要或全部消息"""
    if order == "front":
        return f"""{MEMORY_FLUSH_FROM_SUMMARY_PROMPT}

---

## 对话内容（待提取记忆）

{content}

---

## 现有记忆（请勿重复追加）

{existing_memory}"""
    else:
        return f"""## 对话内容（待提取记忆）

{content}

---

## 现有记忆（请勿重复追加）

{existing_memory}

---

{MEMORY_FLUSH_FROM_SUMMARY_PROMPT}"""


def call_via_openai(
    base_url: str,
    model: str,
    user_content: str,
    *,
    tools: list[dict] | None = None,
) -> tuple[str, float]:
    """通过 OpenAI 客户端调用 Ollama，与正式代码对齐（chat_with_tools + extra_body think=False）"""
    try:
        import httpx
        from openai import OpenAI
    except ImportError as e:
        print(f"需 pip install openai httpx: {e}", file=sys.stderr)
        sys.exit(1)

    from llm.chat import chat_with_tools

    # 不传 system：指令已在 user_content 中，system 会严重拖慢 Ollama（实测 11s vs 97s）
    messages = [{"role": "user", "content": user_content}]
    http_client = httpx.Client(trust_env=False, timeout=120.0)
    client = OpenAI(base_url=base_url, api_key="ollama", http_client=http_client)
    extra_body = {"think": False}
    t0 = time.perf_counter()
    try:
        resp = chat_with_tools(
            client,
            model,
            messages,
            tools=tools,
            extra_body=extra_body,
            temperature=0.2,
        )
        content = (resp.get("content") or "").strip()
        tool_calls = resp.get("tool_calls") or []
        if tool_calls:
            content = content or f"[{len(tool_calls)} tool_calls]"
    finally:
        http_client.close()
    elapsed = time.perf_counter() - t0
    return content, elapsed


def main():
    parser = argparse.ArgumentParser(description="对比 memory flush prompt 顺序对速度的影响")
    parser.add_argument("--order", choices=["front", "back", "both"], default="front",
                        help="front=指令在前(更快), back=指令在后, both=两种都测")
    parser.add_argument("--summary", default=None, help="覆盖：用摘要而非 DB 全部消息")
    parser.add_argument("--session-key", default=None, help="指定 session，默认取最近更新的")
    parser.add_argument("--workspace", default=None, help="workspace 路径，默认从 config 读取")
    parser.add_argument("--print-prompt", action="store_true", help="只打印 prompt，不请求")
    parser.add_argument("--no-tools", action="store_true", help="不传 tools（对比用，正式 memory_flush 会传 tools）")
    parser.add_argument("--simple-test", action="store_true", help="简单测试：只发 hi，验证连接")
    args = parser.parse_args()

    if args.simple_test:
        base_url, model, _, _ = load_config()
        print(f"简单测试: model={model} base_url={base_url} (OpenAI 客户端, 无 tools)")
        content, elapsed = call_via_openai(base_url, model, "hi", tools=None)
        print(f"耗时 {elapsed:.2f}s, 回复: {content[:200]}")
        return

    base_url, model, workspace, db_path = load_config()
    if args.workspace:
        workspace = Path(args.workspace)
        if not workspace.is_absolute():
            workspace = BASE / workspace

    # 默认：从 DB 加载全部消息（模拟 db 尚无 summary 的情况）
    content = args.summary or load_messages_from_db(db_path, args.session_key)
    existing_memory = load_memory_for_flush(workspace)

    print(f"model={model} base_url={base_url}")
    print(f"workspace={workspace} db={db_path}")
    print(f"content_len={len(content)} memory_len={len(existing_memory)}")
    print()

    from tools.memory_tool import get_tools_definitions as get_memory_tools

    tools = None if args.no_tools else get_memory_tools(workspace)
    print(f"tools: {'无' if not tools else f'{len(tools)} 个 (memory_append 等)'}")
    print()

    orders = ["front", "back"] if args.order == "both" else [args.order]
    results = []

    for order in orders:
        label = "指令在前" if order == "front" else "指令在后"
        prompt = build_prompt(order, content, existing_memory)
        print(f"[{label}] prompt 总长 {len(prompt)} 字符, 约 {len(prompt)//2} tokens")
        if args.print_prompt:
            print("\n" + "=" * 60 + f"\n[{label}] PROMPT:\n" + "=" * 60)
            print(prompt)
            print("=" * 60 + "\n")
            continue
        resp_content, elapsed = call_via_openai(base_url, model, prompt, tools=tools)
        results.append((order, elapsed, resp_content))
        print(f"[{label}] 耗时 {elapsed:.2f}s, 输出 {len(resp_content)} 字符")
        print(f"  输出预览: {resp_content[:150]}..." if len(resp_content) > 150 else f"  输出: {resp_content}")
        print()

    if args.print_prompt:
        return
    if args.order == "both" and len(results) == 2:
        t1, t2 = results[0][1], results[1][1]
        diff = t2 - t1
        faster = "指令在前" if t1 < t2 else "指令在后"
        print(f"对比: {faster} 更快，相差 {abs(diff):.2f}s")


if __name__ == "__main__":
    main()
