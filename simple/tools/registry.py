"""
工具注册表：插件式扩展，支持内置工具与外部插件
"""
from pathlib import Path
from typing import Callable

# 全局注册表
_TOOL_DEFINITIONS: list[dict] = []
_TOOL_EXECUTORS: dict[str, Callable] = {}


def register_tool(definition: dict, executor: Callable):
    """注册单个工具"""
    name = definition.get("function", {}).get("name") or definition.get("name")
    if not name:
        raise ValueError("工具定义必须包含 name 或 function.name")
    _TOOL_DEFINITIONS.append(definition)
    _TOOL_EXECUTORS[name] = executor


def register_tools(definitions: list[dict], executors: dict[str, Callable]):
    """批量注册工具"""
    for d in definitions:
        name = d.get("function", {}).get("name") or d.get("name")
        if name and name in executors:
            register_tool(d, executors[name])


def get_definitions() -> list[dict]:
    """返回 OpenAI 格式的 tools 定义"""
    return [d for d in _TOOL_DEFINITIONS if "function" in d]


def get_executors() -> dict[str, Callable]:
    """返回工具名 -> 执行函数"""
    return dict(_TOOL_EXECUTORS)


def clear():
    """清空注册表（测试用）"""
    _TOOL_DEFINITIONS.clear()
    _TOOL_EXECUTORS.clear()


def load_builtin_tools(
    exec_timeout: int = 30,
    workspace: str | Path | None = None,
    memory_workspace: str | Path | None = None,
    tools_config: dict | None = None,
):
    """
    加载内置工具。tools_config 控制各模块启用：
    exec, file, browser, system, automation
    """
    clear()
    cfg = tools_config or {}
    exec_enabled = cfg.get("exec", {}).get("enabled", True)
    file_enabled = cfg.get("file", {}).get("enabled", False)
    browser_enabled = cfg.get("browser", {}).get("enabled", False)
    system_enabled = cfg.get("system", {}).get("enabled", False)
    automation_enabled = cfg.get("automation", {}).get("enabled", False)
    memory_enabled = cfg.get("memory", {}).get("enabled", False)
    im_enabled = cfg.get("im", {}).get("enabled", False)
    search_enabled = cfg.get("search", {}).get("enabled", False)

    if exec_enabled:
        from .exec_tool import get_tools_definitions, _make_executors as make_exec_exec
        defs = get_tools_definitions(exec_timeout)
        exec_executors = make_exec_exec(workspace)
        executors = {k: _wrap_exec(v, exec_timeout) for k, v in exec_executors.items()}
        register_tools(defs, executors)

    if file_enabled:
        from .file_tool import get_tools_definitions as get_file_defs, _make_executors as make_file_exec
        wp = Path(workspace) if workspace else None
        defs = get_file_defs(wp)
        register_tools(defs, make_file_exec(wp))

    if browser_enabled:
        from .browser_tool import get_tools_definitions as get_browser_defs, _make_executors as make_browser_exec
        timeout = cfg.get("browser", {}).get("timeout_sec", 30)
        wp = Path(workspace) if workspace else None
        defs = get_browser_defs(timeout)
        register_tools(defs, make_browser_exec(workspace=wp, timeout_sec=timeout))

    if system_enabled:
        from .system_tool import get_tools_definitions as get_sys_defs, _make_executors as make_sys_exec
        defs = get_sys_defs(exec_timeout)
        register_tools(defs, make_sys_exec(exec_timeout))

    if automation_enabled:
        from .automation_tool import get_tools_definitions as get_auto_defs, TOOL_EXECUTORS as auto_exec
        defs = get_auto_defs()
        register_tools(defs, auto_exec)

    if memory_enabled:
        from .memory_tool import get_tools_definitions as get_mem_defs, _make_executors as make_mem_exec
        wp = Path(memory_workspace) if memory_workspace else Path(workspace) if workspace else None
        defs = get_mem_defs(wp)
        register_tools(defs, make_mem_exec(wp))

    if im_enabled:
        from .im_tool import get_tools_definitions as get_im_defs, _make_executors as make_im_exec
        wp = Path(workspace) if workspace else None
        defs = get_im_defs(wp)
        register_tools(defs, make_im_exec(wp))

    if search_enabled:
        from .search_tool import get_tools_definitions as get_search_defs, _make_executors as make_search_exec
        api_key = cfg.get("search", {}).get("api_key")
        defs = get_search_defs()
        register_tools(defs, make_search_exec(api_key))


def load_plugins(plugin_dirs: list[str | Path] | None = None, base_dir: Path | None = None):
    """
    从插件目录加载工具。插件目录下需有 tools.py 或 tools/__init__.py，
    并定义 register(register_tool_fn) 函数。
    base_dir: 插件路径的基准目录，默认为当前工作目录。
    """
    if not plugin_dirs:
        return
    base = Path(base_dir) if base_dir else Path.cwd()
    for d in plugin_dirs:
        p = base / d if not Path(d).is_absolute() else Path(d)
        if not p.is_dir():
            continue
        # 支持 tools.py 或 tools/ 包
        mod = None
        if (p / "tools.py").exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("plugin_tools", p / "tools.py")
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        elif (p / "tools" / "__init__.py").exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("plugin_tools", p / "tools" / "__init__.py")
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        if mod and hasattr(mod, "register"):
            def _reg(defn, exec_fn):
                register_tool(defn, exec_fn)
            mod.register(_reg)


def _wrap_exec(fn, timeout: int):
    """exec 工具：取 config 与模型传入的较大值，避免模型传过小导致超时"""

    def _wrapped(**kw):
        val = kw.get("timeout_sec")
        model_val = int(float(val)) if val is not None else timeout
        kw["timeout_sec"] = max(model_val, timeout)
        return fn(**kw)
    return _wrapped
