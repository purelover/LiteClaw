"""
AgentSkills 格式 Skill 加载器
兼容 OpenClaw / agentskills.io 的 SKILL.md 格式
"""
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from util.log import log


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (frontmatter_dict, body)"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        return {}, content
    fm_str, body = match.group(1), match.group(2)
    try:
        import yaml
        fm = yaml.safe_load(fm_str) or {}
    except Exception:
        fm = {}
    return fm, body.strip()


def _check_requires(requires: dict, base_dir: Path) -> bool:
    """
    检查 OpenClaw metadata.openclaw.requires 是否满足。
    requires: { bins: [...], env: [...], config: [...] }
    返回 True 表示可加载，False 表示跳过。
    """
    if not requires:
        return True
    bins = requires.get("bins") or []
    env_vars = requires.get("env") or []
    for b in bins:
        if not shutil.which(b):
            log("skills", "skill 跳过: 缺少 bin %s", b)
            return False
    for e in env_vars:
        if not os.environ.get(e):
            log("skills", "skill 跳过: 缺少 env %s", e)
            return False
    # config 检查较复杂，暂不实现
    return True


def load_skill(skill_dir: Path, check_requires: bool = True) -> Optional[dict]:
    """
    加载单个 skill 目录。
    返回 {"name": str, "description": str, "body": str} 或 None。
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists() or not skill_md.is_file():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log("skills", "读取 SKILL.md 失败 %s: %s", skill_dir, e)
        return None
    fm, body = _parse_frontmatter(content)
    name = fm.get("name") or skill_dir.name
    description = fm.get("description") or ""
    if not description:
        log("skills", "skill %s 缺少 description，跳过", name)
        return None
    if check_requires:
        openclaw = (fm.get("metadata") or {}).get("openclaw") or {}
        requires = openclaw.get("requires") or {}
        if not _check_requires(requires, skill_dir):
            return None
    return {
        "name": name,
        "description": description,
        "body": body,
    }


def load_skills(
    skill_dirs: list[str | Path],
    base_dir: Path | None = None,
    entries: dict | None = None,
    only: list[str] | None = None,
    check_requires: bool = True,
) -> list[dict]:
    """
    从多个目录加载 skills。
    skill_dirs: 目录列表，如 ["skills", "~/openclaw-skills"]
    base_dir: 相对路径的基准
    entries: 可选，skills.entries 配置，用于启用/禁用，如 {"pdf": {"enabled": true}}
    only: 可选白名单，如 ["github","summarize"]，仅加载这些 skill，避免 prompt 过长
    check_requires: 是否检查 metadata.openclaw.requires（bins/env）
    返回 [{"name", "description", "body"}, ...]
    """
    base = Path(base_dir) if base_dir else Path.cwd()
    entries = entries or {}
    only_set = set(only) if only else None
    result = []
    seen = set()
    for i, d in enumerate(skill_dirs):
        p = Path(d)
        if not p.is_absolute():
            p = base / p
        p = p.expanduser().resolve()
        if not p.is_dir():
            log("skills", "skill 目录不存在: %s", p)
            continue
        # 第一个目录（本地 skills）始终全量加载，only 仅过滤后续目录（如 openclaw）
        apply_only = only_set is not None and i > 0
        for sub in sorted(p.iterdir()):
            if not sub.is_dir():
                continue
            skill = load_skill(sub, check_requires=check_requires)
            if not skill:
                continue
            name = skill["name"]
            if name in seen:
                continue
            if apply_only and name not in only_set:
                continue
            entry = entries.get(name, {})
            if isinstance(entry, dict) and entry.get("enabled") is False:
                log("skills", "skill %s 已禁用", name)
                continue
            seen.add(name)
            result.append(skill)
    log("skills", "已加载 %d 个 skills: %s", len(result), [s["name"] for s in result])
    # 供 skill_read 工具按需拉取 body
    _LOADED_SKILLS.clear()
    _LOADED_SKILLS.update({s["name"]: s for s in result})
    return result


_LOADED_SKILLS: dict[str, dict] = {}


def get_skill_body(name: str) -> str | None:
    """按 name 获取 skill 的完整 body，供 skill_read 工具使用。未找到返回 None。"""
    s = _LOADED_SKILLS.get(name)
    if not s:
        return None
    return s.get("body") or ""


def build_skills_prompt(skills: list[dict], mode: str = "full") -> str:
    """
    将 skills 构建为 system prompt 片段。
    mode: full=注入 name+description+body；metadata_only=仅 name+description，body 通过 skill_read 按需拉取
    """
    if not skills:
        return ""
    names = [s["name"] for s in skills]
    base = (
        "## Skills（已加载能力）\n"
        f"你已具备以下 skills：{', '.join(names)}。用户询问技能时，应说明你已加载的 skills，可从 OpenClaw 社区获取更多 skills 放入 skills 目录。不要声称「无法安装技能」或「无法修改底层技能系统」。"
    )
    if mode == "metadata_only":
        base += "\n\n需要某 skill 的详细说明时，可调用 skill_read(skill_name) 获取完整内容。"
    parts = [base, ""]
    for s in skills:
        parts.append(f"### {s['name']}\n{s['description']}\n")
        if mode == "full" and s.get("body"):
            parts.append(s["body"])
        parts.append("")
    return "\n".join(parts).strip()
