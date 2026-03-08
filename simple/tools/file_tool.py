"""
文件工具：read、write、edit、apply_patch
限制在 workspace 目录内操作
"""
from pathlib import Path
from typing import Optional


def _resolve_path(workspace: Optional[Path], path: str) -> Path:
    """解析路径，确保在 workspace 内"""
    p = Path(path)
    if not p.is_absolute() and workspace:
        p = (workspace / p).resolve()
    elif workspace:
        p = p.resolve()
        if not str(p).startswith(str(workspace.resolve())):
            raise PermissionError(f"路径 {path} 超出 workspace 范围")
    return p


def file_read(path: str, workspace: Optional[Path] = None, encoding: str = "utf-8") -> str:
    """读取文件内容"""
    p = _resolve_path(workspace, path)
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    if not p.is_file():
        return f"[错误] 不是文件: {path}"
    try:
        return p.read_text(encoding=encoding, errors="replace")
    except Exception as e:
        return f"[错误] 读取失败: {e}"


def file_write(path: str, content: str, workspace: Optional[Path] = None, encoding: str = "utf-8") -> str:
    """写入文件（覆盖）"""
    p = _resolve_path(workspace, path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)
        return f"已写入 {path}，共 {len(content)} 字符\n实际路径: {p.resolve()}"
    except Exception as e:
        return f"[错误] 写入失败: {e}"


def file_edit(path: str, old_text: str, new_text: str, workspace: Optional[Path] = None) -> str:
    """编辑文件：将 old_text 替换为 new_text（首次匹配）"""
    p = _resolve_path(workspace, path)
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            return f"[错误] 未找到要替换的文本"
        content = content.replace(old_text, new_text, 1)
        p.write_text(content, encoding="utf-8")
        return f"已替换 1 处"
    except Exception as e:
        return f"[错误] 编辑失败: {e}"


def file_apply_patch(path: str, patch: str, workspace: Optional[Path] = None) -> str:
    """
    应用 Unified Diff 格式补丁。使用系统 patch 命令（若可用），否则回退到简单替换。
    """
    p = _resolve_path(workspace, path)
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    try:
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, encoding="utf-8") as f:
            f.write(patch)
            patch_path = f.name
        try:
            r = subprocess.run(
                ["patch", "-p1", "-f", "-i", patch_path, str(p)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(p.parent),
            )
            if r.returncode == 0:
                return "已应用补丁"
            return f"[错误] patch 失败: {r.stderr or r.stdout}"
        except FileNotFoundError:
            pass
        finally:
            Path(patch_path).unlink(missing_ok=True)
        # 无 patch 命令：简单逐行替换
        return _apply_patch_simple(p, patch)
    except Exception as e:
        return f"[错误] 应用补丁失败: {e}"


def _apply_patch_simple(p: Path, patch: str) -> str:
    """无 patch 命令时：提示使用 file_edit"""
    return "[提示] 系统无 patch 命令，请使用 file_edit 进行逐处替换，或安装 patch 后重试"


def get_tools_definitions(workspace: Optional[Path] = None) -> list[dict]:
    wp = str(workspace) if workspace else "当前目录"
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": f"读取文件内容。路径限制在 workspace ({wp}) 内。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": f"写入文件（覆盖）。path 填 workspace 内相对路径如 myfile.txt，文件会保存在 {wp}。不要用 exec 创建文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "workspace 内相对路径，如 myfile.txt 或 subdir/file.txt"},
                        "content": {"type": "string", "description": "要写入的内容"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_edit",
                "description": "编辑文件：将文件中首次出现的 old_text 替换为 new_text。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "old_text": {"type": "string", "description": "要替换的原文"},
                        "new_text": {"type": "string", "description": "新内容"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_apply_patch",
                "description": "对文件应用 Unified Diff 格式补丁。适用于多行修改。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "patch": {"type": "string", "description": "Unified Diff 格式的补丁文本"},
                    },
                    "required": ["path", "patch"],
                },
            },
        },
    ]


def _make_executor(workspace: Optional[Path]):
    def _pop_extra(kw):
        kw.pop("timeout_sec", None)

    def _read(path: str, **kw):
        _pop_extra(kw)
        return file_read(path, workspace=workspace, **kw)

    def _write(path: str, content: str, **kw):
        _pop_extra(kw)
        return file_write(path, content, workspace=workspace, **kw)

    def _edit(path: str, old_text: str, new_text: str, **kw):
        _pop_extra(kw)
        return file_edit(path, old_text, new_text, workspace=workspace, **kw)

    def _patch(path: str, patch: str, **kw):
        _pop_extra(kw)
        return file_apply_patch(path, patch, workspace=workspace, **kw)

    return {
        "file_read": _read,
        "file_write": _write,
        "file_edit": _edit,
        "file_apply_patch": _patch,
    }


# 默认无 workspace 时使用当前目录
TOOL_EXECUTORS = _make_executor(None)


def _make_executors(workspace: Optional[Path]) -> dict:
    return _make_executor(workspace)
