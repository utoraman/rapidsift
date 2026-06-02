#!/usr/bin/env python3
"""
RapidSift Telegram Notifier

Fetches fresh prices from Yahoo Finance, runs signal detection on all
watchlist tickers, and sends new signals to Telegram with chart images.

Requires env vars:
    TELEGRAM_BOT_TOKEN  — bot API token
    TELEGRAM_CHAT_ID    — recipient chat ID

Usage:
    python3 scripts/notify_telegram.py           # detect & notify
    python3 scripts/notify_telegram.py --test    # send a test message
"""

import os
import sys
import json
import math
import io
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import requests
import yfinance as yf
import pandas as pd

# ── Config ───────────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SITE_URL = "https://rapidsift.vercel.app"

STATE_FILE = Path(__file__).parent.parent / ".signal_state.json"

WATCHLIST = [
    "NVDA","INTC","PLUG","SOFI","AAL","TSLA","F","NU","SNAP","GRAB",
    "PLTR","MARA","AMZN","MU","NFLX","AAPL","OPEN","NIO","IREN","T",
    "BAC","AMD","PFE","SMCI","HIMS","BBAI","MSFT","DNN","CMCSA","PATH",
    "ACHR","BITF","HOOD","GOOGL","RGTI","SMR","VALE","SOUN","CCL","IONQ",
    "ORCL","RIVN","JOBY","CIFR","VZ","MRVL","AVGO","RKLB","RDW","WBD",
    "CSCO","NCLH","CPNG","NOW","NKE","XOM","BTBT","APLD","NVO","CLSK",
    "PINS","WMT","GOOG","MSTR","PYPL","RIOT","WFC","QS","OXY","FCX",
    "QCOM","UBER","CLF","SLB","BSX","LYFT","ET","ASTS","CMG","DVN",
    "KO","META","HAL","U","CORZ","C","TSM","LUNR","CRM","DKNG",
    "LCID","KMI","CSX","CVX","ABT","BMY","COIN","DAL","BABA","RBLX",
    "JD","SHOP","BE","SCHW","UUUU","TME","IQ","RUN","LRCX","PG",
    "DIS","ON","COP","MRK","JPM","NEM","PANW","UEC","BKR","MDT",
    "LUV","UNH","BX","CVS","MRNA","ARM","JNJ","CELH","UAL","TEAM",
    "SBUX","GM","V","TXN","PDD","KKR","SNOW","ACN","AMAT","BA",
    "TEVA","ABBV","CARR","FISV","XPEV","MS","ENPH","WMB","IBM","FIS",
    "UPS","PEP","GILD","FTNT","O","CL","GE","BBWI","TMUS","RTX",
    "MNST","AI","AA","AFRM","ADBE","DDOG","TGT","WDAY","NTLA","APO",
    "SE","DASH","APP","COF","UPST","EW","EOG","HUT","KMB","MGM",
    "FCEL","LIDR","NVAX","ZM","DXCM","GSK","NET","EPD","DOCU","TLRY",
    "EL","BROS","DHR","WOLF","EVGO","PENN","LVS","HD","SEDG","GFS",
]


# ── Signal Detection ─────────────────────────────────────────────────────────

