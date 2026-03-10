"""
本地大模型客户端（Ollama，兼容 OpenAI 格式 v1/chat/completions）
"""
import httpx
from openai import OpenAI

from util.log import log
from llm.stats import record as record_llm_stats


def create_client(base_url: str = "http://localhost:11434/v1"):
    """创建 Ollama 客户端（无需 api_key）
    使用 trust_env=False 避免 httpx 使用 HTTP_PROXY/HTTPS_PROXY，确保直连本地 Ollama
    """
    http_client = httpx.Client(trust_env=False)
    return OpenAI(base_url=base_url, api_key="ollama", http_client=http_client)


def chat(
    client: OpenAI,
    model: str,
    messages: list[dict],
    stream: bool = False,
    enable_thinking: bool = False,
    temperature: float | None = None,
) -> str:
    """调用本地模型对话。enable_thinking=False 关闭 thinking 以提速（Qwen/DeepSeek 等）"""
    extra = {"stream": stream}  # 默认 False，确保结果合并返回
    if not enable_thinking:
        extra["think"] = False
    kwargs = {"model": model, "messages": messages, "stream": stream}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if extra:
        kwargs["extra_body"] = extra
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        log("ollama", "chat 请求失败: %s", type(e).__name__)
        log("ollama", "错误详情: %s", e)
        log("ollama", "base_url=%r", getattr(client, "base_url", "?"))
        raise
    usage = getattr(resp, "usage", None)
    if usage:
        record_llm_stats(
            model,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )
    else:
        record_llm_stats(model, 0, 0)
    if stream:
        return "".join(chunk.choices[0].delta.content or "" for chunk in resp)
    return resp.choices[0].message.content or ""
