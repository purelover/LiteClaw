#!/usr/bin/env python3
"""
LiteClaw Simple - Python 精简实现
飞书 IM + 本地/豆包 LLM 协同

架构：Gateway 控制平面 + Session 管理 + Lane 队列 + Agent 工具调用 + SQLite 存储
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from util.log import log
from util.tokens import estimate_messages_tokens, estimate_tools_tokens
from util.channel_context import get_tool_failure_state

from llm.doubao import create_client as create_doubao_client, chat as doubao_chat
from llm.ollama import create_client as create_ollama_client, chat as ollama_chat
from llm.chat import chat_with_tools
from llm.hybrid_loop import run_hybrid_chat, run_hybrid_chat_with_tools
from storage.db import init_storage
from gateway.gateway import Gateway, GatewayConfig
from gateway.queue import Job
from agent.agent import Agent


def load_config():
    base = Path(__file__).parent
    for name in ("config.yaml", "config.example.yaml"):
        p = base / name
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("请复制 config.example.yaml 为 config.yaml 并填写配置")


def run():
    cfg = load_config()

    # 飞书、豆包直连，不走 proxy（lark-oapi 会读 NO_PROXY；豆包/ollama 已用 trust_env=False）
    _no = os.environ.get("NO_PROXY", "")
    for h in ("open.feishu.cn", "open.larksuite.com", "feishu.cn", "larksuite.com"):
        if h not in _no:
            _no = (_no + "," + h) if _no else h
    os.environ["NO_PROXY"] = _no

    # 存储
    storage_cfg = cfg.get("storage", {})
    db_path = storage_cfg.get("db_path", "data/liteclaw.db")
    if not Path(db_path).is_absolute():
        db_path = Path(__file__).parent / db_path
    compaction_cfg = cfg.get("compaction", {})
    max_turns = 200 if compaction_cfg.get("enabled") else 20
    init_storage(db_path, max_turns=max_turns)

    # 豆包（云端）
    db = cfg.get("doubao", {})
    api_key = db.get("api_key") or os.environ.get("ARK_API_KEY")
    endpoint_id = db.get("endpoint_id")
    cloud_chain = cfg.get("cloud_chain") or ([endpoint_id] if endpoint_id else [])
    if not api_key or not cloud_chain:
        raise ValueError("请配置 doubao.api_key 和 cloud_chain")

    doubao = create_doubao_client(api_key)
    model_id = cloud_chain[0]

    # 本地模型（Ollama）
    local_cfg = cfg.get("local", {})
    hybrid_cfg = cfg.get("hybrid_loop", {})
    use_hybrid = hybrid_cfg.get("enabled", False) and local_cfg.get("enabled", False)
    ollama_client = None
    local_model = "llama3.2"
    if use_hybrid:
        base_url = local_cfg.get("base_url", "http://localhost:11434/v1")
        log("llm", "Ollama base_url=%r model=%r", base_url, local_cfg.get("model", "llama3.2"))
        ollama_client = create_ollama_client(base_url)
        local_model = local_cfg.get("model", "llama3.2")

    # LLM 统计：注册已知模型，每次请求后打印全部（未使用的标「本次未使用」）
    from llm.stats import set_known_models
    known = ([local_model] if use_hybrid and ollama_client else []) + list(cloud_chain)
    set_known_models(known)

    # 飞书
    fs = cfg.get("feishu", {})
    app_id = fs.get("app_id") or os.environ.get("FEISHU_APP_ID")
    app_secret = fs.get("app_secret") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ValueError("请配置 feishu.app_id 和 feishu.app_secret")
    feishu_mode = fs.get("mode", "ws")  # ws=长连接, webhook=HTTP 回调
    if feishu_mode not in ("ws", "webhook"):
        feishu_mode = "ws"

    from im.feishu import create_client as create_feishu_client, send_text, send_post, is_rich_content
    feishu = create_feishu_client(app_id, app_secret)

    exec_cfg = cfg.get("exec", {})
    exec_enabled = exec_cfg.get("enabled", False)
    exec_timeout = exec_cfg.get("timeout_sec", 30)

    # 工具配置：exec + file/browser/system/automation + 插件
    tools_cfg = cfg.get("tools", {})
    tools_config = {
        "exec": {"enabled": exec_enabled, "timeout_sec": exec_timeout},
        "file": tools_cfg.get("file", {}),
        "browser": tools_cfg.get("browser", {}),
        "system": tools_cfg.get("system", {}),
        "automation": tools_cfg.get("automation", {}),
        "memory": tools_cfg.get("memory", {}),
        "im": tools_cfg.get("im", {}),
        "search": tools_cfg.get("search", {}),
    }
    # 飞书模式下默认启用 send_image、send_file 工具
    if tools_config["im"].get("enabled") is None and fs:
        tools_config["im"] = {**tools_config["im"], "enabled": True}
    workspace = tools_cfg.get("file", {}).get("workspace", "data/workspace")
    memory_workspace = tools_cfg.get("memory", {}).get("workspace") or tools_cfg.get("file", {}).get("workspace", "data/workspace")
    if not Path(workspace).is_absolute():
        workspace = Path(__file__).parent / workspace
    from tools.registry import load_builtin_tools, load_plugins
    if not Path(memory_workspace).is_absolute():
        memory_workspace = Path(__file__).parent / memory_workspace
    if tools_config["memory"].get("enabled"):
        from storage.workspace import ensure_workspace
        ensure_workspace(Path(memory_workspace))
    if exec_enabled or tools_config["file"].get("enabled"):
        from storage.workspace import ensure_workspace
        ensure_workspace(Path(workspace))
    load_builtin_tools(
        exec_timeout=exec_timeout,
        workspace=workspace,
        memory_workspace=memory_workspace,
        tools_config=tools_config,
    )
    load_plugins(tools_cfg.get("plugins"), base_dir=Path(__file__).parent)

    # 定时提醒持久化：注入 feishu 并恢复未触发的提醒（进程重启后自动恢复）
    from tools.reminder_scheduler import set_feishu_client, start_scheduler
    set_feishu_client(feishu)
    start_scheduler()

    # Skills（AgentSkills 格式，兼容 OpenClaw）
    skills_cfg = cfg.get("skills", {})
    skills_dirs = skills_cfg.get("load", []) or []
    skills_entries = skills_cfg.get("entries", {}) or {}
    skills_only = skills_cfg.get("only")  # 白名单，如 ["github","summarize"]，避免 127+ skills 撑爆 prompt
    skills_mode = skills_cfg.get("mode", "full")  # full=全量注入 | metadata_only=仅元数据，body 通过 skill_read 按需拉取
    check_requires = skills_cfg.get("check_requires", True)
    skills_prompt = ""
    if skills_dirs:
        from skills.loader import load_skills, build_skills_prompt
        loaded = load_skills(
            skills_dirs,
            base_dir=Path(__file__).parent,
            entries=skills_entries,
            only=skills_only,
            check_requires=check_requires,
        )
        skills_prompt = build_skills_prompt(loaded, mode=skills_mode)
        if skills_mode == "metadata_only":
            from tools.skill_tool import register_skill_read_tool
            register_skill_read_tool()

    enable_thinking = local_cfg.get("enable_thinking", False)
    agent_cfg = cfg.get("agent", {}) or {}
    temperature = agent_cfg.get("temperature")
    tool_failure_state: dict = {}  # 工具执行失败状态，供 hybrid fallback 使用

    def chat_fn(messages: list, tools: list | None) -> dict:
        """工具调用时：hybrid 用本地+fallback，否则用云端；无工具时同 hybrid_chat_fn 或豆包"""
        if tools:
            if use_hybrid and ollama_client:
                def call_local(msgs, t):
                    pt = estimate_messages_tokens(msgs) + estimate_tools_tokens(t)
                    log("llm", "chat_with_tools 本地 (Ollama) prompt_tokens≈%d...", pt)
                    extra = None if enable_thinking else {"think": False}
                    r = chat_with_tools(ollama_client, local_model, msgs, t, extra_body=extra, temperature=temperature)
                    log("llm", "chat_with_tools 本地返回, content_len=%d tool_calls=%d",
                        len(r.get("content", "") or ""), len(r.get("tool_calls") or []))
                    return r

                def call_cloud(mid, msgs, t):
                    pt = estimate_messages_tokens(msgs) + estimate_tools_tokens(t)
                    log("llm", "chat_with_tools 云端 (Doubao) model=%s prompt_tokens≈%d...", mid[:20] if mid else "", pt)
                    r = chat_with_tools(doubao, mid, msgs, t, temperature=temperature)
                    log("llm", "chat_with_tools 云端返回, content_len=%d tool_calls=%d",
                        len(r.get("content", "") or ""), len(r.get("tool_calls") or []))
                    return r

                return run_hybrid_chat_with_tools(
                    messages,
                    tools,
                    call_local=call_local,
                    call_cloud=call_cloud,
                    cloud_chain=cloud_chain,
                    cloud_index=get_tool_failure_state().get("cloud_index", -1),
                )
            pt = estimate_messages_tokens(messages) + estimate_tools_tokens(tools)
            log("llm", "chat_with_tools 云端 (Doubao) model=%s prompt_tokens≈%d...", model_id[:20] if model_id else "", pt)
            resp = chat_with_tools(doubao, model_id, messages, tools, temperature=temperature)
            log("llm", "chat_with_tools 云端返回, content_len=%d tool_calls=%d",
                len(resp.get("content", "") or ""), len(resp.get("tool_calls") or []))
            return resp
        if use_hybrid and ollama_client:
            content = hybrid_chat_fn(messages)
            return {"content": content, "tool_calls": []}
        pt = estimate_messages_tokens(messages)
        log("llm", "doubao_chat 开始 prompt_tokens≈%d...", pt)
        content = doubao_chat(doubao, model_id, messages, temperature=temperature)
        log("llm", "doubao_chat 返回, len=%d", len(content) if content else 0)
        return {"content": content, "tool_calls": []}

    def hybrid_chat_fn(messages: list) -> str:
        def call_local(msgs):
            pt = estimate_messages_tokens(msgs)
            log("llm", "call_local (Ollama) 开始 prompt_tokens≈%d...", pt)
            r = ollama_chat(ollama_client, local_model, msgs, enable_thinking=enable_thinking, temperature=temperature)
            log("llm", "call_local 返回, len=%d", len(r) if r else 0)
            return r

        def call_cloud(mid, msgs):
            pt = estimate_messages_tokens(msgs)
            log("llm", "call_cloud (Doubao) 开始 model=%s prompt_tokens≈%d...", mid[:20] if mid else "", pt)
            r = doubao_chat(doubao, mid, msgs, temperature=temperature)
            log("llm", "call_cloud 返回, len=%d", len(r) if r else 0)
            return r

        return run_hybrid_chat(
            messages,
            call_local=call_local,
            call_cloud=call_cloud,
            cloud_chain=cloud_chain,
        )

    tools_enabled = any([
        tools_config["exec"].get("enabled"),
        tools_config["file"].get("enabled"),
        tools_config["browser"].get("enabled"),
        tools_config["system"].get("enabled"),
        tools_config["automation"].get("enabled"),
        tools_config["memory"].get("enabled"),
        tools_config["im"].get("enabled"),
    ])

    max_tool_rounds = agent_cfg.get("max_tool_rounds", 10)

    agent = Agent(
        chat_fn=chat_fn,
        tools_enabled=tools_enabled,
        exec_timeout=exec_timeout,
        use_hybrid=use_hybrid,
        hybrid_chat_fn=hybrid_chat_fn if use_hybrid else None,
        workspace_path=Path(memory_workspace) if tools_cfg.get("memory", {}).get("enabled") else None,
        compaction_cfg=compaction_cfg,
        skills_prompt=skills_prompt,
        max_tool_rounds=max_tool_rounds,
        tool_failure_state=tool_failure_state if use_hybrid else None,
    )

    gw_cfg = cfg.get("gateway", {})
    gateway = Gateway(GatewayConfig(max_concurrent_lanes=gw_cfg.get("max_concurrent_lanes", 10)))

    # 注入 Gateway 供 automation 工具（gateway_status）使用
    from tools.automation_tool import set_gateway_ref
    set_gateway_ref(gateway)

    def reply_sender(channel_info: dict, text: str):
        import time
        receive_id = channel_info.get("receive_id") or channel_info.get("peer_id")
        receive_id_type = channel_info.get("receive_id_type", "open_id")
        log("reply", "reply_sender receive_id=%s type=%s text_len=%d", receive_id, receive_id_type, len(text) if text else 0)
        if not receive_id:
            return
        to_send = (text or "").strip() or "处理结果为空"
        use_post = is_rich_content(to_send)
        log("reply", "text_len=%d is_rich=%s", len(to_send), use_post)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if use_post:
                    ok = send_post(feishu, receive_id, receive_id_type, to_send)
                    log("reply", "发送成功 (%s)", "post" if ok else "post失败→文本")
                else:
                    send_text(feishu, receive_id, receive_id_type, to_send)
                    log("reply", "发送成功")
                return
            except Exception as e:
                log("reply", "发送失败 (attempt %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    import traceback
                    traceback.print_exc()

    def agent_factory(gw):
        from util.channel_context import set_channel_context, clear_channel_context

        def worker(job: Job):
            set_channel_context(
                job.channel_info, feishu, {"app_id": app_id, "app_secret": app_secret}
            )
            try:
                session = gw.session_manager.get_or_create(job.session_key, job.channel_info)
                return agent.run(session, job.message)
            finally:
                clear_channel_context()
        return worker

    gateway.set_reply_sender(reply_sender)
    gateway.set_agent_factory(agent_factory)
    gateway.start()

    def on_message(receive_id: str, message, receive_id_type: str = "open_id"):
        """message: str 或 dict {"text": str, "images": [data_url, ...]}"""
        gateway.on_inbound(
            f"feishu:{receive_id}",
            {"receive_id": receive_id, "receive_id_type": receive_id_type, "channel": "feishu"},
            message,
        )

    if feishu_mode == "ws":
        import lark_oapi as lark
        from im.feishu_ws import create_ws_client
        _level = fs.get("ws_log_level", "info")
        _log_level = getattr(lark.LogLevel, _level.upper(), lark.LogLevel.INFO)
        ws_cli = create_ws_client(app_id, app_secret, on_message, workspace_path=workspace, log_level=_log_level)
        llm_mode = "本地+云端协同" if use_hybrid else "豆包"
        log("main", "LiteClaw Simple 启动中 [Gateway + Agent + %s]，飞书长连接 (WebSocket)", llm_mode)
        log("main", "请在飞书开放平台选择「使用长连接接收事件」")
        ws_cli.start()
    else:
        from flask import Flask, request
        from lark_oapi.adapter.flask import parse_req, parse_resp
        from im.feishu import build_event_handler
        verification_token = fs.get("verification_token") or ""
        encrypt_key = fs.get("encrypt_key") or ""
        handler = build_event_handler(
            feishu, app_id, app_secret, verification_token, encrypt_key, on_message,
            workspace_path=workspace,
        )
        app = Flask(__name__)

        @app.route("/webhook/event", methods=["GET", "POST"])
        def webhook():
            return parse_resp(handler.do(parse_req(request)))

        port = fs.get("port", 9000)
        llm_mode = "本地+云端协同" if use_hybrid else "豆包"
        log("main", "LiteClaw Simple 启动中 [Gateway + Agent + %s]，飞书 Webhook: http://0.0.0.0:%d/webhook/event", llm_mode, port)
        log("main", "请在飞书开放平台将事件订阅 URL 设为: https://你的域名/webhook/event")
        app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()