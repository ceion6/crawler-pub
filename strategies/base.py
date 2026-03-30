class StrategyBase:
    """公开执行器的策略接口占位。"""

    def check_stock(self, html_text: str) -> bool:
        raise NotImplementedError

    def extract_price(self, html_text: str) -> str:
        raise NotImplementedError
