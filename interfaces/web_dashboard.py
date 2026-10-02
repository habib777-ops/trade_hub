import sys
import time
from pathlib import Path

# Fixation du chemin racine
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# Tentative d'importation de MetaTrader 5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

st.set_page_config(page_title="TradeAgent Hub - Dashboard Live SMC", layout="wide")
st.title("📈 TradeAgent Hub - SMC Chart (FVG, OB, Breakers & Liquidity Sweeps)")

# Sidebar - Configuration
symbol = st.sidebar.text_input("Symbole", value="XAUUSD")
tf_option = st.sidebar.selectbox("Timeframe", ["M1", "M5", "M15", "H1", "D1"], index=1)
num_bars = st.sidebar.slider("Nombre de bougies", min_value=30, max_value=300, value=100)

auto_refresh = st.sidebar.checkbox("🔄 Auto-Refresh (5s)", value=True)

st.sidebar.markdown("---")
st.sidebar.subheader("👁️ Visibilité SMC")
show_fvg = st.sidebar.checkbox("Fair Value Gaps (FVG)", value=True)
show_ob = st.sidebar.checkbox("Order Blocks (OB)", value=True)
show_bb = st.sidebar.checkbox("Breaker Blocks (BB)", value=True)
show_sweeps = st.sidebar.checkbox("Liquidity Sweeps (BSL/SSL)", value=True)

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Niveaux de Trading")
entry_price = st.sidebar.number_input("Prix d'entrée (Entry)", value=2650.0, step=0.5)
sl_price = st.sidebar.number_input("Stop Loss (SL)", value=2642.0, step=0.5)
tp_price = st.sidebar.number_input("Take Profit (TP)", value=2665.0, step=0.5)

tf_map = {
    "M1": getattr(mt5, "TIMEFRAME_M1", 1) if MT5_AVAILABLE else 1,
    "M5": getattr(mt5, "TIMEFRAME_M5", 5) if MT5_AVAILABLE else 5,
    "M15": getattr(mt5, "TIMEFRAME_M15", 15) if MT5_AVAILABLE else 15,
    "H1": getattr(mt5, "TIMEFRAME_H1", 16385) if MT5_AVAILABLE else 16385,
    "D1": getattr(mt5, "TIMEFRAME_D1", 16408) if MT5_AVAILABLE else 16408,
}

df = None
data_source = "Simulation"
open_positions = []
account_info = None

