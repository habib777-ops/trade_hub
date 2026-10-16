# -*- coding: utf-8 -*-
import os
import sys
import time
import base64
import json
import logging
import subprocess
import atexit
import requests
import re
import sqlite3
import pandas as pd
import pandas_ta as ta
import mplfinance as mpf
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from dotenv import load_dotenv

# Chargement automatique des variables d'environnement depuis le fichier .env
load_dotenv()

os.environ["YOLO_VERBOSE"] = "False"

# --- CONFIGURATION VIA VARIABLES D'ENVIRONNEMENT ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "https://ollama.com/api/generate")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

NEWS_JSON_FILE = os.getenv(
    "NEWS_JSON_FILE", 
    r"C:\Users\user\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\calendar_news.json"
)
NEWS_TXT_FILE = os.getenv(
    "NEWS_TXT_FILE", 
    r"C:\Users\user\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\macro_news.txt"
)

from patterns import detect_all_patterns
from engines.fundamental_engine import FundamentalEngine

# Tentative d'importation d'Ultralytics pour la vision YOLO ONNX
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO = None
    YOLO_AVAILABLE = False
except Exception:
    YOLO = None
    YOLO_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TradeHub")

CHART_PATH = "data/cache/charts/EURUSD_M5_pro.png"
STATE_FILE = "data/cache/last_analysis.json"
DB_PATH = "data/trading_hub.db"
CONFIG_PATH = "data/cache/config.json"
STEALTH_FILE = "data/cache/stealth_orders.json"
VISION_MODEL_PATH = "runs/detect/models/runs/smc_vision_v1-4/weights/best.onnx"


def send_discord_alert(message):
    """Envoie une notification formatée vers un salon Discord via Webhook."""
    if not DISCORD_WEBHOOK_URL or "api/webhooks" not in DISCORD_WEBHOOK_URL:
        return  # Désactivé si l'URL n'est pas configurée
    
    payload = {"content": message}
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code not in [200, 204]:
            logging.error(f"❌ Erreur envoi Discord (Code {response.status_code})")
    except Exception as e:
        logging.error(f"❌ Exception lors de l'envoi de l'alerte Discord : {e}")


