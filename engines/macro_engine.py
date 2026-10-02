import pandas as pd
from datetime import datetime, timedelta

class MacroEngine:
    @staticmethod
    def analyze(df_h1: pd.DataFrame, symbol: str) -> dict:
        if df_h1 is None or len(df_h1) < 50:
            return {
                "h1_trend": "NEUTRAL",
                "news_blocked": False,
                "news_reason": "Données H1 insuffisantes"
            }

        ema_20_h1 = df_h1['close'].ewm(span=20, adjust=False).mean().iloc[-2]
        ema_50_h1 = df_h1['close'].ewm(span=50, adjust=False).mean().iloc[-2]
        last_close = df_h1['close'].iloc[-2]

        if last_close > ema_20_h1 > ema_50_h1:
            h1_trend = "BULLISH"
        elif last_close < ema_20_h1 < ema_50_h1:
            h1_trend = "BEARISH"
        else:
            h1_trend = "NEUTRAL"

        news_blocked, news_reason = MacroEngine._check_news_shield(symbol)

        return {
            "h1_trend": h1_trend,
            "h1_close": float(last_close),
            "h1_ema20": float(ema_20_h1),
            "h1_ema50": float(ema_50_h1),
            "news_blocked": news_blocked,
            "news_reason": news_reason
        }

    @staticmethod
    def _check_news_shield(symbol: str) -> tuple[bool, str]:
        now = datetime.now()
        base_curr = symbol[:3]
        quote_curr = symbol[3:6] if len(symbol) >= 6 else "USD"

        high_impact_events = [
            {"title": "US CPI Inflation", "currency": "USD", "time": now + timedelta(minutes=45)}
        ]

        for event in high_impact_events:
            if event["currency"] in [base_curr, quote_curr]:
                diff_min = (event["time"] - now).total_seconds() / 60.0
                if 0 <= diff_min <= 30:
                    return True, f"⚠️ ROUGE : Annonce '{event['title']}' dans {int(diff_min)} min."

        return False, "✅ Pas d'annonce majeure imminente."
