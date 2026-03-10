"""
系统工具：进程列表、系统命令执行
"""
import platform
import subprocess
from typing import Optional


def process_list(limit: int = 20, filter_name: Optional[str] = None) -> str:
    """列出当前进程，返回 PID、名称、CPU、内存等"""
    try:
        import psutil
    except ImportError:
        return _process_list_fallback(limit, filter_name)

    lines = ["PID\tNAME\tCPU%\tMEM%\tSTATUS"]
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = p.info
            name = (info.get("name") or "?")[:30]
            if filter_name and filter_name.lower() not in name.lower():
                continue
            cpu = info.get("cpu_percent") or 0
            mem = info.get("memory_percent") or 0
            status = (info.get("status") or "?")[:10]
            lines.append(f"{info.get('pid', '?')}\t{name}\t{cpu:.1f}\t{mem:.1f}\t{status}")
            if len(lines) > limit + 1:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return "\n".join(lines[: limit + 1])


def _process_list_fallback(limit: int, filter_name: Optional[str]) -> str:
    """无 psutil 时使用 ps 命令"""
    try:
        cmd = ["ps", "-eo", "pid,comm,%cpu,%mem,state", "--no-headers"]
        if platform.system() == "Windows":
            cmd = ["tasklist", "/FO", "CSV", "/NH"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            lines = r.stdout.strip().split("\n")[:limit]
            return "PID\tNAME\tCPU%\tMEM%\tSTATUS\n" + "\n".join(lines) if lines else "(无输出)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "[提示] 安装 psutil 可获取更完整进程信息: pip install psutil"


def exec_command(command: str, timeout_sec: int = 30, shell: bool = True) -> str:
    """执行系统命令，返回 stdout 和 stderr"""
    try:
        r = subprocess.run(
            command if shell else command.split(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
            shell=shell,
        )
        out = r.stdout.strip() if r.stdout else ""
        err = r.stderr.strip() if r.stderr else ""
        # 判定失败：退出码非 0，或 stderr 含 Traceback（如 python 脚本异常）
        is_fail = r.returncode != 0 or "Traceback" in err
        body = f"[stdout]\n{out}\n[stderr]\n{err}\n退出码: {r.returncode}" if err else (out or "(无输出)")
        if is_fail:
            return f"[错误] {body}"
        return body
    except subprocess.TimeoutExpired:
        return f"[错误] 执行超时（{timeout_sec} 秒）"
    except Exception as e:
        return f"[错误] 执行失败: {e}"


def get_tools_definitions(timeout_sec: int = 30) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "process_list",
                "description": "列出系统进程，包含 PID、名称、CPU、内存占用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "最多返回进程数", "default": 20},
                        "filter_name": {"type": "string", "description": "按进程名过滤"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "exec_command",
                "description": "执行系统命令（Shell），返回 stdout 和 stderr。慎用，仅限可信环境。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的命令"},
                        "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 30},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def _make_executors(timeout_sec: int = 30) -> dict:
    """创建系统工具执行器，exec_command 取 config 与模型传入的较大值"""

    def _effective_timeout(kw: dict) -> int:
        val = kw.get("timeout_sec")
        model_val = int(float(val)) if val is not None else timeout_sec
        return max(model_val, timeout_sec)

    return {
        "process_list": lambda **kw: process_list(
            limit=kw.get("limit", 20),
            filter_name=kw.get("filter_name"),
        ),
        "exec_command": lambda **kw: exec_command(
            kw.get("command", ""),
            timeout_sec=_effective_timeout(kw),
        ),
    }


TOOL_EXECUTORS = _make_executors(30)  # 兼容直接 import
