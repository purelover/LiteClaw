"""统一日志，带时间戳"""
from datetime import datetime


def log(tag: str, msg: str, *args):
    """带时间戳的日志输出"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = msg % args if args else msg
    print(f"[{ts}] [{tag}] {text}")
