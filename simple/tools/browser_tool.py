"""
无头浏览器工具：导航、截图、获取页面内容
依赖 playwright，需先执行: pip install playwright && playwright install chromium
"""
from pathlib import Path
from typing import Optional


def _resolve_output_path(workspace: Optional[Path], output_path: str) -> Path:
    """将 output_path 解析为 workspace 下的绝对路径"""
    p = Path(output_path)
    if not p.is_absolute() and workspace:
        return (workspace / p).resolve()
    return p.resolve()


def browser_navigate(url: str, timeout_sec: int = 30, wait_until: str = "domcontentloaded") -> str:
    """打开 URL 并返回页面标题与文本摘要"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[错误] 未安装 playwright，请执行: pip install playwright && playwright install chromium"

    try:
        to_ms = int(float(timeout_sec)) * 1000 if timeout_sec else 30000
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=to_ms, wait_until=wait_until)
                title = page.title()
                body = page.query_selector("body")
                text = body.inner_text()[:3000] if body else "(无内容)"
                return f"标题: {title}\n\n内容摘要:\n{text}"
            finally:
                browser.close()
    except Exception as e:
        return f"[错误] 导航失败: {e}"


def browser_screenshot(
    url: str,
    timeout_sec: int = 30,
    output_path: Optional[str] = None,
    wait_until: str = "domcontentloaded",
    workspace: Optional[Path] = None,
) -> str:
    """打开 URL 并截图。output_path 相对于 workspace 解析，wait_until=domcontentloaded 更快"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[错误] 未安装 playwright"

    try:
        to_ms = int(float(timeout_sec)) * 1000 if timeout_sec else 30000
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=to_ms, wait_until=wait_until)
                if output_path:
                    out = _resolve_output_path(workspace, output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out))
                    return f"截图已保存: {output_path}\n实际路径: {out.resolve()}"
                return "[提示] 请指定 output_path 保存截图"
            finally:
                browser.close()
    except Exception as e:
        return f"[错误] 截图失败: {e}"


def browser_content(url: str, selector: Optional[str] = None, timeout_sec: int = 30) -> str:
    """获取页面内容，可选 CSS 选择器提取特定区域"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[错误] 未安装 playwright"

    try:
        to_ms = int(float(timeout_sec)) * 1000 if timeout_sec else 30000
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=to_ms, wait_until="domcontentloaded")
                if selector:
                    el = page.query_selector(selector)
                    text = el.inner_text()[:5000] if el else "(未找到元素)"
                else:
                    body = page.query_selector("body")
                    text = body.inner_text()[:5000] if body else "(无内容)"
                return text
            finally:
                browser.close()
    except Exception as e:
        return f"[错误] 获取内容失败: {e}"


def get_tools_definitions(timeout_sec: int = 30) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "使用无头浏览器打开 URL，返回页面标题和文本内容摘要。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要访问的 URL"},
                        "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 30},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_screenshot",
                "description": "使用无头浏览器打开 URL 并截图保存。需传 url 和 output_path（如 screenshot.png，会保存到 workspace）。用户说「截个屏发给我」时，先截图再调用 send_image 发送。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要截图的 URL"},
                        "output_path": {"type": "string", "description": "截图保存路径，相对于 workspace（如 data/workspace）"},
                        "timeout_sec": {"type": "integer", "description": "超时秒数，建议 30+", "default": 30},
                    },
                    "required": ["url", "output_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_content",
                "description": "获取网页内容，可用 CSS 选择器提取特定区域。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要获取内容的 URL"},
                        "selector": {"type": "string", "description": "可选，CSS 选择器如 #main 或 .article"},
                        "timeout_sec": {"type": "integer", "description": "超时秒数", "default": 30},
                    },
                    "required": ["url"],
                },
            },
        },
    ]


def _effective_timeout(kw: dict, config_default: int) -> int:
    """取 config 与模型传入的较大值，避免模型传过小导致超时"""
    val = kw.get("timeout_sec")
    model_val = int(float(val)) if val is not None else config_default
    return max(model_val, config_default)


def _make_executors(workspace: Optional[Path] = None, timeout_sec: int = 30) -> dict:
    """创建带 workspace 的浏览器工具执行器，截图保存到 workspace 目录"""
    wp = Path(workspace) if workspace else None
    return {
        "browser_navigate": lambda **kw: browser_navigate(kw.get("url", ""), timeout_sec=_effective_timeout(kw, timeout_sec)),
        "browser_screenshot": lambda **kw: browser_screenshot(
            kw.get("url", ""),
            timeout_sec=_effective_timeout(kw, timeout_sec),
            output_path=kw.get("output_path"),
            workspace=wp,
        ),
        "browser_content": lambda **kw: browser_content(
            kw.get("url", ""),
            selector=kw.get("selector"),
            timeout_sec=_effective_timeout(kw, timeout_sec),
        ),
    }
