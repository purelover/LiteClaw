"""
从 LLM 回复中提取可执行代码块（```python ... ```、```bash ... ``` 等）
"""
import re
from dataclasses import dataclass


@dataclass
class CodeBlock:
    lang: str
    code: str


EXECUTABLE_LANGS = frozenset({"python", "py", "bash", "sh", "shell"})


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """提取 Markdown 代码块，返回可执行语言的块列表"""
    pattern = r"```(\w*)\s*\n(.*?)```"
    blocks: list[CodeBlock] = []
    for m in re.finditer(pattern, text, re.DOTALL):
        lang = (m.group(1) or "").strip().lower()
        code = (m.group(2) or "").strip()
        if not code:
            continue
        if lang in EXECUTABLE_LANGS:
            blocks.append(CodeBlock(lang=lang, code=code))
    return blocks
