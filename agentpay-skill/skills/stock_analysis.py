"""
股票分析 Provider Skill
========================
独立的 Provider 技能模块：通过 CUA-driver 操控浏览器查股票数据。

这是 Provider Agent 自己的业务逻辑，不依赖 AgentPay。
AgentPay SDK 只负责支付结算。
"""

from agentpay_skill.cua_runner import CUARunner


class StockAnalysisProvider:
    """
    股票分析 Provider。
    组合：自己的分析逻辑 + CUA-Driver 桌面自动化。
    """

    def __init__(self, headless: bool = False):
        self.cua = CUARunner(headless=headless)

    def analyze(self, symbol: str) -> dict:
        """
        执行股票分析。
        如果 CUA-driver 可用 → 真实操控浏览器搜索
        不可用 → 返回模拟数据
        """
        result = self.cua.run_stock_analysis(symbol)
        if result.get("success"):
            return {
                "symbol": symbol.upper(),
                "price": result.get("analysis", {}).get("price", "N/A"),
                "recommendation": result.get("analysis", {}).get("recommendation", "N/A"),
                "method": "cua-driver" if self.cua.available else "simulated",
            }
        return {"error": result.get("error", "未知错误"), "symbol": symbol}

    @staticmethod
    def service_info():
        return {
            "name": "stock_analysis",
            "description": "股票技术分析 + 买卖建议 (CUA-driver 浏览器搜索)",
            "price": 5.0,
            "task_type": "browser_search",
        }
