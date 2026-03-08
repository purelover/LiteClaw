"""
豆包大模型客户端（火山引擎 ARK API，兼容 OpenAI 格式）
"""
import os
import httpx
from openai import OpenAI


def create_client(api_key: str | None = None, base_url: str = "https://ark.cn-beijing.volces.com/api/v3"):
    """创建豆包客户端
    使用 trust_env=False 避免 httpx 使用 HTTP_PROXY/HTTPS_PROXY，直连火山引擎 API
    """
    key = api_key or os.environ.get("ARK_API_KEY") or os.environ.get("DOUBAO_API_KEY")
    if not key:
        raise ValueError("需要 ARK_API_KEY 或 doubao.api_key")
    http_client = httpx.Client(trust_env=False)
    return OpenAI(api_key=key, base_url=base_url, http_client=http_client)


def chat(
    client: OpenAI,
    endpoint_id: str,
    messages: list[dict],
    stream: bool = False,
) -> str:
    """调用豆包对话"""
    resp = client.chat.completions.create(
        model=endpoint_id,
        messages=messages,
        stream=stream,
    )
    if stream:
        return "".join(chunk.choices[0].delta.content or "" for chunk in resp)
    return resp.choices[0].message.content or ""
