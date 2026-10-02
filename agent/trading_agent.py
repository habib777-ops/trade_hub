import os
from datetime import datetime

class TradingPlanAgent:
    def __init__(self, min_confidence: float = 0.75):
        self.min_confidence = min_confidence

    def evaluate_and_build_plan(self, macro_data: dict, tech_data: dict, vision_data: dict) -> dict:
        if macro_data.get("news_blocked", False):
            return {
                "decision": "HOLD",
                "confidence": 0.0,
                "reason": f"Filtre Calendrier Actif -> {macro_data.get('news_reason')}",
                "plan": None
            }

        symbol = tech_data.get("symbol", "XAUUSD")
        current_price = tech_data.get("current_price", 0.0)
        h1_trend = macro_data.get("h1_trend", "NEUTRAL")
        fvg = tech_data.get("fvg", "NONE")
        visual_bias = vision_data.get("visual_bias", "NEUTRAL")
        rejection = vision_data.get("rejection", "NONE")
        rsi = tech_data.get("rsi", 50.0)

        action = "HOLD"
        reasons = []

        if h1_trend == "BULLISH" and fvg == "BULLISH_FVG" and visual_bias == "BULLISH_TRENDLINE":
            action = "BUY"
            reasons.append("Tendance H1 Haussière + FVG Bullish M5 + Pente Visuelle OpenCV Haussière")
            if rejection == "BULLISH_REJECTION":
                reasons.append("Confirmation supplémentaire : Pinbar de rejet détectée sur M5")

        elif h1_trend == "BEARISH" and fvg == "BEARISH_FVG" and visual_bias == "BEARISH_TRENDLINE":
            action = "SELL"
            reasons.append("Tendance H1 Baissière + FVG Bearish M5 + Pente Visuelle OpenCV Baissière")
            if rejection == "BEARISH_REJECTION":
                reasons.append("Confirmation supplémentaire : Pinbar de rejet détectée sur M5")

        if action == "HOLD":
            return {
                "decision": "HOLD",
                "confidence": 0.0,
                "reason": f"Manque de confluence (H1: {h1_trend} | FVG: {fvg} | OpenCV: {visual_bias})",
                "plan": None
            }

        sl_points = float(os.getenv("MT5_SL_POINTS", 300.0))
        tp_points = float(os.getenv("MT5_TP_POINTS", 600.0))
        risk_percent = float(os.getenv("RISK_PERCENT", 1.0))

        point_scale = 0.01 if "XAU" in symbol or "JPY" in symbol else 0.0001
        sl_price = current_price - (sl_points * point_scale) if action == "BUY" else current_price + (sl_points * point_scale)
        tp_price = current_price + (tp_points * point_scale) if action == "BUY" else current_price - (tp_points * point_scale)

        return {
            "decision": action,
            "confidence": 0.85,
            "reason": " | ".join(reasons),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plan": {
                "symbol": symbol,
                "entry_price": round(current_price, 2),
                "stop_loss_price": round(sl_price, 2),
                "take_profit_price": round(tp_price, 2),
                "sl_points": sl_points,
                "tp_points": tp_points,
                "risk_percent": risk_percent,
                "risk_reward_ratio": round(tp_points / sl_points, 2)
            }
        }
