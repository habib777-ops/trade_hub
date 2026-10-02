import MetaTrader5 as mt5

class RiskManager:
    @staticmethod
    def calculate_lot_size(symbol: str, risk_percent: float, sl_points: float) -> float:
        account_info = mt5.account_info()
        symbol_info = mt5.symbol_info(symbol)

        if not account_info or not symbol_info:
            return float(symbol_info.volume_min if symbol_info else 0.01)

        balance = account_info.balance
        risk_amount = balance * (risk_percent / 100.0)

        point_value = symbol_info.trade_tick_value
        if point_value == 0 or sl_points == 0:
            return float(symbol_info.volume_min)

        raw_lot = risk_amount / (sl_points * point_value)

        volume_step = symbol_info.volume_step
        min_volume = symbol_info.volume_min
        max_volume = symbol_info.volume_max

        calculated_lot = round(raw_lot / volume_step) * volume_step
        calculated_lot = max(min_volume, min(max_volume, calculated_lot))

        return float(round(calculated_lot, 2))

    @staticmethod
    def calculate_sl_tp(symbol: str, action: str, price: float, sl_points: float, tp_points: float) -> tuple[float, float]:
        symbol_info = mt5.symbol_info(symbol)
        point = symbol_info.point if symbol_info else 0.01

        if action == "BUY":
            sl = price - (sl_points * point)
            tp = price + (tp_points * point)
        else:
            sl = price + (sl_points * point)
            tp = price - (tp_points * point)

        return round(sl, 2), round(tp, 2)
