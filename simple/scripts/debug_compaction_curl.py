#!/usr/bin/env python3
"""
调试 compaction 摘要请求：生成 curl 命令或直接请求。
用法：
  python scripts/debug_compaction_curl.py                    # 从 stdin 读取 prompt，输出 curl
  python scripts/debug_compaction_curl.py --request         # 直接发请求（需 pip install requests）
  echo "prompt内容" | python scripts/debug_compaction_curl.py
"""
import json
import sys
from pathlib import Path

# 从项目根读取 config 获取 base_url 和 model
def _load_config():
    base = Path(__file__).resolve().parent.parent
    for name in ("config.yaml", "config.example.yaml"):
        p = base / name
        if p.exists():
            import yaml
            with open(p, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            local = cfg.get("local", {}) or {}
            return local.get("base_url", "http://localhost:11434/v1"), local.get("model", "qwen3.5fast_longcontext")
    return "http://localhost:11434/v1", "qwen3.5fast_longcontext"

BASE_URL, MODEL = _load_config()


def main():
    do_request = "--request" in sys.argv or "-r" in sys.argv
    user_content = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not user_content:
        print("用法: 将 compaction prompt 通过 stdin 传入，或从日志复制", file=sys.stderr)
        print("示例: 从日志复制 '对话历史：' 到 '摘要：' 之间的内容，加上头尾", file=sys.stderr)
        sys.exit(1)

    # 若只有对话历史，补全 prompt 结构
    if "请将以下对话历史" not in user_content:
        user_content = f"""请将以下对话历史压缩为一段简洁摘要（200字以内），保留：关键决策、用户偏好、未完成事项、重要事实、失败/错误信息（便于后续自我修正）。只输出摘要，不要其他内容。

对话历史：
---
{user_content}
---
摘要："""

    # 不传 system：指令已在 user_content 中，system 会严重拖慢 Ollama
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": user_content}],
        "temperature": 0.2,
        "stream": False,
    }

    out_path = Path(__file__).parent.parent / "data" / "debug_compaction_request.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    url = BASE_URL.rstrip("/") + "/chat/completions"
    curl_cmd = f'''curl -X POST "{url}" \\
  -H "Content-Type: application/json" \\
  -d @{out_path}'''
    print(curl_cmd)

    if do_request:
        try:
            import requests
            r = requests.post(url, json=payload, timeout=300)
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print("\n--- 响应 ---\n", content)
        except ImportError:
            print("\n需 pip install requests 才能用 --request", file=sys.stderr)
        except Exception as e:
            print(f"\n请求失败: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