if MT5_AVAILABLE:
    if not mt5.initialize():
        st.sidebar.warning("⚠️ MT5 non connecté, passage en mode simulation.")
    else:
        acc_raw = mt5.account_info()
        if acc_raw is not None:
            account_info = acc_raw._asdict()

        rates = mt5.copy_rates_from_pos(symbol, tf_map[tf_option], 0, num_bars)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.rename(columns={'time': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
            data_source = f"MetaTrader 5 ({symbol} {tf_option})"
        
        pos_raw = mt5.positions_get(symbol=symbol)
        if pos_raw is not None and len(pos_raw) > 0:
            for p in pos_raw:
                open_positions.append({
                    "Ticket": p.ticket,
                    "Symbole": p.symbol,
                    "Type": "BUY" if p.type == 0 else "SELL",
                    "Volume": p.volume,
                    "Prix Ouverture": p.price_open,
                    "Prix Actuel": p.price_current,
                    "SL": p.sl,
                    "TP": p.tp,
                    "Profit ($)": p.profit
                })

if account_info is None:
    account_info = {
        "balance": 10000.0,
        "equity": 10250.50,
        "margin_free": 9800.00,
        "margin_level": 500.0,
        "currency": "USD"
    }

if df is None:
    np.random.seed(int(time.time()))
    dates = pd.date_range(end=pd.Timestamp.now(), periods=num_bars, freq="5min")
    close_prices = 2650.0 + np.cumsum(np.random.normal(0, 0.6, num_bars))
    high_prices = close_prices + np.random.uniform(0.3, 1.2, num_bars)
    low_prices = close_prices - np.random.uniform(0.3, 1.2, num_bars)
    open_prices = low_prices + np.random.uniform(0.1, 0.8, num_bars)
    df = pd.DataFrame({"Date": dates, "Open": open_prices, "High": high_prices, "Low": low_prices, "Close": close_prices})

# --- Section 1: Métriques du Compte ---
st.subheader("💳 Métriques du Compte Trading")
m1, m2, m3, m4 = st.columns(4)

balance = account_info.get("balance", 0.0)
equity = account_info.get("equity", 0.0)
margin_free = account_info.get("margin_free", 0.0)
margin_level = account_info.get("margin_level", 0.0)
currency = account_info.get("currency", "USD")
pnl_floating = equity - balance

m1.metric("Solde (Balance)", f"${balance:,.2f} {currency}")
m2.metric("Équité (Equity)", f"${equity:,.2f} {currency}", delta=f"{pnl_floating:,.2f} $")
m3.metric("Marge Libre (Free Margin)", f"${margin_free:,.2f} {currency}")
m4.metric("Niveau de Marge", f"{margin_level:.1f}%" if margin_level else "N/A")

st.caption(f"Source : **{data_source}** | Dernier rafraîchissement : `{pd.Timestamp.now().strftime('%H:%M:%S')}`")

# --- Section 2: Construction du Graphique ---
fig = go.Figure(data=[go.Candlestick(
    x=df["Date"],
    open=df["Open"],
    high=df["High"],
    low=df["Low"],
    close=df["Close"],
    increasing_line_color="#26a69a",
    decreasing_line_color="#ef5350",
    name=symbol
)])

shapes = []
annotations = []

# 1. Détection FVG
if show_fvg:
    for i in range(2, len(df)):
        if df["Low"].iloc[i] > df["High"].iloc[i-2]:
            fvg_low = df["High"].iloc[i-2]
            fvg_high = df["Low"].iloc[i]
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i-2], x1=df["Date"].iloc[-1],
                y0=fvg_low, y1=fvg_high,
                fillcolor="rgba(38, 166, 154, 0.15)", line=dict(width=0),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i-1], y=(fvg_low + fvg_high) / 2,
                text="Bullish FVG", showarrow=False, font=dict(size=8, color="#26a69a")
            ))
        elif df["High"].iloc[i] < df["Low"].iloc[i-2]:
            fvg_high = df["Low"].iloc[i-2]
            fvg_low = df["High"].iloc[i]
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i-2], x1=df["Date"].iloc[-1],
                y0=fvg_low, y1=fvg_high,
                fillcolor="rgba(239, 83, 80, 0.15)", line=dict(width=0),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i-1], y=(fvg_low + fvg_high) / 2,
                text="Bearish FVG", showarrow=False, font=dict(size=8, color="#ef5350")
            ))

# 2. Détection Order Blocks (OB) & Breaker Blocks (BB)
for i in range(1, len(df) - 3):
    is_bearish_candle = df["Close"].iloc[i] < df["Open"].iloc[i]
    strong_bullish = (df["Close"].iloc[i+1] > df["High"].iloc[i])
    
    if is_bearish_candle and strong_bullish:
        ob_low = df["Low"].iloc[i]
        ob_high = df["High"].iloc[i]
        
        is_broken = any(df["Close"].iloc[j] < ob_low for j in range(i+2, len(df)))
        
        if is_broken and show_bb:
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1],
                y0=ob_low, y1=ob_high,
                fillcolor="rgba(230, 81, 0, 0.35)", line=dict(color="#e65100", width=1, dash="dot"),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i], y=ob_low,
                text="Bearish Breaker", showarrow=False, font=dict(size=9, color="#e65100")
            ))
        elif not is_broken and show_ob:
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1],
                y0=ob_low, y1=ob_high,
                fillcolor="rgba(41, 121, 255, 0.25)", line=dict(color="#2979ff", width=1),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i], y=ob_high,
                text="Bullish OB", showarrow=False, font=dict(size=9, color="#2979ff")
            ))

    is_bullish_candle = df["Close"].iloc[i] > df["Open"].iloc[i]
    strong_bearish = (df["Close"].iloc[i+1] < df["Low"].iloc[i])
    
    if is_bullish_candle and strong_bearish:
        ob_low = df["Low"].iloc[i]
        ob_high = df["High"].iloc[i]
        
        is_broken = any(df["Close"].iloc[j] > ob_high for j in range(i+2, len(df)))
        
        if is_broken and show_bb:
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1],
                y0=ob_low, y1=ob_high,
                fillcolor="rgba(156, 39, 176, 0.35)", line=dict(color="#9c27b0", width=1, dash="dot"),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i], y=ob_high,
                text="Bullish Breaker", showarrow=False, font=dict(size=9, color="#9c27b0")
            ))
        elif not is_broken and show_ob:
            shapes.append(dict(
                type="rect", xref="x", yref="y",
                x0=df["Date"].iloc[i], x1=df["Date"].iloc[-1],
                y0=ob_low, y1=ob_high,
                fillcolor="rgba(255, 109, 0, 0.25)", line=dict(color="#ff6d00", width=1),
            ))
            annotations.append(dict(
                x=df["Date"].iloc[i], y=ob_low,
                text="Bearish OB", showarrow=False, font=dict(size=9, color="#ff6d00")
            ))

