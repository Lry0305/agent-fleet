"""
定价 — 全平台统一费率，按交付物真实 token 数结算
================================================================
价格不是老板定的，也不是 provider 自己报的：老板估不准一个任务要产出多少
token，provider 也没法把报价和真实算力精确挂钩，与其两边都猜数字，不如
价格从一开始就是统一的（backend/config.py 的 GLOBAL_RATE_PER_1K），选 provider
时看任务适配（skill 匹配 + 声誉），不比价。

跟更早版本的区别：最早靠关键词猜任务"复杂度"（出现"深度/报告"就判 2 倍），
后来改成 provider 自报单价 + 老板猜一个预授权上限（max_credits）+ 竞价，
两版都在让人猜一个数字。现在两阶段都不用猜：
  - 派单时：任务真实会产出多少还不知道，按统一费率 × 保守输出上限自动锁仓
    （预授权），不是最终要付的钱，不需要老板/agent 指定任何数字。
  - 交付后：拿 provider 真实交回来的内容，用 tiktoken 数一遍真实 token 数，
    按统一费率结算——多退（锁多了退差价），不能超过锁仓上限（少不补）。
"""

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


def count_tokens(text: str) -> int:
    """数真实 token 数。tiktoken 装不上时退化成"字符数/4"的粗略估算，不让整条链路崩掉。"""
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text) // 4)


def _extract_text(d: dict) -> str:
    """把 dict 里所有字符串字段拼起来——input_json / output_json 都用这个数 token。"""
    parts = [str(v) for v in (d or {}).values() if isinstance(v, str)]
    return " ".join(parts)


def input_text(input_dict: dict) -> str:
    """兼容旧调用点：task.input 拼成文本。"""
    return _extract_text(input_dict)


def output_text(output_dict: dict) -> str:
    """provider 交付物 output 拼成文本，用来数真实产出的 token 数。"""
    return _extract_text(output_dict)


def estimate_lock() -> tuple[float, str]:
    """派单时自动算的预授权锁仓额——不是最终结算价，是"最多花这么多"的上限。

    全平台统一费率（GLOBAL_RATE_PER_1K）× DEFAULT_MAX_TOKENS_CAP（保守假设的单任务
    输出上限），不需要老板/客户端指定任何数字（没有 max_credits 这种要猜 token 量的
    字段了）。返回 (锁仓 credits, 简短说明)——说明字符串是给聊天气泡/任务卡片这种
    一行内联展示用的，故意写得短。
    """
    from backend.config import DEFAULT_MAX_TOKENS_CAP, GLOBAL_RATE_PER_1K
    if GLOBAL_RATE_PER_1K <= 0:
        return 0.0, "未定价"
    lock_amount = round(GLOBAL_RATE_PER_1K * DEFAULT_MAX_TOKENS_CAP / 1000, 2)
    return lock_amount, "预授权"


def settle_price(output_dict: dict) -> tuple[float, int]:
    """交付后的真实结算价：真数交付物的 token 数 × 全平台统一费率。返回 (结算价, token 数)。"""
    from backend.config import GLOBAL_RATE_PER_1K
    if GLOBAL_RATE_PER_1K <= 0:
        return 0.0, 0
    tokens = count_tokens(output_text(output_dict))
    price = round(GLOBAL_RATE_PER_1K * tokens / 1000, 2)
    return price, tokens