# ==========================================
# 0. MODULE PERSISTANCE SQLITE & INDEX
# ==========================================
def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyses_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            bias TEXT,
            analyse_fondamentale TEXT,
            analyse_technique TEXT,
            raw_json TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            action_type TEXT,
            ticket INTEGER,
            price REAL,
            sl REAL,
            tp REAL,
            details TEXT
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_timestamp ON analyses_history(timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON analyses_history(symbol);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades_history(timestamp);")
    
    conn.commit()
    conn.close()

def log_analysis_to_db(plan):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO analyses_history (timestamp, symbol, bias, analyse_fondamentale, analyse_technique, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            now_str,
            plan.get("symbol", "EURUSD"),
            plan.get("bias", "NEUTRAL"),
            plan.get("analyse_fondamentale", ""),
            plan.get("analyse_technique", ""),
            json.dumps(plan, ensure_ascii=False)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Erreur insertion BDD analyse : {e}")

def log_trade_event_to_db(symbol, action_type, ticket, price=0.0, sl=0.0, tp=0.0, details=""):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO trades_history (timestamp, symbol, action_type, ticket, price, sl, tp, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (now_str, symbol, action_type, ticket, price, sl, tp, details))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Erreur insertion BDD trade : {e}")

init_db()

# ==========================================
# 1. MODULE CONFIG & GESTION STEALTH
# ==========================================
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"multi_tf_enabled": True, "yolo_vision_enabled": True, "stealth_mode_enabled": False}

def save_stealth_virtual_order(ticket, symbol, action, sl, tp):
    """Enregistre un ordre stealth pour surveillance locale par le bot."""
    os.makedirs("data/cache", exist_ok=True)
    stealth_data = {}
    if os.path.exists(STEALTH_FILE):
        try:
            with open(STEALTH_FILE, "r", encoding="utf-8") as f:
                stealth_data = json.load(f)
        except:
            pass
    
    stealth_data[str(ticket)] = {
        "symbol": symbol,
        "action": action,
        "virtual_sl": sl,
        "virtual_tp": tp,
        "time_added": time.time()
    }
    with open(STEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(stealth_data, f, indent=4)

def manage_stealth_virtual_stops(symbol="EURUSD"):
    """Vérifie en continu si les SL/TP virtuels (masqués au broker) sont atteints."""
    if not mt5.initialize(): return
    if not os.path.exists(STEALTH_FILE): return
    
    try:
        with open(STEALTH_FILE, "r", encoding="utf-8") as f:
            stealth_data = json.load(f)
    except:
        return
        
    if not stealth_data: return

    positions = mt5.positions_get(symbol=symbol)
    if not positions: return
    
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return
    
    active_tickets = {p.ticket for p in positions}
    updated_stealth = {}

    for ticket_str, data in stealth_data.items():
        ticket = int(ticket_str)
        if ticket not in active_tickets:
            continue
            
        pos = next((p for p in positions if p.ticket == ticket), None)
        if not pos: continue
        
        v_sl = data["virtual_sl"]
        v_tp = data["virtual_tp"]
        action = data["action"]
        
        triggered = False
        reason = ""
        
        if action == "BUY":
            if tick.bid <= v_sl:
                triggered = True
                reason = f"Virtual SL atteint à {tick.bid} (SL: {v_sl})"
            elif tick.bid >= v_tp:
                triggered = True
                reason = f"Virtual TP atteint à {tick.bid} (TP: {v_tp})"
        elif action == "SELL":
            if tick.ask >= v_sl:
                triggered = True
                reason = f"Virtual SL atteint à {tick.ask} (SL: {v_sl})"
            elif tick.ask <= v_tp:
                triggered = True
                reason = f"Virtual TP atteint à {tick.ask} (TP: {v_tp})"
                
        if triggered:
            c_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = tick.bid if c_type == mt5.ORDER_TYPE_SELL else tick.ask
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": symbol,
                "volume": pos.volume,
                "type": c_type,
                "price": price,
                "magic": 234000,
                "comment": "Stealth Auto-Close"
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                send_discord_alert(f"🚨 **TRADE HUB - Fermeture Position**\nTicket : `{ticket}`\nSymbole : `{symbol}`\nMotif : {reason}")
                logger.info(f"🥷 STEALTH DÉCLENCHÉ | Position #{ticket} fermée. Motif: {reason}")
                log_trade_event_to_db(symbol, f"STEALTH_CLOSE_{action}", ticket, price, v_sl, v_tp, reason)
        else:
            updated_stealth[ticket_str] = data

    with open(STEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_stealth, f, indent=4)

# ==========================================
# 2. MODULE DE CAPTURE & VISION YOLO ONNX
# ==========================================
def run_yolo_vision_inference(image_path: str) -> list:
    cfg = load_config()
    if not cfg.get("yolo_vision_enabled", True):
        return [{"label": "YOLO_DISABLED", "confidence": 1.0, "reasoning": "Vision YOLO désactivée manuellement depuis le dashboard."}]

    detections = []
    if not YOLO_AVAILABLE or not os.path.exists(VISION_MODEL_PATH):
        return [{"label": "INFO", "confidence": 1.0, "reasoning": "Modèle YOLO ONNX introuvable au chemin spécifié."}]
    
    try:
        model = YOLO(VISION_MODEL_PATH, task="detect")
        results = model(image_path, verbose=False)
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                class_name = model.names.get(cls_id, f"Class_{cls_id}")
                detections.append({
                    "label": class_name,
                    "confidence": round(conf, 2),
                    "reasoning": f"Structure {class_name} détectée par YOLO ONNX avec {conf*100:.1f}% de confiance."
                })
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'inférence YOLO ONNX : {e}")
    
    return detections

def generate_live_chart(symbol="EURUSD", timeframe=mt5.TIMEFRAME_M5, num_candles=300, output_dir="data/cache/charts"):
    if not mt5.initialize(): 
        return None, []

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_candles)
    if rates is None or len(rates) == 0: 
        return None, []

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)

    detected_patterns = detect_all_patterns(df)
    pattern_text = f" | Formations: {', '.join(detected_patterns)}" if detected_patterns else " | Formations: Aucune"

    df["EMA_50"] = ta.ema(df["close"], length=50, min_periods=1)
    df["EMA_200"] = ta.ema(df["close"], length=200, min_periods=1)
    df["RSI"] = ta.rsi(df["close"], length=14)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch is not None and not stoch.empty:
        df["STOCH_k"] = stoch.iloc[:, 0]
        df["STOCH_d"] = stoch.iloc[:, 1]
    else:
        df["STOCH_k"] = 50
        df["STOCH_d"] = 50

    df["WILLR"] = ta.willr(df["high"], df["low"], df["close"], length=14)
    os.makedirs(output_dir, exist_ok=True)

    apds = [
        mpf.make_addplot(df["EMA_50"], color="blue", width=1.2, panel=0),
        mpf.make_addplot(df["EMA_200"], color="orange", width=1.5, panel=0),
        mpf.make_addplot(df["RSI"], color="purple", width=1.2, panel=1),
        mpf.make_addplot(df["STOCH_k"], color="teal", width=1, panel=2),
        mpf.make_addplot(df["STOCH_d"], color="deeppink", width=1, panel=2),
        mpf.make_addplot(df["WILLR"], color="brown", width=1, panel=3),
        mpf.make_addplot(df["ATR"], color="green", width=1.2, panel=4)
    ]
    mc = mpf.make_marketcolors(up="green", down="red", edge="black", wick="black")
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", y_on_right=True)

    chart_title = f"{symbol} M5 Analysis{pattern_text}"

    mpf.plot(
        df, type="candle", style=s, addplot=apds, 
        title=chart_title,
        savefig=CHART_PATH, figsize=(14, 10), tight_layout=True, 
        panel_ratios=(4, 1, 1, 1, 1)
    )

    vision_detections = run_yolo_vision_inference(CHART_PATH)
    return CHART_PATH, vision_detections

# ==========================================
# 3. MODULE MT5 & GESTION DE RISQUE
# ==========================================
def get_mt5_state(symbol="EURUSD"):
    if not mt5.initialize(): 
        return {"positions_actives": [], "ordres_en_attente": []}
    positions = mt5.positions_get(symbol=symbol)
    orders = mt5.orders_get(symbol=symbol)
    pos_list = [{"ticket": p.ticket, "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL", "prix_ouverture": p.price_open, "profit_actuel": p.profit} for p in positions] if positions else []
    ord_list = [{"ticket": o.ticket, "type": str(o.type), "prix_cible": o.price_open} for o in orders] if orders else []
    return {"positions_actives": pos_list, "ordres_en_attente": ord_list}

