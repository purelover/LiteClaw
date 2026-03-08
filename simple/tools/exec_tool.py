"""
Exec 工具：供 Agent 调用的代码执行
"""
from exec.local_exec import run_locally


def exec_python(code: str, timeout_sec: int = 30) -> str:
    """执行 Python 代码"""
    r = run_locally(code, "python", timeout_sec=timeout_sec)
    if r.success:
        return r.stdout if r.stdout else "(无输出)"
    return f"[错误] {r.stderr}\n退出码: {r.return_code}"


def exec_bash(code: str = "", command: str = "", timeout_sec: int = 30) -> str:
    """执行 Bash 脚本。参数 code 或 command 均可（兼容模型传 command）"""
    script = (code or command or "").strip()
    r = run_locally(script, "bash", timeout_sec=timeout_sec)
    if r.success:
        return r.stdout if r.stdout else "(无输出)"
    return f"[错误] {r.stderr}\n退出码: {r.return_code}"


def get_tools_definitions(timeout_sec: int = 30) -> list[dict]:
    """返回 OpenAI 格式的 tools 定义"""
    return [
        {
            "type": "function",
            "function": {
                "name": "exec_python",
                "description": "在本机执行 Python 代码，返回 stdout 和 stderr。仅用于真正的 Python 脚本。file_write、file_read 等是工具，应通过工具调用使用，不要写 file_write(...) 用 exec_python 执行（会报 NameError）。创建文件用 file_write 工具。**必须使用 ASCII 标点**：冒号用 : 不能 ：，方括号用 [] 不能 【】。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "要执行的 Python 代码"},
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "exec_bash",
                "description": "在本机执行 Bash 脚本，返回 stdout 和 stderr。仅用于真正的 shell 命令。file_write、browser_screenshot 等是工具，应通过工具调用使用，不要用 exec_bash 执行工具名。创建文件用 file_write 工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "要执行的 Bash 脚本（与 command 二选一）"},
                        "command": {"type": "string", "description": "要执行的 Bash 脚本（与 code 二选一）"},
                    },
                },
            },
        },
    ]


def _coerce_timeout(kw: dict, default: int = 30) -> int:
    v = kw.get("timeout_sec", default)
    try:
        return int(float(v)) if v is not None else default
    except (TypeError, ValueError):
        return default


def _exec_bash_kw(kw: dict) -> str:
    return (kw.get("code") or kw.get("command") or "").strip()


TOOL_EXECUTORS = {
    "exec_python": lambda **kw: exec_python(kw.get("code", ""), timeout_sec=_coerce_timeout(kw)),
    "exec_bash": lambda **kw: exec_bash(code=_exec_bash_kw(kw), timeout_sec=_coerce_timeout(kw)),
}
