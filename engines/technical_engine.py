import pandas as pd
import numpy as np

class TechnicalEngine:
    @staticmethod
    def analyze(df_m5: pd.DataFrame, symbol: str) -> dict:
        if df_m5 is None or len(df_m5) < 30:
            return {"current_price": 0.0, "fvg": "NONE", "rsi": 50.0, "atr": 0.0}

        delta = df_m5['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df_m5['rsi'] = 100 - (100 / (1 + rs))

        df_m5['ema_20'] = df_m5['close'].ewm(span=20, adjust=False).mean()
        df_m5['ema_50'] = df_m5['close'].ewm(span=50, adjust=False).mean()

        high_low = df_m5['high'] - df_m5['low']
        high_close = np.abs(df_m5['high'] - df_m5['close'].shift())
        low_close = np.abs(df_m5['low'] - df_m5['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df_m5['atr'] = true_range.rolling(14).mean()

        last = df_m5.iloc[-2]

        fvg = "NONE"
        b1 = df_m5.iloc[-4]
        b3 = df_m5.iloc[-2]

        if b3['low'] > b1['high']:
            fvg = "BULLISH_FVG"
        elif b3['high'] < b1['low']:
            fvg = "BEARISH_FVG"

        return {
            "symbol": symbol,
            "current_price": float(last['close']),
            "rsi": float(last['rsi']),
            "atr": float(last['atr']),
            "fvg": fvg,
            "ema_trend": "BULLISH" if last['ema_20'] > last['ema_50'] else "BEARISH"
        }
