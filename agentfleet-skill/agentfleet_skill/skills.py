"""
AgentFleet Skill 定义
===================
Provider Agent 可注册的独立技能。

每个 Skill 是独立的 Python 模块，Agent 开发者可以自由扩展。
AgentFleet 不绑定技能逻辑——这是 Provider 自己的业务代码。
"""

from typing import Callable


class Skill:
    """单个 Skill 定义"""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable,
        price: float = 0.0,
        task_type: str = "api_call",
    ):
        self.name = name
        self.description = description
        self.handler = handler
        self.price = price
        self.task_type = task_type

    def execute(self, target: str, **kwargs) -> dict:
        """执行这个 Skill"""
        return self.handler(target, **kwargs)


class SkillRegistry:
    """
    Skill 注册表。
    Provider Agent 把自己会的技能注册到这里。
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def list_names(self) -> list[str]:
        return list(self._skills.keys())


# ═══════════════════════════════════════════════════════
#  内置 Skill 示例
# ═══════════════════════════════════════════════════════

def _stock_handler(target: str, **kwargs) -> dict:
    """股票分析处理函数"""
    import random
    symbol = target.upper()
    return {
        "symbol": symbol,
        "price": round(random.uniform(100, 500), 2),
        "change_pct": round(random.uniform(-5, 5), 2),
        "recommendation": random.choice(["BUY", "HOLD", "SELL"]),
        "analysis": f"{symbol} 技术面偏多，成交量放大。",
    }


def _translate_handler(target: str, lang: str = "zh", **kwargs) -> dict:
    """翻译处理函数"""
    return {
        "original": target,
        "translated": f"[{lang}] {target}",
        "target_lang": lang,
    }


def _weather_handler(target: str, **kwargs) -> dict:
    """天气查询处理函数"""
    import random
    return {
        "city": target,
        "temperature": random.randint(-5, 40),
        "condition": random.choice(["晴", "多云", "小雨", "阴"]),
        "humidity": random.randint(30, 90),
    }


STOCK_SKILL = Skill(
    name="stock_analysis",
    description="股票技术分析 + 买卖建议",
    handler=_stock_handler,
    price=5.0,
    task_type="browser_search",
)

TRANSLATE_SKILL = Skill(
    name="translation",
    description="多语言翻译服务",
    handler=_translate_handler,
    price=2.0,
    task_type="api_call",
)

WEATHER_SKILL = Skill(
    name="weather_query",
    description="全球城市天气查询",
    handler=_weather_handler,
    price=1.0,
    task_type="api_call",
)


def create_default_registry() -> SkillRegistry:
    """创建包含默认 Skill 的注册表"""
    registry = SkillRegistry()
    registry.register(STOCK_SKILL)
    registry.register(TRANSLATE_SKILL)
    registry.register(WEATHER_SKILL)
    return registry
