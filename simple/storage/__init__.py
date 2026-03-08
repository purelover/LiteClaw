"""存储层：Session 与对话历史持久化"""
from .db import Storage, init_storage

__all__ = ["Storage", "init_storage"]