def compute_rsi(closes, period=14):
    deltas = [0.0] + [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [-min(d, 0) for d in deltas]
    rsi = [float('nan')] * len(closes)
    if len(closes) < period + 1:
        return rsi
    avg_gain = sum(gains[1:period+1]) / period
    avg_loss = sum(losses[1:period+1]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100 - (100 / (1 + avg_gain / avg_loss))
    return rsi


def compute_sma(closes, period):
    sma = [float('nan')] * len(closes)
    for i in range(period - 1, len(closes)):
        sma[i] = sum(closes[i - period + 1:i + 1]) / period
    return sma


def compute_ema(data, period):
    ema = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema


def detect_signals(ticker, df):
    signals = []
    if df.empty or len(df) < 2:
        return signals

    closes = df["close"].tolist()
    opens = df["open"].tolist()
    volumes = df["volume"].tolist()
    latest_close = closes[-1]
    prev_close = closes[-2]
    latest_date = str(df["date"].iloc[-1])[:10]

    # Percent change (volatility-normalized)
    pct = ((latest_close - prev_close) / prev_close) * 100
    if len(df) >= 22:
        rets = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                for i in range(max(1, len(closes)-20), len(closes))]
        mean_r = sum(rets) / len(rets)
        std = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
        if std > 0 and abs(pct / std) >= 2.0:
            z = pct / std
            signals.append({
                "type": "percent_change",
                "direction": "sell" if pct > 0 else "buy",
                "detail": f"Daily move {pct:+.2f}% ({z:+.1f}σ)",
            })
    elif abs(pct) >= 3.0:
        signals.append({
            "type": "percent_change",
            "direction": "sell" if pct > 0 else "buy",
            "detail": f"Daily move {pct:+.2f}%",
        })

    # RSI transition
    rsi = compute_rsi(closes, 14)
    if len(rsi) >= 2 and not math.isnan(rsi[-1]) and not math.isnan(rsi[-2]):
        if rsi[-1] <= 30 and rsi[-2] > 30:
            signals.append({"type": "rsi", "direction": "buy",
                           "detail": f"RSI={rsi[-1]:.1f} crossed below 30"})
        elif rsi[-1] >= 70 and rsi[-2] < 70:
            signals.append({"type": "rsi", "direction": "sell",
                           "detail": f"RSI={rsi[-1]:.1f} crossed above 70"})

    # MACD crossover
    if len(closes) >= 35:
        ema12 = compute_ema(closes, 12)
        ema26 = compute_ema(closes, 26)
        macd = [ema12[i] - ema26[i] for i in range(len(closes))]
        sig_line = compute_ema(macd, 9)
        prev_d = macd[-2] - sig_line[-2]
        curr_d = macd[-1] - sig_line[-1]
        if prev_d < 0 and curr_d > 0:
            signals.append({"type": "macd", "direction": "buy", "detail": "MACD bullish crossover"})
        elif prev_d > 0 and curr_d < 0:
            signals.append({"type": "macd", "direction": "sell", "detail": "MACD bearish crossover"})

    # MA crossover
    if len(closes) >= 51:
        sma20 = compute_sma(closes, 20)
        sma50 = compute_sma(closes, 50)
        if not any(math.isnan(v) for v in [sma20[-1], sma20[-2], sma50[-1], sma50[-2]]):
            prev_d = sma20[-2] - sma50[-2]
            curr_d = sma20[-1] - sma50[-1]
            if prev_d < 0 and curr_d > 0:
                signals.append({"type": "ma_crossover", "direction": "buy", "detail": "SMA20 crossed above SMA50"})
            elif prev_d > 0 and curr_d < 0:
                signals.append({"type": "ma_crossover", "direction": "sell", "detail": "SMA20 crossed below SMA50"})

    # Volume spike
    if len(df) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        if avg_vol > 0 and volumes[-1] / avg_vol >= 2.0:
            ratio = volumes[-1] / avg_vol
            direction = "buy" if latest_close < opens[-1] else "sell"
            signals.append({"type": "volume_spike", "direction": direction,
                           "detail": f"Volume spike: {ratio:.1f}x average"})

    for s in signals:
        s["ticker"] = ticker
        s["price"] = latest_close
        s["date"] = latest_date

    return signals


# ── Chart SVG Builder ─────────────────────────────────────────────────────────

def build_chart_svg(df, ticker, chart_type="line"):
    """Build SVG chart string for Telegram. Returns SVG string."""
    C_BG = "#1a1d2e"
    C_GRID = "#2a2d3e"
    C_TEXT = "#8890a0"
    C_ACCENT = "#5bb8d4"
    C_UP = "#4ade80"
    C_DOWN = "#f87171"
    C_WARN = "#fbbf24"
    C_RSI = "#a78bfa"
    FONT = "'JetBrains Mono', monospace"

    W, H = 800, 420
    PL, PR, PT = 52, 16, 10
    GAP = 6
    PRICE_H = 230
    RSI_H = 70
    VOL_H = 65

    price_top = PT
    price_bot = price_top + PRICE_H
    rsi_top = price_bot + GAP
    rsi_bot = rsi_top + RSI_H
    vol_top = rsi_bot + GAP
    vol_bot = vol_top + VOL_H
    CW = W - PL - PR

    n = len(df)
    if n < 2:
        return None

    closes = df["close"].tolist()
    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    dates = df["date"].tolist()

    price_min = min(lows) * 0.995
    price_max = max(highs) * 1.005
    price_range = price_max - price_min or 1
    vol_max = max(volumes) * 1.1 or 1

    def px(i): return round(PL + (i + 0.5) / n * CW, 1)
    def py_price(v): return round(price_top + PRICE_H * (1 - (v - price_min) / price_range), 1)
    def py_rsi(v): return round(rsi_top + RSI_H * (1 - v / 100), 1)
    def py_vol(v): return round(vol_top + VOL_H * (1 - v / vol_max), 1)

    bar_w = max(1, round(CW / n * 0.65, 1))

    svg = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="font-family:{FONT}">']
    svg.append(f'<rect width="{W}" height="{H}" fill="{C_BG}" rx="8"/>')

    # Title
    last_price = closes[-1]
    prev_price = closes[-2]
    change = ((last_price - prev_price) / prev_price) * 100
    change_color = C_UP if change >= 0 else C_DOWN
    svg.append(f'<text x="{PL}" y="{PT + 2}" font-size="13" font-weight="700" fill="white">{ticker}</text>')
    svg.append(f'<text x="{PL + len(ticker)*9 + 8}" y="{PT + 2}" font-size="11" fill="{C_TEXT}">${last_price:,.2f}</text>')
    svg.append(f'<text x="{PL + len(ticker)*9 + 80}" y="{PT + 2}" font-size="11" fill="{change_color}">{change:+.2f}%</text>')

    pt_offset = 16
    price_top += pt_offset
    price_bot += pt_offset
    rsi_top += pt_offset
    rsi_bot += pt_offset
    vol_top += pt_offset
    vol_bot += pt_offset

    # Recalculate py functions with offset
    def py_price(v): return round(price_top + PRICE_H * (1 - (v - price_min) / price_range), 1)
    def py_rsi(v): return round(rsi_top + RSI_H * (1 - v / 100), 1)
    def py_vol(v): return round(vol_top + VOL_H * (1 - v / vol_max), 1)

    # Price grid
    raw_step = price_range / 4
    mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
    step = round(raw_step / mag) * mag or mag
    v = math.floor(price_min / step) * step
    while v <= price_max:
        if v >= price_min:
            y = py_price(v)
            svg.append(f'<line x1="{PL}" x2="{W-PR}" y1="{y}" y2="{y}" stroke="{C_GRID}" stroke-width="0.5"/>')
            svg.append(f'<text x="{PL-4}" y="{y+3}" text-anchor="end" font-size="8" fill="{C_TEXT}">${v:,.0f}</text>')
        v += step

    # RSI grid
    for rv in (30, 70):
        y = py_rsi(rv)
        color = C_UP if rv == 30 else C_DOWN
        svg.append(f'<line x1="{PL}" x2="{W-PR}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="0.5" opacity="0.4" stroke-dasharray="3,3"/>')

    # Panel separators
    svg.append(f'<line x1="{PL}" x2="{W-PR}" y1="{price_bot}" y2="{price_bot}" stroke="{C_GRID}" stroke-width="0.5"/>')
    svg.append(f'<line x1="{PL}" x2="{W-PR}" y1="{rsi_bot}" y2="{rsi_bot}" stroke="{C_GRID}" stroke-width="0.5"/>')

    # SMAs
    sma20 = compute_sma(closes, 20)
    sma50 = compute_sma(closes, 50)
    for sma, color, dash in [(sma50, C_ACCENT, "4,3"), (sma20, C_WARN, "")]:
        pts = [f"{px(i)},{py_price(v)}" for i, v in enumerate(sma) if not math.isnan(v)]
        if pts:
            d = f' stroke-dasharray="{dash}"' if dash else ""
            svg.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1" opacity="0.7"{d}/>')

    # Price line
    if chart_type == "line":
        pts = [f"{px(i)},{py_price(closes[i])}" for i in range(n)]
        svg.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C_ACCENT}" stroke-width="1.5"/>')
        lx, ly = px(n-1), py_price(closes[-1])
        svg.append(f'<circle cx="{lx}" cy="{ly}" r="3" fill="{C_ACCENT}"/>')
    else:
        for i in range(n):
            x = px(i)
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            color = C_UP if c >= o else C_DOWN
            svg.append(f'<line x1="{x}" x2="{x}" y1="{py_price(h)}" y2="{py_price(l)}" stroke="{color}" stroke-width="0.7"/>')
            bt, bb = py_price(max(o,c)), py_price(min(o,c))
            bx = round(x - bar_w/2, 1)
            svg.append(f'<rect x="{bx}" y="{bt}" width="{bar_w}" height="{max(1, bb-bt)}" fill="{color}"/>')

    # RSI
    rsi = compute_rsi(closes, 14)
    rsi_pts = [f"{px(i)},{py_rsi(v)}" for i, v in enumerate(rsi) if not math.isnan(v)]
    if rsi_pts:
        svg.append(f'<polyline points="{" ".join(rsi_pts)}" fill="none" stroke="{C_RSI}" stroke-width="1.2"/>')

    # Volume
    vbw = max(1, round(CW / n * 0.5, 1))
    for i in range(n):
        v = volumes[i]
        y_top = py_vol(v)
        h = vol_bot - y_top
        if h < 0.5: continue
        color = C_UP if closes[i] >= opens[i] else C_DOWN
        svg.append(f'<rect x="{round(px(i)-vbw/2,1)}" y="{y_top}" width="{vbw}" height="{h}" fill="{color}" opacity="0.3"/>')

    # Date labels
    skip = max(1, n // 7)
    for i in range(0, n, skip):
        d = pd.Timestamp(dates[i])
        svg.append(f'<text x="{px(i)}" y="{vol_bot+12}" text-anchor="middle" font-size="7.5" fill="{C_TEXT}">{d.strftime("%b %d")}</text>')

    svg.append('</svg>')
    return "\n".join(svg)


# ── SVG to PNG ────────────────────────────────────────────────────────────────

def svg_to_png(svg_string):
    """Convert SVG string to PNG bytes using cairosvg."""
    import cairosvg
    return cairosvg.svg2png(bytestring=svg_string.encode("utf-8"),
                            output_width=1600, output_height=840)


# ── Telegram API ──────────────────────────────────────────────────────────────

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SIGNAL_LABELS = {
    "rsi": "RSI",
    "macd": "MACD",
    "ma_crossover": "MA Cross",
    "percent_change": "% Change",
    "volume_spike": "Vol Spike",
    "adjusted_drop": "Adj Drop",
    "adjusted_surge": "Adj Surge",
    "confluence": "Confluence",
}


def send_message(text):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def send_photo(png_bytes, caption):
    requests.post(f"{API}/sendPhoto",
        data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("chart.png", io.BytesIO(png_bytes), "image/png")},
    )