# 3. Algorithme de Détection des Liquidity Sweeps (BSL / SSL)
if show_sweeps and len(df) > 10:
    for i in range(5, len(df) - 1):
        prev_high = df["High"].iloc[i-5:i].max()
        prev_low = df["Low"].iloc[i-5:i].min()
        
        # BSL Sweep: Mèche au-dessus du sommet précédent, mais clôture en-dessous
        if df["High"].iloc[i] > prev_high and df["Close"].iloc[i] < prev_high:
            annotations.append(dict(
                x=df["Date"].iloc[i], y=df["High"].iloc[i] + 0.3,
                text="🧹 BSL Sweep", showarrow=True, arrowhead=2, ax=0, ay=-20,
                font=dict(size=9, color="#e040fb"), arrowcolor="#e040fb"
            ))
            shapes.append(dict(
                type="line", xref="x", yref="y",
                x0=df["Date"].iloc[i-5], x1=df["Date"].iloc[i],
                y0=prev_high, y1=prev_high,
                line=dict(color="#e040fb", width=1, dash="dashdot")
            ))

        # SSL Sweep: Mèche en-dessous du creux précédent, mais clôture au-dessus
        if df["Low"].iloc[i] < prev_low and df["Close"].iloc[i] > prev_low:
            annotations.append(dict(
                x=df["Date"].iloc[i], y=df["Low"].iloc[i] - 0.3,
                text="🧹 SSL Sweep", showarrow=True, arrowhead=2, ax=0, ay=20,
                font=dict(size=9, color="#00e676"), arrowcolor="#00e676"
            ))
            shapes.append(dict(
                type="line", xref="x", yref="y",
                x0=df["Date"].iloc[i-5], x1=df["Date"].iloc[i],
                y0=prev_low, y1=prev_low,
                line=dict(color="#00e676", width=1, dash="dashdot")
            ))

# Lignes de Trading
fig.add_hline(y=entry_price, line_dash="dash", line_color="gold", annotation_text="ENTRY", annotation_position="top left")
fig.add_hline(y=sl_price, line_dash="dash", line_color="red", annotation_text="STOP LOSS", annotation_position="bottom left")
fig.add_hline(y=tp_price, line_dash="dash", line_color="green", annotation_text="TAKE PROFIT", annotation_position="top left")

fig.update_layout(
    title=f"SMC Chart — {symbol} ({tf_option}) | Full SMC Suite (FVG, OB, Breakers, Sweeps)",
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    height=600,
    shapes=shapes,
    annotations=annotations,
    margin=dict(l=10, r=10, t=40, b=10)
)

st.plotly_chart(fig, use_container_width=True, key="xauusd_smc_full_chart")

# --- Section 3: Positions Ouvertes ---
st.markdown("---")
st.subheader("💼 Positions en Cours & PnL Temps Réel")

if open_positions:
    pos_df = pd.DataFrame(open_positions)
    total_pnl = pos_df["Profit ($)"].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Positions Actives", len(pos_df))
    col2.metric("P&L Total Cumulé ($)", f"${total_pnl:,.2f}", delta=f"{total_pnl:,.2f} $")
    col3.metric("Volume Total (Lots)", f"{pos_df['Volume'].sum():.2f}")
    
    st.dataframe(pos_df, use_container_width=True)
else:
    st.info("ℹ️ Aucune position actuellement ouverte sur MetaTrader 5.")

# Auto-refresh 5s
if auto_refresh:
    time.sleep(5)
    st.rerun()