def check_and_apply_breakeven(symbol="EURUSD"):
    if not mt5.initialize(): return
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return
    tick = mt5.symbol_info_tick(symbol)
    symbol_info = mt5.symbol_info(symbol)
    if not tick or not symbol_info: return

    for p in positions:
        entry_price = p.price_open
        current_sl = p.sl
        tp = p.tp
        if current_sl == 0: continue

        if p.type == mt5.ORDER_TYPE_BUY:
            initial_risk = entry_price - current_sl
            if initial_risk > 0 and current_sl < entry_price:
                target_trigger = entry_price + initial_risk
                if tick.bid >= target_trigger:
                    new_sl = round(entry_price + (2 * symbol_info.point), symbol_info.digits)
                    req = {"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": symbol, "sl": new_sl, "tp": tp}
                    res = mt5.order_send(req)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"🛡️ BREAK-EVEN (BUY) | Ticket #{p.ticket} | Nouveau SL: {new_sl}")
                        log_trade_event_to_db(symbol, "BREAK_EVEN_BUY", p.ticket, entry_price, new_sl, tp, "R/R 1:1 atteint")

        elif p.type == mt5.ORDER_TYPE_SELL:
            initial_risk = current_sl - entry_price
            if initial_risk > 0 and current_sl > entry_price:
                target_trigger = entry_price - initial_risk
                if tick.ask <= target_trigger:
                    new_sl = round(entry_price - (2 * symbol_info.point), symbol_info.digits)
                    req = {"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "symbol": symbol, "sl": new_sl, "tp": tp}
                    res = mt5.order_send(req)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"🛡️ BREAK-EVEN (SELL) | Ticket #{p.ticket} | Nouveau SL: {new_sl}")
                        log_trade_event_to_db(symbol, "BREAK_EVEN_SELL", p.ticket, entry_price, new_sl, tp, "R/R 1:1 atteint")

def apply_atr_trailing_stop(symbol="EURUSD", timeframe=mt5.TIMEFRAME_M5, atr_length=14, multiplier=2.0):
    if not mt5.initialize(): return
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, atr_length + 20)
    if rates is None or len(rates) < atr_length: return

    df = pd.DataFrame(rates)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=atr_length)
    current_atr = df["atr"].iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0: return

    tick = mt5.symbol_info_tick(symbol)
    symbol_info = mt5.symbol_info(symbol)
    if not tick or not symbol_info: return

    atr_distance = current_atr * multiplier

    for p in positions:
        ticket = p.ticket
        order_type = p.type
        current_sl = p.sl
        tp = p.tp

        if order_type == mt5.ORDER_TYPE_BUY:
            proposed_sl = round(tick.bid - atr_distance, symbol_info.digits)
            if proposed_sl > current_sl and proposed_sl < tick.bid:
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": symbol, "sl": proposed_sl, "tp": tp}
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"📈 TRAILING ATR (BUY) | Ticket #{ticket} | Nouveau SL: {proposed_sl}")
                    log_trade_event_to_db(symbol, "TRAILING_ATR_BUY", ticket, tick.bid, proposed_sl, tp, f"ATR: {current_atr:.5f}")

        elif order_type == mt5.ORDER_TYPE_SELL:
            proposed_sl = round(tick.ask + atr_distance, symbol_info.digits)
            if (proposed_sl < current_sl or current_sl == 0) and proposed_sl > tick.ask:
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": symbol, "sl": proposed_sl, "tp": tp}
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"📉 TRAILING ATR (SELL) | Ticket #{ticket} | Nouveau SL: {proposed_sl}")
                    log_trade_event_to_db(symbol, "TRAILING_ATR_SELL", ticket, tick.ask, proposed_sl, tp, f"ATR: {current_atr:.5f}")

def purge_expired_pending_orders(symbol="EURUSD", max_age_seconds=7200):
    if not mt5.initialize(): return
    orders = mt5.orders_get(symbol=symbol)
    if not orders: return

    current_time = time.time()
    for order in orders:
        order_age = current_time - order.time_setup
        if order_age >= max_age_seconds:
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"⏳ PURGE TEMPORELLE | Ordre #{order.ticket} annulé.")
                log_trade_event_to_db(symbol, "PURGE_EXPIRED_ORDER", order.ticket, order.price_open, 0.0, 0.0, f"Age: {int(order_age // 60)} min")

def manage_ai_orders(gestion_ordres, symbol="EURUSD"):
    if not mt5.initialize() or not gestion_ordres:
        return
    for action_item in gestion_ordres:
        ticket = action_item.get("ticket")
        action = action_item.get("action", "").upper()
        reasoning = action_item.get("reasoning", "Aucune raison spécifiée")
        if not ticket:
            continue

        if action in ["CANCEL_ORDER", "CANCEL", "REMOVE"]:
            orders = mt5.orders_get(ticket=ticket)
            if orders:
                res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"🗑️ ANNULATION IA | Ordre #{ticket} annulé. Motif: {reasoning}")
                    log_trade_event_to_db(symbol, "CANCEL_ORDER_AI", ticket, 0.0, 0.0, 0.0, reasoning)
                else:
                    logger.error(f"❌ Échec annulation ordre #{ticket}. Retcode: {res.retcode if res else 'Inconnu'}")

        elif action in ["CLOSE_POSITION", "CLOSE"]:
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                p = positions[0]
                pos_symbol = p.symbol
                c_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                
                tick = mt5.symbol_info_tick(pos_symbol)
                if not tick:
                    logger.error(f"❌ Impossible d'obtenir le tick pour le symbole {pos_symbol}")
                    continue
                    
                price = tick.bid if c_type == mt5.ORDER_TYPE_SELL else tick.ask
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": ticket,
                    "symbol": pos_symbol,
                    "volume": p.volume,
                    "type": c_type,
                    "price": price,
                    "magic": 234000,
                    "comment": "AI Auto-Close"
                }
                res = mt5.order_send(req)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"🚨 FERMETURE ANTICIPÉE IA | Position #{ticket} fermée. Motif: {reasoning}")
                    send_discord_alert(f"🚨 **TRADE HUB - Fermeture Position**\nTicket : `{ticket}`\nSymbole : `{pos_symbol}`\nMotif : {reasoning}")
                    log_trade_event_to_db(pos_symbol, "CLOSE_POSITION_AI", ticket, price, 0.0, 0.0, reasoning)
                else:
                    logger.error(f"❌ Échec fermeture position #{ticket}. Code retcode MT5 : {res.retcode if res else 'Inconnu'}")
            else:
                logger.warning(f"⚠️ Position #{ticket} introuvable sur MT5 (déjà fermée ou inexistante).")

def extract_price(entry_str):
    matches = re.findall(r"[\d]*[.,][\d]+|\d+", str(entry_str))
    if matches: return float(matches[0].replace(',', '.'))
    return None

