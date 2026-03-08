"""
记忆工具：memory_get、memory_search、memory_append
对齐 OpenClaw 的 memory 能力
"""
from pathlib import Path
from typing import Optional

from storage.workspace import memory_get as _memory_get
from storage.workspace import memory_append as _memory_append
from storage.workspace import memory_search as _memory_search


def get_tools_definitions(workspace: Path | None = None) -> list[dict]:
    wp = str(workspace) if workspace else "data/workspace"
    return [
        {
            "type": "function",
            "function": {
                "name": "memory_get",
                "description": f"读取工作区记忆文件。路径如 MEMORY.md、memory/2025-03-06.md。工作区: {wp}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "工作区相对路径"},
                        "lines": {"type": "integer", "description": "可选，只读前 N 行"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "在 MEMORY.md、TODO.md、NOTES.md 和 memory/*.md 中搜索关键词。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "history_search",
                "description": "在本次对话的原始历史中搜索关键词，返回匹配的摘录。适用于用户说「咱们之前聊过xxx，翻一下当时的结论」「结合之前的讨论来回答」等场景。与 memory_search 不同：history_search 查的是原始对话记录，memory_search 查的是精炼后的记忆文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词，如话题、结论、人名等"},
                        "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_append",
                "description": "追加内容到记忆文件。path 选择：memory/YYYY-MM-DD.md=当日摘要、用户说「记住」的默认目标；MEMORY.md=长期偏好、关键决策；TODO.md=任务列表（长任务时更新，会放 context 末尾引导注意力）；NOTES.md=结构化笔记。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "MEMORY.md、TODO.md、NOTES.md 或 memory/2025-03-06.md"},
                        "content": {"type": "string", "description": "要追加的内容"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]


def _make_executors(workspace: Path | None) -> dict:
    def get(path: str, lines: Optional[int] = None, **kw):
        return _memory_get(path, workspace=workspace, lines=lines)

    def search(query: str, limit: int = 5, **kw):
        return _memory_search(query, workspace=workspace, limit=limit)

    def history_search(query: str, limit: int = 5, **kw):
        from util.channel_context import get_session_key
        from storage.db import get_storage
        sk = get_session_key()
        if not sk:
            return "(无当前会话，无法搜索历史)"
        return get_storage().search_conversation_history(sk, query, limit=limit)

    def append(path: str, content: str, **kw):
        return _memory_append(path, content, workspace=workspace)

    return {
        "memory_get": get,
        "memory_search": search,
        "history_search": history_search,
        "memory_append": append,
    }
