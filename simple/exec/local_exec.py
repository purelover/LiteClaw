"""
本地执行：将 LLM 生成的代码写入临时文件，在本机直接运行
"""
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import NamedTuple


class ExecResult(NamedTuple):
    stdout: str
    stderr: str
    return_code: int
    success: bool


def run_locally(
    code: str,
    lang: str,
    *,
    timeout_sec: int | float = 30,
) -> ExecResult:
    """
    在本机直接执行代码。

    - code: 要执行的代码
    - lang: 语言标识，如 python、bash
    - timeout_sec: 执行超时（秒）

    返回 ExecResult(stdout, stderr, return_code, success)
    """
    timeout_sec = int(float(timeout_sec)) if timeout_sec is not None else 30
    suffix = ".py" if lang in ("python", "py") else ".sh"
    temp_name = f"exec_{uuid.uuid4().hex[:12]}{suffix}"

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(code)
        script_path = f.name

    try:
        if lang in ("python", "py"):
            cmd = [sys.executable, script_path]
        else:
            cmd = ["bash", script_path]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )

        return ExecResult(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            return_code=proc.returncode or 0,
            success=proc.returncode == 0,
        )
    except subprocess.TimeoutExpired:
        return ExecResult(
            stdout="",
            stderr=f"执行超时（{timeout_sec} 秒）",
            return_code=-1,
            success=False,
        )
    except FileNotFoundError as e:
        return ExecResult(
            stdout="",
            stderr=f"未找到解释器: {e}",
            return_code=-1,
            success=False,
        )
    except Exception as e:
        return ExecResult(
            stdout="",
            stderr=f"执行失败: {e}",
            return_code=-1,
            success=False,
        )
    finally:
        try:
            Path(script_path).unlink(missing_ok=True)
        except OSError:
            pass
