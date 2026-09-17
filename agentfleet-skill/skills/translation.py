"""
翻译 Provider Skill
===================
独立的 Provider 技能模块：翻译服务。
"""


class TranslationProvider:
    """翻译 Provider"""

    def translate(self, text: str, target_lang: str = "zh") -> dict:
        """执行翻译"""
        return {
            "original": text,
            "translated": f"[{target_lang}] {text}",
            "target_lang": target_lang,
        }

    @staticmethod
    def service_info():
        return {
            "name": "translation",
            "description": "多语言翻译服务 (中/英/日/韩)",
            "price": 2.0,
            "task_type": "api_call",
        }