# ── State Management ─────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_signals": [], "last_run": ""}


def save_state(signals):
    state = {
        "last_signals": [f"{s['ticker']}_{s['type']}_{s['date']}" for s in signals],
        "last_run": datetime.utcnow().isoformat(),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))


def is_new_signal(sig, prev_keys):
    key = f"{sig['ticker']}_{sig['type']}_{sig['date']}"
    return key not in prev_keys


# ── Main ──────────────────────────────────────────────────────────────────────

def _is_market_hours():
    """Check if US markets are currently open (roughly 13:30-20:00 UTC weekdays)."""
    now = datetime.utcnow()
    if now.weekday() >= 5:
        return False
    hour_utc = now.hour + now.minute / 60
    return 13.5 <= hour_utc <= 20.0


def _overlay_intraday_single(ticker, daily_df):
    """Fetch live intraday data for a single ticker and overlay onto daily bars."""
    try:
        t = yf.Ticker(ticker)
        idf = t.history(period="1d", interval="5m")
        if idf.empty or len(idf) < 1:
            return daily_df

        idf = idf.reset_index()
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        today_open = float(idf["Open"].iloc[0])
        today_high = float(idf["High"].max())
        today_low = float(idf["Low"].min())
        today_close = float(idf["Close"].iloc[-1])
        today_vol = int(idf["Volume"].sum()) if "Volume" in idf.columns else 0

        last_date = str(daily_df["date"].iloc[-1])[:10]
        today_bar = pd.DataFrame([{
            "date": pd.Timestamp(today_str),
            "open": today_open, "high": today_high,
            "low": today_low, "close": today_close, "volume": today_vol,
        }])

        if last_date == today_str:
            return pd.concat([daily_df.iloc[:-1], today_bar], ignore_index=True)
        else:
            return pd.concat([daily_df, today_bar], ignore_index=True)
    except Exception:
        return daily_df