def validate_multi_timeframe(symbol: str, signal_action: str, higher_tf=mt5.TIMEFRAME_H1) -> bool:
    cfg = load_config()
    if not cfg.get("multi_tf_enabled", True):
        return True

    if not mt5.initialize():
        return True
    
    rates = mt5.copy_rates_from_pos(symbol, higher_tf, 0, 200)
    if rates is None or len(rates) < 50:
        return True
        
    df = pd.DataFrame(rates)
    df["EMA_50"] = ta.ema(df["close"], length=50, min_periods=1)
    df["EMA_200"] = ta.ema(df["close"], length=200, min_periods=1)
    
    if df["EMA_50"].empty or df["EMA_200"].empty:
        return True
        
    ema_fast = df["EMA_50"].iloc[-1]
    ema_slow = df["EMA_200"].iloc[-1]
    current_close = df["close"].iloc[-1]
    
    if signal_action == "BUY":
        return ema_fast >= ema_slow and current_close >= ema_fast
    elif signal_action == "SELL":
        return ema_fast <= ema_slow and current_close <= ema_slow
            
    return True

def execute_ai_trade(plan, lot_size=0.01):
    if not mt5.initialize(): return False
    symbol = plan.get("symbol", "EURUSD")
    tp_plan = plan.get("plan_de_trading", {})
    action = tp_plan.get("action", "").upper()

    if not validate_multi_timeframe(symbol, action):
        logger.info(f"❌ Signal {action} rejeté par le filtre multi-TF H1.")
        return False
        
    try:
        target_price = extract_price(tp_plan.get("entry_zone", ""))
        sl = float(tp_plan.get("stop_loss", 0))
        tp = float(tp_plan.get("take_profit", 0))
    except (ValueError, TypeError):
        return False

    if not target_price or not sl or not tp:
        logger.error("❌ Données de prix manquantes pour l'exécution.")
        return False

    if action == "BUY" and not (sl < target_price < tp):
        logger.error("🛡️ BLOCAGE GARDE-FOU (BUY) | Niveaux incohérents.")
        return False
    elif action == "SELL" and not (tp < target_price < sl):
        logger.error("🛡️ BLOCAGE GARDE-FOU (SELL) | Niveaux incohérents.")
        return False

    tick = mt5.symbol_info_tick(symbol)
    if not tick: return False
    ask, bid = tick.ask, tick.bid

    if action == "BUY":
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if ask > target_price else mt5.ORDER_TYPE_BUY_STOP
    elif action == "SELL":
        order_type = mt5.ORDER_TYPE_SELL_LIMIT if bid < target_price else mt5.ORDER_TYPE_SELL_STOP
    else:
        return False

    # Vérification du mode Stealth
    cfg = load_config()
    stealth_active = cfg.get("stealth_mode_enabled", False)
    
    broker_sl = 0.0 if stealth_active else sl
    broker_tp = 0.0 if stealth_active else tp

    req = {
        "action": mt5.TRADE_ACTION_PENDING, 
        "symbol": symbol, 
        "volume": float(lot_size), 
        "type": order_type, 
        "price": target_price, 
        "sl": broker_sl, 
        "tp": broker_tp, 
        "magic": 234000, 
        "type_time": mt5.ORDER_TIME_GTC
    }
    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"✅ Ordre EN ATTENTE placé | Ticket : {result.order}")
        save_stealth_virtual_order(result.order, symbol, action, sl, tp)
        send_discord_alert(f"🚀 **TRADE HUB - Nouvel Ordre**\nSymbole : {symbol}\nAction : {action}\nTicket : `{result.order}`\nPrix : `{target_price}`")
        return True
    else:
        logger.error(f"❌ Échec placement MT5. Code retcode : {result.retcode if result else 'Inconnu'}")
        return False

# ==========================================
# 4. API INTERNE FASTAPI (UNIFIÉE)
# ==========================================
app = FastAPI(title="Trade Agent Hub Unified", version="2.0")

