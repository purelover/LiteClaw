"""
工作区记忆：对齐 OpenClaw 的 Markdown 文件存储
- AGENTS.md: 顶层指令
- SOUL.md: 人格与价值观
- USER.md: 用户偏好
- MEMORY.md: 长期记忆
- memory/YYYY-MM-DD.md: 每日日志（仅追加）
- TODO.md: 任务列表（Recitation，长任务时放 context 末尾引导注意力）
- NOTES.md: 结构化笔记（Anthropic structured note-taking）
"""
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def get_workspace_path(base: Path | str) -> Path:
    """解析工作区路径"""
    p = Path(base)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p.resolve()


def ensure_workspace(workspace: Path) -> Path:
    """确保工作区目录及 memory 子目录存在，并创建 TODO.md、NOTES.md 占位（若不存在）"""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "memory").mkdir(exist_ok=True)
    for name in ("TODO.md", "NOTES.md"):
        p = workspace / name
        if not p.exists():
            p.write_text(f"# {name[:-3]}\n\n", encoding="utf-8")
    return workspace


def _read_file_safe(p: Path, encoding: str = "utf-8") -> str:
    if not p.exists() or not p.is_file():
        return ""
    try:
        return p.read_text(encoding=encoding, errors="replace").strip()
    except OSError:
        return ""


def load_workspace_prompt(workspace: Path) -> str:
    """
    加载工作区文件构建 system prompt 前缀。
    顺序：AGENTS.md + SOUL.md + USER.md
    """
    parts = []
    for name in ("AGENTS.md", "SOUL.md", "USER.md"):
        content = _read_file_safe(workspace / name)
        if content:
            parts.append(f"## {name}\n{content}")
    return "\n\n".join(parts) if parts else ""


def load_memory_long(workspace: Path, max_lines: int = 200) -> str:
    """加载 MEMORY.md（长期记忆），仅主私聊会话使用。默认前 200 行（Anthropic），完整内容可 memory_get"""
    content = _read_file_safe(workspace / "MEMORY.md")
    if max_lines > 0 and content:
        lines = content.splitlines()
        if len(lines) > max_lines:
            content = "\n".join(lines[:max_lines]) + "\n\n[... 后续内容可 memory_get('MEMORY.md') 读取]"
    return content


def load_memory_daily(workspace: Path, days: int = 2) -> str:
    """
    加载最近 N 天的每日记忆（memory/YYYY-MM-DD.md）。
    默认加载今天和昨天。
    """
    parts = []
    for i in range(days):
        d = datetime.now().date() - timedelta(days=i)
        path = workspace / "memory" / f"{d:%Y-%m-%d}.md"
        content = _read_file_safe(path)
        if content:
            parts.append(f"### {d:%Y-%m-%d}\n{content}")
    return "\n\n".join(parts) if parts else ""


def build_memory_context(
    workspace: Path,
    include_long: bool = True,
    daily_days: int = 2,
) -> str:
    """
    构建记忆上下文，供 system prompt 使用。
    include_long: 是否包含 MEMORY.md（主私聊为 True，群聊可为 False）
    """
    parts = []
    if include_long:
        long_mem = load_memory_long(workspace)
        if long_mem:
            parts.append("## 长期记忆 (MEMORY.md)\n" + long_mem)
    daily = load_memory_daily(workspace, days=daily_days)
    if daily:
        parts.append("## 近期记忆 (memory/)\n" + daily)
    return "\n\n".join(parts) if parts else ""


def load_recitation_context(workspace: Path) -> str:
    """
    加载 Recitation 上下文：TODO.md + NOTES.md。
    放 system 末尾，引导模型注意力到当前任务（Manus: manipulate attention through recitation）。
    """
    parts = []
    for name in ("TODO.md", "NOTES.md"):
        content = _read_file_safe(workspace / name)
        if content:
            title = "任务列表" if name == "TODO.md" else "笔记"
            parts.append(f"## {title} ({name})\n{content}")
    return "\n\n".join(parts) if parts else ""


# --- 记忆工具 ---

def memory_get(path: str, workspace: Path | None = None, lines: Optional[int] = None) -> str:
    """
    读取记忆文件。path 为工作区相对路径，如 MEMORY.md 或 memory/2025-03-06.md。
    """
    wp = workspace or Path("data/workspace")
    p = (wp / path).resolve()
    if not str(p).startswith(str(wp.resolve())):
        return "[错误] 路径超出工作区范围"
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if lines is not None and lines > 0:
            content = "\n".join(content.splitlines()[:lines])
        return content
    except OSError as e:
        return f"[错误] 读取失败: {e}"


ALLOWED_MEMORY_PATHS = ("MEMORY.md", "TODO.md", "NOTES.md")

def _is_allowed_memory_path(path: str) -> bool:
    if path in ALLOWED_MEMORY_PATHS:
        return True
    return path.startswith("memory/") and path.endswith(".md")


def memory_append(path: str, content: str, workspace: Path | None = None) -> str:
    """
    追加内容到记忆文件。支持：MEMORY.md、TODO.md、NOTES.md、memory/YYYY-MM-DD.md。
    TODO.md/NOTES.md 用于长任务的结构化笔记（Anthropic/Manus）。
    """
    wp = workspace or Path("data/workspace")
    p = (wp / path).resolve()
    if not str(p).startswith(str(wp.resolve())):
        return "[错误] 路径超出工作区范围"
    if not _is_allowed_memory_path(path):
        return "[错误] 仅允许写入 MEMORY.md、TODO.md、NOTES.md 或 memory/YYYY-MM-DD.md"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write("\n\n" + content.strip() + "\n")
        return f"已追加到 {path}"
    except OSError as e:
        return f"[错误] 写入失败: {e}"


def memory_search(query: str, workspace: Path | None = None, limit: int = 5) -> str:
    """
    在 MEMORY.md、TODO.md、NOTES.md 和 memory/*.md 中搜索。先实现关键词匹配，后续可扩展向量搜索。
    """
    wp = workspace or Path("data/workspace")
    if not wp.exists():
        return "(工作区不存在)"
    results = []
    query_lower = query.lower()
    files = [wp / "MEMORY.md", wp / "TODO.md", wp / "NOTES.md"]
    mem_dir = wp / "memory"
    if mem_dir.exists():
        files.extend(mem_dir.glob("*.md"))
    for f in files:
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = str(f.relative_to(wp))
            for i, line in enumerate(text.splitlines(), 1):
                if query_lower in line.lower():
                    results.append(f"[{rel}:{i}] {line.strip()[:200]}")
                    if len(results) >= limit:
                        break
        except OSError:
            continue
        if len(results) >= limit:
            break
    return "\n".join(results) if results else "(未找到匹配)"
