"""
LLM 调用统计：各模型调用次数、累计 input/output tokens、占比
"""
from util.log import log

_stats: dict[str, dict] = {}  # model_id -> {calls, input_tokens, output_tokens}
_known_models: list[str] = []  # 已知模型列表（用于每次请求后全量打印）


def set_known_models(models: list[str]) -> None:
    """注册已知模型列表，每次请求完成后会打印所有模型的统计（未使用的标「本次未使用」）"""
    global _known_models
    _known_models = list(dict.fromkeys(models))  # 去重保序


def record(model_id: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """记录一次调用，并立即打出所有模型的统计日志"""
    if model_id not in _stats:
        _stats[model_id] = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    s = _stats[model_id]
    s["calls"] += 1
    s["input_tokens"] += prompt_tokens
    s["output_tokens"] += completion_tokens

    # 汇总（用于占比）
    total_calls = sum(m["calls"] for m in _stats.values())
    total_in = sum(m["input_tokens"] for m in _stats.values())
    total_out = sum(m["output_tokens"] for m in _stats.values())

    # 打印所有模型：本次使用的有 in/out，未使用的标「本次未使用」
    models_to_log = _known_models if _known_models else list(_stats.keys())
    for mid in models_to_log:
        m = _stats.get(mid, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        label = mid[:30] + ("..." if len(mid) > 30 else mid)
        if mid == model_id:
            pc = 100 * m["calls"] / total_calls if total_calls else 0
            pi = 100 * m["input_tokens"] / total_in if total_in else 0
            po = 100 * m["output_tokens"] / total_out if total_out else 0
            log(
                "llm",
                "stats [%s] 本次 in=%d out=%d | 累计 calls=%d in=%d out=%d | 占比 calls=%.1f%% in=%.1f%% out=%.1f%%",
                label,
                prompt_tokens,
                completion_tokens,
                m["calls"],
                m["input_tokens"],
                m["output_tokens"],
                pc,
                pi,
                po,
            )
        else:
            pc = 100 * m["calls"] / total_calls if total_calls else 0
            pi = 100 * m["input_tokens"] / total_in if total_in else 0
            po = 100 * m["output_tokens"] / total_out if total_out else 0
            log(
                "llm",
                "stats [%s] 本次未使用 | 累计 calls=%d in=%d out=%d | 占比 calls=%.1f%% in=%.1f%% out=%.1f%%",
                label,
                m["calls"],
                m["input_tokens"],
                m["output_tokens"],
                pc,
                pi,
                po,
            )


def get_stats() -> dict:
    """返回当前统计（只读）"""
    return {k: dict(v) for k, v in _stats.items()}


def reset() -> None:
    """清空统计（测试用）"""
    global _known_models
    _stats.clear()
    _known_models.clear()
