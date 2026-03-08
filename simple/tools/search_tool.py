"""
基于 Serper 的网络搜索工具
API 文档: https://serper.dev
响应格式: organic, knowledgeGraph, answerBox, search_info 等
"""
import os
from typing import Optional


def _format_serper_response(data: dict) -> str:
    """将 Serper API 返回的 JSON 格式化为可读文本"""
    parts = []

    # answerBox: 直接答案（如定义、计算器等）
    answer = data.get("answerBox")
    if answer:
        if isinstance(answer, str):
            parts.append(f"【直接答案】\n{answer}")
        elif isinstance(answer, dict):
            title = answer.get("title", "")
            answer_text = answer.get("answer") or answer.get("snippet", "")
            if title or answer_text:
                parts.append(f"【直接答案】\n{title}\n{answer_text}")

    # knowledgeGraph: 知识图谱
    kg = data.get("knowledgeGraph")
    if kg:
        title = kg.get("title", "")
        kg_type = kg.get("type", "")
        desc = kg.get("description", "")
        website = kg.get("website", "")
        attrs = kg.get("attributes", {})
        lines = [f"【知识图谱】{title}"]
        if kg_type:
            lines.append(f"类型: {kg_type}")
        if desc:
            lines.append(f"描述: {desc}")
        if website:
            lines.append(f"官网: {website}")
        if isinstance(attrs, dict) and attrs:
            for k, v in attrs.items():
                if v:
                    lines.append(f"{k}: {v}")
        parts.append("\n".join(lines))

    # organic: 有机搜索结果
    organic = data.get("organic", [])
    if organic:
        lines = ["【搜索结果】"]
        for i, item in enumerate(organic[:10], 1):
            title = item.get("title", "(无标题)")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            lines.append(f"{i}. {title}")
            if link:
                lines.append(f"   {link}")
            if snippet:
                lines.append(f"   {snippet}")
            lines.append("")
        parts.append("\n".join(lines).rstrip())

    # search_info: 搜索元信息
    info = data.get("search_info")
    if info:
        total = info.get("totalResultsCount") or info.get("total_results_count")
        time_taken = info.get("timeTakenDisplayed") or info.get("time_taken_displayed")
        if total is not None or time_taken is not None:
            meta = []
            if total is not None:
                meta.append(f"约 {total} 条结果")
            if time_taken is not None:
                meta.append(f"耗时 {time_taken}")
            parts.append(f"【元信息】{', '.join(meta)}")

    if not parts:
        return "未找到相关结果。"

    return "\n\n".join(parts)


def serper_search(query: str, api_key: Optional[str] = None) -> str:
    """
    使用 Serper API 进行网络搜索。
    api_key 可从 tools.search.api_key 或环境变量 SERPER_API_KEY 获取。
    """
    key = api_key or os.environ.get("SERPER_API_KEY")
    if not key:
        return "[错误] 未配置 Serper API Key，请在 config.yaml 的 tools.search.api_key 或环境变量 SERPER_API_KEY 中设置。"

    try:
        import requests
    except ImportError:
        return "[错误] 未安装 requests，请执行: pip install requests"

    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": key,
        "Content-Type": "application/json",
    }
    payload = {"q": query.strip()}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return _format_serper_response(data)
    except requests.exceptions.Timeout:
        return "[错误] 搜索请求超时"
    except requests.exceptions.HTTPError as e:
        if e.response is not None:
            try:
                err_body = e.response.json()
                msg = err_body.get("message", err_body.get("error", str(e)))
            except Exception:
                msg = e.response.text or str(e)
        else:
            msg = str(e)
        return f"[错误] 搜索请求失败: {msg}"
    except Exception as e:
        return f"[错误] 搜索失败: {e}"


def get_tools_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "serper_search",
                "description": "使用 Serper API 进行网络搜索，返回 Google 搜索结果（有机结果、知识图谱、直接答案等）。适用于查询实时信息、事实、新闻等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词或完整问题"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def _make_executors(api_key: Optional[str] = None) -> dict:
    key = api_key or os.environ.get("SERPER_API_KEY")

    def _search(**kw):
        return serper_search(kw.get("query", ""), api_key=key)

    return {"serper_search": _search}