def load_macro_news_from_disk(symbol: str = "EURUSD") -> str:
    """
    Charge et formate explicitement les actualités économiques pour les rendre 
    parfaitement claires et non équivoques pour le modèle Gemma.
    """
    if os.path.exists(NEWS_JSON_FILE):
        try:
            with open(NEWS_JSON_FILE, "r", encoding="utf-8") as f:
                events = json.load(f)
            
            if not events:
                return "Aucun événement économique récent dans le calendrier."

            target_currencies = []
            sym_upper = symbol.upper()
            if "USD" in sym_upper: target_currencies.append("USD")
            if "EUR" in sym_upper: target_currencies.append("EUR")
            if "GBP" in sym_upper: target_currencies.append("GBP")
            if "JPY" in sym_upper: target_currencies.append("JPY")
            if not target_currencies: target_currencies = ["USD"]

            filtered_blocks = []
            for idx, ev in enumerate(events, 1):
                curr = ev.get("currency", "USD")
                if curr not in target_currencies:
                    continue
                
                time_str = ev.get("time", "")
                name = ev.get("name", "")
                actual = ev.get("actual", 0.0)
                forecast = ev.get("forecast", 0.0)
                previous = ev.get("previous", 0.0)
                
                surprise = actual - forecast
                trend = actual - previous
                
                # Formatage structuré ligne par ligne pour une clarté maximale pour l'IA
                block = (
                    f"--- ÉVÉNEMENT MACROÉCONOMIQUE #{idx} ---\n"
                    f"• Indicateur : {name}\n"
                    f"• Devise : {curr}\n"
                    f"• Date/Heure : {time_str}\n"
                    f"• Valeur Réelle (Actual) : {actual}\n"
                    f"• Valeur Précédente (Previous) : {previous}\n"
                    f"• Prévision du Marché (Forecast) : {forecast}\n"
                    f"• Écart Surprise (Actual - Forecast) : {surprise:+.2f}\n"
                    f"• Momentum Tendance (Actual - Previous) : {trend:+.2f}\n"
                )
                filtered_blocks.append(block)
            
            if filtered_blocks:
                return "\n".join(filtered_blocks)
        except Exception as e:
            logger.warning(f"⚠️ Erreur de lecture du JSON calendar_news.json : {e}")

    if os.path.exists(NEWS_TXT_FILE):
        try:
            with open(NEWS_TXT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            logger.warning(f"⚠️ Lecture du fichier macro_news.txt impossible : {e}")
            
    return "Aucune donnée macroéconomique récente disponible dans le cache."
@app.get("/")
def root_status():
    """Endpoint de santé pour vérifier instantanément que le serveur répond (200 OK)."""
    return {"status": "online", "message": "TradeHub Unified Engine est opérationnel."}

@app.post("/analyze-chart")
async def analyze_chart(
    file: UploadFile = File(...), 
    symbol: str = Form("EURUSD"), 
    model_name: str = Form("gemma4:31b"), 
    mt5_state: str = Form("{}"),
    fundamental_context: str = Form(""),
    vision_detections: str = Form("[]")
):
    try:
        contents = await file.read()
        b64_image = base64.b64encode(contents).decode("utf-8")
        
        if not fundamental_context or fundamental_context == "Aucune donnée macroéconomique récente transmise.":
            news_context = load_macro_news_from_disk(symbol)
        else:
            news_context = fundamental_context

        prompt = f"""Tu es un analyste institutionnel expert en Smart Money Concepts (SMC), en Price Action et en analyse fondamentale macroéconomique pour le symbole {symbol}. 

DONNÉES OFFICIELLES DU CALENDRIER ÉCONOMIQUE MQL5 (À analyser strictement selon les devises concernées) :
{news_context}

Détections visuelles YOLOv8 ONNX : {vision_detections}
État technique, positions et ordres actifs de MT5 : {mt5_state}. 

CONSIGNES STRICTES :
1. Respecte rigoureusement la distinction des devises (ex: ne confonds pas les indicateurs USD et EUR).
2. Base ton analyse fondamentale uniquement sur les valeurs réelles (Actual), prévisions (Forecast) et précédentes (Previous) fournies ci-dessus dans les blocs d'événements.

Réponds STRICTEMENT au format JSON valide respectant cette structure exacte :
{{
    "status": "success",
    "symbol": "{symbol}",
    "trade_authorized": true,
    "bias": "BULLISH / BEARISH / NEUTRAL",
    "analyse_fondamentale": "Synthèse rigoureuse dissociant clairement l'impact des devises (USD/EUR) et s'appuyant explicitement sur les chiffres Actual, Forecast et Previous fournis.",
    "analyse_technique": "Synthèse des FVG, Order Blocks, structures et formations détectées.",
    "detections": {vision_detections},
    "gestion_ordres": [
        {{
            "ticket": 123,
            "action": "HOLD / MODIFY / CLOSE",
            "reasoning": "Justification de la gestion de l'ordre existant"
        }}
    ],
    "scenarios": {{
        "scenario_a": {{
            "titre": "Scénario Principal",
            "action": "SELL / BUY / WAIT",
            "entry_zone": "niveau ou fourchette",
            "stop_loss": "niveau de prix",
            "take_profit_1": "niveau cible 1",
            "risk_reward_ratio": "ex: 1:3"
        }},
        "scenario_b": {{
            "titre": "Scénario Alternatif",
            "action": "BUY / SELL / WAIT",
            "entry_zone": "niveau ou fourchette",
            "stop_loss": "niveau de prix",
            "take_profit_1": "niveau cible 1",
            "risk_reward_ratio": "ex: 1:2"
        }}
    }}
}}"""

        headers = {"Content-Type": "application/json"}
        if OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

        resp = requests.post(
            OLLAMA_ENDPOINT, 
            json={"model": model_name, "prompt": prompt, "images": [b64_image], "stream": False, "format": "json"}, 
            headers=headers,
            timeout=120
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            res_json = json.loads(match.group(0))
            if "detections" not in res_json or not res_json["detections"]:
                res_json["detections"] = json.loads(vision_detections) if vision_detections else []

            os.makedirs("data/cache", exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f: 
                json.dump(res_json, f, ensure_ascii=False, indent=4)
            log_analysis_to_db(res_json)
            return res_json
        else:
            raise ValueError(f"Format JSON introuvable dans la réponse : {raw}")
    except Exception as e: 
        logger.error(f"❌ Erreur API analyze-chart : {e}")
        raise HTTPException(status_code=500, detail=str(e))
# ==========================================
# 5. EXÉCUTION DU CYCLE DU BOT
# ==========================================
def perform_trading_cycle():
    symbol = "EURUSD"
    print("\n" + "="*80)
    print(f"🕒 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Début d'un nouveau cycle...")
    print("="*80)
    
    purge_expired_pending_orders(symbol=symbol, max_age_seconds=7200)

    print("📊 Analyse du calendrier économique et des fondamentaux...")
    try:
        fund_analysis = FundamentalEngine.analyze_news_impact(symbol)
        print(f"[Fundamental] Biais macro : {fund_analysis.get('fundamental_bias')} (Score USD: {fund_analysis.get('weighted_score_usd', 0)})")
        fundamental_text = fund_analysis.get("formatted_prompt_text", "")
    except Exception as e:
        print(f"[Fundamental Error] : {e}")
        fundamental_text = ""

    print("🔄 Capture du graphique et analyse YOLO Vision ONNX...")
    image_path, vision_detections = generate_live_chart(symbol=symbol, num_candles=300)
    if not image_path: 
        return None
    
    state = get_mt5_state(symbol)
    
    print("🚀 Envoi au serveur d'analyse (Gemma + Vision + Fondamental)...")
    with open(image_path, "rb") as f:
        resp = requests.post(
            "http://127.0.0.1:8001/analyze-chart", 
            files={"file": ("chart.png", f, "image/png")}, 
            data={
                "symbol": symbol, 
                "model_name": "gemma4:31b", 
                "mt5_state": json.dumps(state),
                "fundamental_context": fundamental_text,
                "vision_detections": json.dumps(vision_detections)
            }
        )
        plan = resp.json()

    if plan.get("gestion_ordres"): 
        manage_ai_orders(plan["gestion_ordres"], symbol)
        
    if not plan.get("trade_authorized"): 
        return plan

    scenarios = plan.get("scenarios", {})
    print("\n🎯 Placement simultané des Scénarios A et B sur MetaTrader 5...")
    for key in ["scenario_a", "scenario_b"]:
        scen = scenarios.get(key, {})
        act = scen.get("action", "").upper()
        if act in ["BUY", "SELL"]:
            print(f"\n⚡ Préparation ordre {key.upper()} ({act}) :")
            execute_ai_trade({
                "symbol": symbol, 
                "plan_de_trading": {
                    "action": act, 
                    "entry_zone": scen.get("entry_zone"), 
                    "stop_loss": scen.get("stop_loss"), 
                    "take_profit": scen.get("take_profit_1")
                }
            })
    return plan

def run_trading_bot():
    print("🤖 Bot Semi-Auto Intelligent démarré (Vision YOLO ONNX + Mode Stealth + SQLite).")
    last_analysis_time = 0
    analysis_interval = 300  # 5 minutes

    while True:
        try:
            manage_stealth_virtual_stops(symbol="EURUSD")
            check_and_apply_breakeven(symbol="EURUSD")
            apply_atr_trailing_stop(symbol="EURUSD", timeframe=mt5.TIMEFRAME_M5, atr_length=14, multiplier=2.0)

            current_time = time.time()
            if current_time - last_analysis_time >= analysis_interval:
                perform_trading_cycle()
                last_analysis_time = current_time

        except requests.exceptions.ConnectionError:
            pass
        except Exception as e:
            print(f"❌ Erreur bot : {e}")
        
        time.sleep(5)

# ==========================================
# 6. DASHBOARD STREAMLIT UNIFIÉ
# ==========================================
STREAMLIT_CODE = r"""# -*- coding: utf-8 -*-
import sys
import time
import json
import os
import re
import sqlite3
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from PIL import Image

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

st.set_page_config(page_title="TradeAgent Hub - SMC Complete Dashboard", layout="wide")

st.sidebar.title("🎛️ Cauris Hub")
menu = st.sidebar.selectbox("Navigation", ["📈 Dashboard & Exécution SMC", "👁️ Analyse Vision IA & Captures", "📜 Historique & BDD", "📊 Vue d'ensemble du Système"])

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Configuration Graphique")
symbol = st.sidebar.text_input("Symbole", value="EURUSD")
tf_option = st.sidebar.selectbox("Timeframe Chart", ["M1", "M5", "M15", "H1"], index=1)
num_bars = st.sidebar.slider("Nombre de bougies", min_value=30, max_value=300, value=200)
auto_refresh = st.sidebar.checkbox("🔄 Auto-Refresh (360s)", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Paramètres d'Exécution")
order_volume = st.sidebar.number_input("Volume (Lots)", value=0.01, step=0.01)

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ Commandes & Filtres IA")

CONFIG_PATH = "data/cache/config.json"

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"multi_tf_enabled": True, "yolo_vision_enabled": True, "stealth_mode_enabled": False}

current_config = load_config()

stealth_toggle = st.sidebar.toggle("🥷 Mode Stealth (SL/TP Masqués)", value=current_config.get("stealth_mode_enabled", False))
if stealth_toggle != current_config.get("stealth_mode_enabled", False):
    current_config["stealth_mode_enabled"] = stealth_toggle
    os.makedirs("data/cache", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_config, f)
    st.sidebar.success(f"Mode Stealth : {'Activé 🟢' if stealth_toggle else 'Désactivé 🔴'}")

yolo_toggle = st.sidebar.toggle("👁️ Vision YOLO ONNX (ON/OFF)", value=current_config.get("yolo_vision_enabled", True))
if yolo_toggle != current_config.get("yolo_vision_enabled", True):
    current_config["yolo_vision_enabled"] = yolo_toggle
    os.makedirs("data/cache", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_config, f)
    st.sidebar.success(f"Vision YOLO : {'Activée 🟢' if yolo_toggle else 'Désactivée 🔴'}")

multi_tf_toggle = st.sidebar.toggle("Validation Multi-TF (H1)", value=current_config.get("multi_tf_enabled", True))
if multi_tf_toggle != current_config.get("multi_tf_enabled", True):
    current_config["multi_tf_enabled"] = multi_tf_toggle
    os.makedirs("data/cache", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_config, f)
    st.sidebar.success(f"Multi-TF : {'Activé 🟢' if multi_tf_toggle else 'Désactivé 🔴'}")

st.sidebar.markdown("---")
st.sidebar.subheader("✏️ Niveaux Manuels")
manual_res = st.sidebar.number_input("Résistance Manuelle", value=0.0, step=0.001)
manual_sup = st.sidebar.number_input("Support Manuel", value=0.0, step=0.001)

st.sidebar.markdown("---")
st.sidebar.subheader("👁️ Affichage des Calques SMC")
show_sr_auto = st.sidebar.checkbox("Supports & Résistances Auto", value=True)
show_fvg = st.sidebar.checkbox("Fair Value Gaps (FVG)", value=True)
show_ob = st.sidebar.checkbox("Order Blocks (OB)", value=True)
show_plan_lines = st.sidebar.checkbox("Lignes du Plan IA (Entry/SL/TP)", value=True)

PLAN_FILE = "data/cache/last_analysis.json"
CHART_PATH = "data/cache/charts/EURUSD_M5_pro.png"
DB_PATH = "data/trading_hub.db"

live_plan = {}
if os.path.exists(PLAN_FILE):
    try:
        with open(PLAN_FILE, "r", encoding="utf-8") as f:
            live_plan = json.load(f)
    except Exception:
        pass

if menu == "📈 Dashboard & Exécution SMC":
    st.title("📈 TradeAgent Hub — Dashboard SMC & Exécution Live")

    st.subheader("📲 Validation & Exécution du Plan de Trading")
    scenarios = live_plan.get("scenarios", {})

    if scenarios:
        col_scen1, col_scen2 = st.columns(2)
        for i, key in enumerate(["scenario_a", "scenario_b"]):
            scen = scenarios.get(key, {})
            act = scen.get("action", "WAIT").upper()
            ent = scen.get("entry_zone", "0.0")
            sl = scen.get("stop_loss", "0.0")
            tp = scen.get("take_profit_1", "0.0")
            titre = scen.get("titre", "")
            
            with (col_scen1 if i == 0 else col_scen2):
                if act in ["BUY", "SELL"]:
                    badge_color = "🟢" if act == "BUY" else "🔴"
                    st.success(f"### {badge_color} {key.upper()} : {act} \n"
                               f"**{titre}**\n\n"
                               f"* **Entry POi** : `{ent}` | **SL** : `{sl}` | **TP** : `{tp}`\n\n"
                               f"📌 **Condition** : {scen.get('condition', '')}")
                    
                    if st.button(f"🚀 EXÉCUTER L'ORDRE {act} SUR MT5", key=f"btn_{key}", width="stretch", type="primary"):
                        st.info("Appel à l'API MT5 envoyé.")
                else:
                    st.warning(f"### ⏸️ {key.upper()} : {act}\nAucune action recommandée.")
    else:
        st.info("⏳ En attente d'un nouveau plan transmis par l'agent...")

    gestion = live_plan.get("gestion_ordres", [])
    if gestion:
        st.markdown("---")
        st.subheader("🛡️ Clôture / Annulation Anticipée Recommandée")
        for g in gestion:
            t_id = g.get("ticket")
            act = g.get("action")
            reason = g.get("reasoning", "")
            if act in ["CLOSE_POSITION", "CANCEL_ORDER"]:
                col_txt, col_act = st.columns([3, 1])
                with col_txt:
                    st.warning(f"⚠️ **Ticket #{t_id}** : Recommandation -> **{act}** ({reason})")
                with col_act:
                    if st.button(f"⚡ Exécuter {act}", key=f"close_{t_id}", type="primary"):
                        if act == "CANCEL_ORDER":
                            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": t_id})
                        elif act == "CLOSE_POSITION":
                            pos = mt5.positions_get(ticket=t_id)
                            if pos:
                                p = pos[0]
                                c_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
                                price = mt5.symbol_info_tick(symbol).bid if c_type == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(symbol).ask
                                mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "position": t_id, "symbol": symbol, "volume": p.volume, "type": c_type, "price": price, "magic": 234000})
                        st.success(f"✅ Action {act} exécutée sur MT5 pour le ticket #{t_id}")

    df = None
    tf_map = {"M1": 1, "M5": 5, "M15": 15, "H1": 16385}
    if MT5_AVAILABLE and mt5.initialize():
        rates = mt5.copy_rates_from_pos(symbol, tf_map.get(tf_option, 5), 0, num_bars)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.rename(columns={'time': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)

    if df is not None:
        st.markdown("---")
        fig = go.Figure(data=[go.Candlestick(
            x=df["Date"], open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350", name=symbol
        )])
        shapes, annotations = [], []

        if manual_res > 0: fig.add_hline(y=manual_res, line_dash="dash", line_color="#ff1744", line_width=1.5, annotation_text=f"RES MANUELLE")
        if manual_sup > 0: fig.add_hline(y=manual_sup, line_dash="dash", line_color="#00e676", line_width=1.5, annotation_text=f"SUP MANUEL")

        if show_sr_auto and len(df) > 10:
            for i in range(3, len(df) - 3):
                if (df["High"].iloc[i] > df["High"].iloc[i-1] and df["High"].iloc[i] > df["High"].iloc[i-2] and df["High"].iloc[i] > df["High"].iloc[i+1] and df["High"].iloc[i] > df["High"].iloc[i+2]):
                    shapes.append(dict(type="line", xref="x", yref="y", x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1], y0=df["High"].iloc[i], y1=df["High"].iloc[i], line=dict(color="#ff5252", width=1, dash="dot")))
                if (df["Low"].iloc[i] < df["Low"].iloc[i-1] and df["Low"].iloc[i] < df["Low"].iloc[i-2] and df["Low"].iloc[i] < df["Low"].iloc[i+1] and df["Low"].iloc[i] < df["Low"].iloc[i+2]):
                    shapes.append(dict(type="line", xref="x", yref="y", x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1], y0=df["Low"].iloc[i], y1=df["Low"].iloc[i], line=dict(color="#69f0ae", width=1, dash="dot")))

        if show_fvg and len(df) > 3:
            for i in range(2, len(df)):
                if df["Low"].iloc[i] > df["High"].iloc[i-2]:
                    shapes.append(dict(type="rect", xref="x", yref="y", x0=df["Date"].iloc[i-2], x1=df["Date"].iloc[-1], y0=df["High"].iloc[i-2], y1=df["Low"].iloc[i], fillcolor="rgba(38, 166, 154, 0.18)", line=dict(width=0)))
                elif df["High"].iloc[i] < df["Low"].iloc[i-2]:
                    shapes.append(dict(type="rect", xref="x", yref="y", x0=df["Date"].iloc[i-2], x1=df["Date"].iloc[-1], y0=df["High"].iloc[i], y1=df["Low"].iloc[i-2], fillcolor="rgba(239, 83, 80, 0.18)", line=dict(width=0)))

        if show_ob and len(df) > 4:
            for i in range(1, len(df) - 2):
                if (df["Close"].iloc[i] < df["Open"].iloc[i]) and (df["Close"].iloc[i+1] > df["High"].iloc[i]):
                    shapes.append(dict(type="rect", xref="x", yref="y", x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1], y0=df["Low"].iloc[i], y1=df["High"].iloc[i], fillcolor="rgba(41, 121, 255, 0.22)", line=dict(color="#2979ff", width=1)))
                elif (df["Close"].iloc[i] > df["Open"].iloc[i]) and (df["Close"].iloc[i+1] < df["Low"].iloc[i]):
                    shapes.append(dict(type="rect", xref="x", yref="y", x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1], y0=df["Low"].iloc[i], y1=df["High"].iloc[i], fillcolor="rgba(255, 109, 0, 0.22)", line=dict(color="#ff6d00", width=1)))

        if show_plan_lines and scenarios:
            for key in ["scenario_a", "scenario_b"]:
                s = scenarios.get(key, {})
                matches = re.findall(r"[\d]*[.,][\d]+|\d+", str(s.get('entry_zone', '')))
                if matches:
                    entry_val = float(matches[0].replace(',', '.'))
                    fig.add_hline(y=entry_val, line_dash="dash", line_color="gold", line_width=2, annotation_text=f"ENTRY ({key[-1].upper()})")

        fig.update_layout(title=f"SMC Full Chart — {symbol} ({tf_option}) | Multi-Calques", template="plotly_dark", xaxis_rangeslider_visible=False, height=580, shapes=shapes, annotations=annotations, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")

        if auto_refresh:
            time.sleep(5)
            st.rerun()

elif menu == "👁️ Analyse Vision IA & Captures":
    st.title("👁️ Inspection de la Vision IA (YOLO ONNX + Gemma)")
    col_img, col_meta = st.columns([1.5, 1])
    
    with col_img:
        st.markdown("### 📸 Capture Graphique Multidimensionnelle")
        if os.path.exists(CHART_PATH):
            st.image(Image.open(CHART_PATH), width="stretch", caption="Image soumise à l'IA avec détections YOLO")
        else:
            st.warning("Aucune capture trouvée.")
            
    with col_meta:
        st.markdown("### 🧠 Métadonnées & Analyse IA")
        if live_plan:
            st.metric("Biais Global (Bias)", live_plan.get("bias", "N/A"))
            
            detections = live_plan.get("detections", [])
            if detections:
                st.markdown("#### 📐 Détections Vision YOLO ONNX")
                for det in detections:
                    label = det.get("label", "Pattern")
                    conf = det.get("confidence", 0.0)
                    reason = det.get("reasoning", "")
                    st.info(f"**{label}** (Confiance: `{conf*100:.0f}%`)\n\n_{reason}_")

            st.markdown("#### 🌍 Analyse Fondamentale")
            st.write(live_plan.get("analyse_fondamentale", "N/A"))
            st.markdown("#### 📉 Analyse Technique")
            st.write(live_plan.get("analyse_technique", "N/A"))
            
            gestion = live_plan.get("gestion_ordres", [])
            if gestion:
                st.markdown("#### 🛡️ Anticipation & Gestion")
                for g in gestion:
                    st.warning(f"**Ticket {g.get('ticket')}** : {g.get('action')}")
            
            with st.expander("🔍 Voir le JSON brut"):
                st.json(live_plan)

elif menu == "📜 Historique & BDD":
    st.title("📜 Historique SQLite du Hub")
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        st.subheader("🤖 Historique des Analyses IA")
        df_analyses = pd.read_sql_query("SELECT id, timestamp, symbol, bias, analyse_fondamentale FROM analyses_history ORDER BY id DESC LIMIT 20", conn)
        st.dataframe(df_analyses, width="stretch")
        
        st.subheader("⚡ Historique des Ordres & Événements MT5")
        df_trades = pd.read_sql_query("SELECT id, timestamp, symbol, action_type, ticket, price, sl, tp, details FROM trades_history ORDER BY id DESC LIMIT 50", conn)
        st.dataframe(df_trades, width="stretch")
        conn.close()
    else:
        st.info("Base de données SQLite non initialisée.")

elif menu == "📊 Vue d'ensemble du Système":
    st.title("📊 Vue d’ensemble du Hub")
    col1, col2, col3 = st.columns(3)
    col1.metric("Moteur IA", "Gemma 4:31b + YOLOv8 ONNX", "Connecté")
    col2.metric("Base SQLite", "Active", "trading_hub.db")
    col3.metric("Cadence Scan", "5 Min", "Opérationnel")
    
    st.markdown("---")
    st.markdown("### Activité du Hub")
    st.info("Le système capture le graphique toutes les 5 minutes, analyse la vision YOLO ONNX, combine les bougies et figures chartistes, gère le risque en continu (5s), enregistre tout dans SQLite et place deux scénarios (A & B) en attente sur MetaTrader 5.")
"""

# ==========================================
# 7. POINT D'ENTRÉE UNIQUE DU HUB
# ==========================================
if __name__ == "__main__":
    print("🚀 Démarrage du Trade Agent Hub Unifié (Mode Stealth + YOLO ONNX)...")
    
    server_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "trade_hub:app", "--host", "127.0.0.1", "--port", "8001", "--log-level", "warning"])
    
    streamlit_file = "dashboard_app.py"
    with open(streamlit_file, "w", encoding="utf-8") as f: 
        f.write(STREAMLIT_CODE)
    
    streamlit_process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", streamlit_file, "--server.port", "8501", "--server.headless", "true"])
    
    def cleanup():
        print("\n🛑 Arrêt propre des services (API & Streamlit)...")
        try:
            server_process.terminate()
            streamlit_process.terminate()
        except Exception:
            pass

    atexit.register(cleanup)
    time.sleep(4)

    try:
        run_trading_bot()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt manuel du Trade Hub.")
        sys.exit(0)