def run_detection():
    print(f"Scanning {len(WATCHLIST)} tickers...")
    market_open = _is_market_hours()
    if market_open:
        print("  Market is open — using live intraday prices")
    else:
        print("  Market is closed — using end-of-day data")

    all_signals = []
    errors = []

    for ticker in WATCHLIST:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period="3mo", interval="1d")
            if df.empty:
                errors.append(f"{ticker}: empty")
                continue
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[cols].dropna()
            if len(df) < 5:
                continue

            # Overlay live intraday price during market hours
            if market_open:
                df = _overlay_intraday_single(ticker, df)

            sigs = detect_signals(ticker, df)
            for s in sigs:
                s["_df"] = df
            all_signals.extend(sigs)

        except Exception as e:
            errors.append(f"{ticker}: {e}")

    if errors:
        print(f"  {len(errors)} errors: {errors[:5]}")

    return all_signals


def notify(signals, prev_keys):
    new_signals = [s for s in signals if is_new_signal(s, prev_keys)]
    # Only notify on buy signals
    new_signals = [s for s in new_signals if s["direction"] == "buy"]

    if not new_signals:
        print("No new buy signals.")
        return

    # Summary message
    summary = f"<b>🔔 RapidSift Alert</b> — {len(new_signals)} new buy signal{'s' if len(new_signals) != 1 else ''}\n"
    summary += f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</i>\n"
    send_message(summary)

    # Individual signal messages with charts
    for sig in new_signals:
        emoji = "🟢" if sig["direction"] == "buy" else "🔴"
        label = SIGNAL_LABELS.get(sig["type"], sig["type"])
        direction = sig["direction"].upper()

        caption = (
            f"{emoji} <b>{sig['ticker']}</b> — {direction}\n"
            f"<b>{label}</b>: {sig['detail']}\n"
            f"Price: <code>${sig['price']:,.2f}</code>\n"
            f"<a href=\"{SITE_URL}/?ticker={sig['ticker']}&signal_type={sig['type']}\">View on RapidSift →</a>"
        )

        # Try to send chart image
        df = sig.get("_df")
        if df is not None and len(df) >= 5:
            try:
                svg = build_chart_svg(df, sig["ticker"])
                if svg:
                    png = svg_to_png(svg)
                    send_photo(png, caption)
                    continue
            except Exception as e:
                print(f"  Chart render failed for {sig['ticker']}: {e}")

        # Fallback: text-only
        send_message(caption)

    print(f"Sent {len(new_signals)} notifications.")


def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        sys.exit(1)

    if "--test" in sys.argv:
        send_message(
            "🧪 <b>RapidSift Test</b>\n\n"
            "Bot is working! Signal notifications will appear here.\n"
            f"Monitoring {len(WATCHLIST)} tickers."
        )
        print("Test message sent.")
        return

    state = load_state()
    prev_keys = set(state.get("last_signals", []))

    all_signals = run_detection()
    print(f"Found {len(all_signals)} total signals ({len([s for s in all_signals if s['direction']=='buy'])} buy, "
          f"{len([s for s in all_signals if s['direction']=='sell'])} sell)")

    notify(all_signals, prev_keys)
    save_state(all_signals)
    print("Done.")


if __name__ == "__main__":
    main()
