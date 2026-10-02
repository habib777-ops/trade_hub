import os
import sys
import time
import asyncio
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.mt5_interface import MT5Handler
from engines.macro_engine import MacroEngine
from engines.technical_engine import TechnicalEngine
from engines.vision_engine import VisionEngine
from agent.trading_agent import TradingPlanAgent
from interfaces.discord_bot import DiscordTradeBot

load_dotenv()

SYMBOL = os.getenv("MT5_SYMBOL", "XAUUSD")
MAGIC = int(os.getenv("MT5_MAGIC", 100200))
AUTO_EXECUTE = os.getenv("AUTO_EXECUTE", "False").lower() in ["true", "1"]
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 1.0))
SL_POINTS = float(os.getenv("MT5_SL_POINTS", 300.0))
TP_POINTS = float(os.getenv("MT5_TP_POINTS", 600.0))

async def run_orchestrator():
    print("=" * 60)
    print("🚀 [STARTUP] Démarrage du Hub TradeAgent...")
    print(f"🔹 Symbole : {SYMBOL} | Magic : {MAGIC}")
    print(f"🔹 Mode Auto-Exécution : {AUTO_EXECUTE}")
    print("=" * 60)

    mt5_handler = MT5Handler(SYMBOL, MAGIC)
    agent = TradingPlanAgent()

    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    discord_bot = None

    if discord_token and discord_token != "VOTRE_TOKEN_DISCORD_ICI":
        discord_bot = DiscordTradeBot(mt5_handler)
        asyncio.create_task(discord_bot.client.start(discord_token))
        print("✅ Client Discord initialisé.")
    else:
        print("⚠️ Token Discord non configuré. Mode autonome / Web uniquement.")

    last_candle_time = None

    while True:
        try:
            df_m5 = mt5_handler.get_rates(mt5.TIMEFRAME_M5, 100)
            df_h1 = mt5_handler.get_rates(mt5.TIMEFRAME_H1, 100)

            if not df_m5.empty and not df_h1.empty:
                current_candle_time = df_m5.iloc[-1]['time']

                if last_candle_time is None:
                    last_candle_time = current_candle_time
                    print(f"⏱️ [BOT READY] Synchronisé sur la bougie M5.")

                elif current_candle_time > last_candle_time:
                    last_candle_time = current_candle_time
                    print(f"\n🔔 [NOUVELLE BOUGIE M5] Analyse en cours sur {SYMBOL}...")

                    macro_data = MacroEngine.analyze(df_h1, SYMBOL)
                    tech_data = TechnicalEngine.analyze(df_m5, SYMBOL)
                    vision_data = VisionEngine.analyze(df_m5, tech_data["fvg"])

                    plan_data = agent.evaluate_and_build_plan(macro_data, tech_data, vision_data)

                    if plan_data["decision"] != "HOLD":
                        print(f"🎯 [PLAN DÉTECTÉ] Action: {plan_data['decision']} | Raison: {plan_data['reason']}")

                        if AUTO_EXECUTE:
                            print("⚡ [AUTO-EXECUTE] Exécution directe sur MetaTrader 5...")
                            success, msg = mt5_handler.execute_order(
                                action=plan_data["decision"],
                                risk_percent=RISK_PERCENT,
                                sl_points=SL_POINTS,
                                tp_points=TP_POINTS
                            )
                            print(f"Statut : {msg}")
                        
                        elif discord_bot:
                            print("📲 [HUMAN-IN-THE-LOOP] Envoi de la proposition vers Discord...")
                            await discord_bot.send_trading_plan(plan_data, vision_data["image_bytes"])
                        
                        else:
                            print("ℹ️ Plan généré. En attente de confirmation via le Dashboard Streamlit.")

                    else:
                        print(f"ℹ️ Pas de setup valide (HOLD) : {plan_data['reason']}")

        except Exception as e:
            print(f"⚠️ Erreur dans la boucle principale : {e}")

        await asyncio.sleep(3)

def main():
    try:
        asyncio.run(run_orchestrator())
    except KeyboardInterrupt:
        print("\n🛑 Arrêt propre du bot. Shutdown MT5...")
        mt5.shutdown()

if __name__ == "__main__":
    main()
