import pandas as pd
import MetaTrader5 as mt5
from core.risk_manager import RiskManager

class MT5Handler:
    def __init__(self, symbol: str, magic: int):
        self.symbol = symbol
        self.magic = magic
        self._initialize()

    def _initialize(self):
        if not mt5.initialize():
            raise SystemExit(f"❌ Impossible de se connecter à MT5. Erreur : {mt5.last_error()}")

    def get_rates(self, timeframe, count: int = 100) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        return pd.DataFrame(rates)

    def execute_order(self, action: str, risk_percent: float, sl_points: float, tp_points: float) -> tuple[bool, str]:
        tick = mt5.symbol_info_tick(self.symbol)
        info = mt5.symbol_info(self.symbol)

        if not tick or not info:
            return False, "❌ Échec de récupération des prix en direct (tick/info)."

        price = tick.ask if action == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        lot_size = RiskManager.calculate_lot_size(self.symbol, risk_percent, sl_points)
        sl_price, tp_price = RiskManager.calculate_sl_tp(self.symbol, action, price, sl_points, tp_points)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 20,
            "magic": self.magic,
            "comment": "TradeAgent Core Order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            return True, f"Ticket #{result.order} | Lot: {lot_size} | Prix: {price:.2f}"
        
        return False, f"Code d'erreur MT5 {result.retcode}: {result.comment}"
