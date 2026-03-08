"""
示例插件：演示如何扩展 LiteClaw 工具
在 config.yaml 中设置 tools.plugins: ["plugins/example"] 启用
"""


def register(register_tool_fn):
    """插件入口：注册工具定义与执行函数"""

    def echo_plugin(**kw) -> str:
        """回显文本，用于测试插件加载"""
        return f"[plugin] echo: {kw.get('text', '')}"

    register_tool_fn(
        {
            "type": "function",
            "function": {
                "name": "echo_plugin",
                "description": "示例插件工具：回显输入的文本，用于验证插件系统。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要回显的文本"},
                    },
                    "required": ["text"],
                },
            },
        },
        echo_plugin,
    )
