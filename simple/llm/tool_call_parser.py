"""
解析模型以文本形式输出的工具调用（部分模型不输出结构化 tool_calls，而是把 JSON 写在 content 里）
"""
import json
import re
import uuid


# 常见错误工具名 -> 正确工具名
NAME_ALIASES = {
    "file_name": "file_write",
    "file_write_file": "file_write",
    "write_file": "file_write",
    "create_file": "file_write",
}


def _parse_args(args) -> dict:
    """arguments 可能是 dict 或 JSON 字符串"""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return {}


def parse_tool_calls_from_text(content: str) -> list[dict]:
    """
    从模型 content 中解析工具调用。
    支持格式：
    - ```json\n{"name": "file_write", "arguments": {...}}\n```
    - 单行 JSON: {"name": "file_write", "arguments": {...}}
    返回 OpenAI 格式的 tool_calls 列表。
    """
    if not content or not isinstance(content, str):
        return []

    results = []
    # 匹配 ```json ... ``` 或 ``` ... ```
    pattern = r"```(?:json)?\s*\n?(.*?)```"
    for m in re.finditer(pattern, content, re.DOTALL):
        block = (m.group(1) or "").strip()
        if not block:
            continue
        # 先尝试整个 block 作为单个 JSON（多行格式）
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                objs = [obj]
            elif isinstance(obj, list):
                objs = [x for x in obj if isinstance(x, dict)]
            else:
                objs = []
        except json.JSONDecodeError:
            objs = []
        # 若失败，再按行解析
        if not objs:
            for line in block.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        objs.append(obj)
                except json.JSONDecodeError:
                    continue
        for obj in objs:
            name = obj.get("name") or (obj.get("function") or {}).get("name")
            args = obj.get("arguments") or (obj.get("function") or {}).get("arguments") or obj.get("args", {})
            if not name:
                continue
            name = NAME_ALIASES.get(name, name)
            args = _parse_args(args)
            results.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args),
                },
            })

    # 也尝试匹配不在代码块内的 JSON（宽松）
    if not results:
        # 匹配 {"name": "xxx", "arguments": {...}}
        loose = re.findall(r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\}|"[^"]*")\s*\}', content)
        for name, args_str in loose:
            name = NAME_ALIASES.get(name, name)
            try:
                args = json.loads(args_str) if args_str.startswith("{") else {}
            except json.JSONDecodeError:
                args = {}
            results.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })

    # Ollama/Qwen 等可能用 <tool_call><function=name><parameter=key>value</parameter></function></tool_call>
    if not results:
        results = _parse_hermes_param_format(content)

    return results


def _parse_hermes_param_format(content: str) -> list[dict]:
    """
    解析 <tool_call><function=name><parameter=key>value</parameter>...</function></tool_call> 格式。
    Ollama 部分模型将 tool call 放在 reasoning 中且用此格式。
    """
    if not content or "<tool_call>" not in content:
        return []
    results = []
    # 匹配 <tool_call>...</tool_call> 块
    block_pattern = r"<tool_call>(.*?)</tool_call>"
    for block_m in re.finditer(block_pattern, content, re.DOTALL):
        block = block_m.group(1) or ""
        # <function=name>
        fn_m = re.search(r"<function=([^>]+)>", block)
        if not fn_m:
            continue
        name = fn_m.group(1).strip()
        name = NAME_ALIASES.get(name, name)
        # <parameter=key>value</parameter>
        args = {}
        for param_m in re.finditer(r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>", block, re.DOTALL):
            key = param_m.group(1).strip()
            val = (param_m.group(2) or "").strip()
            args[key] = val
        results.append({
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return results
