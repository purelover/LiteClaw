"""
Skill 工具：skill_read
在 metadata_only 模式下，供模型按需获取 skill 的完整说明。
"""
from tools.registry import register_tool


def get_skill_read_definition() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "skill_read",
            "description": "读取指定 skill 的完整说明（SKILL.md body）。当需要执行某 skill 但不清楚具体步骤时调用。仅能读取已加载的 skills。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "skill 名称，如 skill-creator、example"},
                },
                "required": ["skill_name"],
            },
        },
    }


def _skill_read_impl(skill_name: str) -> str:
    from skills.loader import get_skill_body
    body = get_skill_body(skill_name.strip())
    if body is None:
        return "[错误] 未找到该 skill，请确认名称正确。已加载的 skills 见 system prompt。"
    if not body:
        return "[该 skill 无详细说明]"
    return body


def register_skill_read_tool():
    """注册 skill_read 工具，在 skills 启用且 mode=metadata_only 时调用"""
    def exec_fn(skill_name: str, **kw):
        return _skill_read_impl(skill_name)
    register_tool(get_skill_read_definition(), exec_fn)
