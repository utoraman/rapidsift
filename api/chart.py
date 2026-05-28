"""Vercel serverless function: live chart SVG generation.

Fetches price data from Yahoo Finance on-the-fly, computes technical
indicators, and returns an SVG chart matching the RapidSift design system.

Query params:
    ticker   – stock ticker symbol (required)
    type     – "line" or "candlestick" (default: "line")
    days     – 30, 60, 90, 180, 365 (default: 60)
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import math
import traceback


def _fetch_prices(ticker: str, days: int):
    """Fetch daily OHLCV data from Yahoo Finance. Returns a pandas DataFrame."""
    import yfinance as yf
    import pandas as pd

    period_map = {30: "1mo", 60: "3mo", 90: "3mo", 180: "6mo", 365: "1y"}
    period = period_map.get(days, "3mo")

    t = yf.Ticker(ticker)
    df = t.history(period=period, interval="1d")
    if df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()

    # Trim to requested number of days
    if len(df) > days:
        df = df.iloc[-days:]

    return df.reset_index(drop=True)


def _compute_rsi(closes, period=14):
    """Compute RSI from a list of close prices. Returns list of same length (NaN-padded)."""
    import math
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
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))
    return rsi


def _compute_sma(closes, period):
    """Simple moving average. Returns list of same length (NaN-padded)."""
    sma = [float('nan')] * len(closes)
    for i in range(period - 1, len(closes)):
        sma[i] = sum(closes[i - period + 1:i + 1]) / period
    return sma


def _build_svg(df, chart_type="line"):
    """Build SVG chart string from DataFrame. Standalone — no external deps beyond pandas."""
    import pandas as pd

    # ── Colors ──
    C_GRID = "oklch(0.30 0.010 250 / 0.25)"
    C_LINE = "oklch(0.30 0.010 250 / 0.45)"
    C_TEXT_MUTE = "oklch(0.55 0.010 250)"
    C_TEXT_DIM = "oklch(0.74 0.008 250)"
    C_ACCENT = "oklch(0.80 0.135 200)"
    C_UP = "oklch(0.80 0.165 145)"
    C_DOWN = "oklch(0.72 0.185 25)"
    C_WARN = "oklch(0.83 0.150 80)"
    C_RSI = "oklch(0.72 0.12 300)"
    FONT = "'JetBrains Mono', ui-monospace, monospace"

    W, H = 1100, 580
    PL, PR, PT = 62, 20, 12
    PB = 28
    GAP = 8
    PRICE_H = 320
    RSI_H = 100
    VOL_H = 90

    price_top = PT
    price_bot = price_top + PRICE_H
    rsi_top = price_bot + GAP
    rsi_bot = rsi_top + RSI_H
    vol_top = rsi_bot + GAP
    vol_bot = vol_top + VOL_H
    CW = W - PL - PR

    n = len(df)
    if n < 2:
        return '<p class="no-data">No price data available.</p>'

    dates = df["date"].tolist()
    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    price_min = min(lows) * 0.995
    price_max = max(highs) * 1.005
    price_range = price_max - price_min or 1
    vol_max = max(volumes) * 1.1 or 1

    def px(i):
        return round(PL + (i + 0.5) / n * CW, 1)

    def py_price(v):
        return round(price_top + PRICE_H * (1 - (v - price_min) / price_range), 1)

    def py_rsi(v):
        return round(rsi_top + RSI_H * (1 - v / 100), 1)

    def py_vol(v):
        return round(vol_top + VOL_H * (1 - v / vol_max), 1)

    bar_w = max(1, round(CW / n * 0.65, 1))

    svg = [f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto;display:block;font-family:{FONT}" xmlns="http://www.w3.org/2000/svg">']

    svg.append('<defs>')
    svg.append('<filter id="glow-up" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
    svg.append('<filter id="glow-down" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
    svg.append(f'<linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="{C_ACCENT}" stop-opacity=".15"/><stop offset="100%" stop-color="{C_ACCENT}" stop-opacity="0"/></linearGradient>')
    svg.append('</defs>')

    # Price grid
    raw_step = price_range / 5
    mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
    step = round(raw_step / mag) * mag or mag
    start = math.floor(price_min / step) * step
    v = start
    while v <= price_max:
        if v >= price_min:
            y = py_price(v)
            svg.append(f'<line x1="{PL}" x2="{W - PR}" y1="{y}" y2="{y}" stroke="{C_GRID}" stroke-width="0.5"/>')
            svg.append(f'<text x="{PL - 6}" y="{y + 3.5}" text-anchor="end" font-size="9" fill="{C_TEXT_MUTE}">${v:,.0f}</text>')
        v += step

    # RSI grid
    for rv in (0, 30, 50, 70, 100):
        y = py_rsi(rv)
        dash = ' stroke-dasharray="3,3"' if rv in (30, 70) else ""
        color = C_UP if rv == 30 else C_DOWN if rv == 70 else C_GRID
        opacity = "0.5" if rv in (30, 70) else "0.3"
        svg.append(f'<line x1="{PL}" x2="{W - PR}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="0.5" opacity="{opacity}"{dash}/>')
        if rv in (30, 70):
            svg.append(f'<text x="{PL - 6}" y="{y + 3.5}" text-anchor="end" font-size="8" fill="{C_TEXT_MUTE}">{rv}</text>')

    # Panel labels
    svg.append(f'<text x="{PL}" y="{price_top + 11}" font-size="9.5" font-weight="500" fill="{C_TEXT_DIM}" letter-spacing="0.08em">PRICE</text>')
    svg.append(f'<text x="{PL}" y="{rsi_top + 11}" font-size="9.5" font-weight="500" fill="{C_TEXT_DIM}" letter-spacing="0.08em">RSI (14)</text>')
    svg.append(f'<text x="{PL}" y="{vol_top + 11}" font-size="9.5" font-weight="500" fill="{C_TEXT_DIM}" letter-spacing="0.08em">VOLUME</text>')

    # Panel separators
    svg.append(f'<line x1="{PL}" x2="{W - PR}" y1="{price_bot}" y2="{price_bot}" stroke="{C_LINE}" stroke-width="0.5"/>')
    svg.append(f'<line x1="{PL}" x2="{W - PR}" y1="{rsi_bot}" y2="{rsi_bot}" stroke="{C_LINE}" stroke-width="0.5"/>')

    # SMAs
    sma20 = _compute_sma(closes, 20)
    sma50 = _compute_sma(closes, 50)

    def _sma_path(sma_list, color, dash=""):
        pts = []
        for i, v in enumerate(sma_list):
            if not math.isnan(v):
                pts.append(f"{px(i)},{py_price(v)}")
        if not pts:
            return ""
        d_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.3" opacity="0.8"{d_attr}/>'

    svg.append(_sma_path(sma50, C_ACCENT, "4,3"))
    svg.append(_sma_path(sma20, C_WARN))

    # Price chart
    if chart_type == "line":
        pts = [f"{px(i)},{py_price(closes[i])}" for i in range(n)]
        area_pts = pts + [f"{px(n-1)},{price_bot}", f"{px(0)},{price_bot}"]
        svg.append(f'<polygon points="{" ".join(area_pts)}" fill="url(#area-fill)"/>')
        svg.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C_ACCENT}" stroke-width="1.5" stroke-linejoin="round"/>')
        lx, ly = px(n - 1), py_price(closes[-1])
        svg.append(f'<circle cx="{lx}" cy="{ly}" r="4" fill="{C_ACCENT}" opacity=".18"/>')
        svg.append(f'<circle cx="{lx}" cy="{ly}" r="2" fill="{C_ACCENT}"/>')
    else:
        for i in range(n):
            x = px(i)
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            is_up = c >= o
            color = C_UP if is_up else C_DOWN
            body_top = py_price(max(o, c))
            body_bot = py_price(min(o, c))
            body_h = max(1, body_bot - body_top)
            wick_top = py_price(h)
            wick_bot = py_price(l)
            svg.append(f'<line x1="{x}" x2="{x}" y1="{wick_top}" y2="{wick_bot}" stroke="{color}" stroke-width="0.8"/>')
            bx = round(x - bar_w / 2, 1)
            svg.append(f'<rect x="{bx}" y="{body_top}" width="{bar_w}" height="{body_h}" fill="{color}" rx="0.5"/>')

    # RSI
    rsi = _compute_rsi(closes, 14)
    rsi_pts = []
    for i, v in enumerate(rsi):
        if not math.isnan(v):
            rsi_pts.append(f"{px(i)},{py_rsi(v)}")
    if rsi_pts:
        svg.append(f'<polyline points="{" ".join(rsi_pts)}" fill="none" stroke="{C_RSI}" stroke-width="1.3" stroke-linejoin="round"/>')
        # Find last valid RSI for halo
        for i in range(len(rsi) - 1, -1, -1):
            if not math.isnan(rsi[i]):
                rlx, rly = px(i), py_rsi(rsi[i])
                svg.append(f'<circle cx="{rlx}" cy="{rly}" r="3.5" fill="{C_RSI}" opacity=".18"/>')
                svg.append(f'<circle cx="{rlx}" cy="{rly}" r="1.8" fill="{C_RSI}"/>')
                break

    # Volume
    vol_bar_w = max(1, round(CW / n * 0.55, 1))
    for i in range(n):
        x = px(i)
        v = volumes[i]
        y_top = py_vol(v)
        h = vol_bot - y_top
        if h < 0.5:
            continue
        is_up = closes[i] >= opens[i]
        color = C_UP if is_up else C_DOWN
        bx = round(x - vol_bar_w / 2, 1)
        svg.append(f'<rect x="{bx}" y="{y_top}" width="{vol_bar_w}" height="{h}" fill="{color}" opacity="0.3" rx="0.5"/>')

    # Date labels
    skip = max(1, n // 9)
    for i in range(0, n, skip):
        d = pd.Timestamp(dates[i])
        label = d.strftime("%b %d")
        x = px(i)
        svg.append(f'<text x="{x}" y="{vol_bot + 16}" text-anchor="middle" font-size="8.5" fill="{C_TEXT_MUTE}">{label}</text>')

    # Legend
    leg_x = W - PR
    leg_y = price_top + 4
    if chart_type == "line":
        legend_items = [(C_ACCENT, "Close", False)]
    else:
        legend_items = [(C_UP, "Up", False), (C_DOWN, "Down", False)]
    legend_items += [(C_WARN, "SMA20", False), (C_ACCENT, "SMA50", True)]

    lx = leg_x
    for color, label, dashed in reversed(legend_items):
        tw = len(label) * 5.5 + 22
        lx -= tw
        dash_attr = ' stroke-dasharray="3,2"' if dashed else ""
        svg.append(f'<line x1="{lx}" x2="{lx + 12}" y1="{leg_y + 4}" y2="{leg_y + 4}" stroke="{color}" stroke-width="1.3"{dash_attr}/>')
        svg.append(f'<text x="{lx + 16}" y="{leg_y + 7}" font-size="9" fill="{C_TEXT_MUTE}">{label}</text>')

    svg.append('</svg>')
    return "\n".join(svg)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            ticker = params.get("ticker", ["NVDA"])[0].upper().strip()
            chart_type = params.get("type", ["line"])[0]
            days = int(params.get("days", ["60"])[0])

            if chart_type not in ("line", "candlestick"):
                chart_type = "line"
            if days not in (30, 60, 90, 180, 365):
                days = 60

            df = _fetch_prices(ticker, days)
            if df.empty:
                body = '<p class="no-data">No price data available.</p>'
            else:
                body = _build_svg(df, chart_type)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(body.encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Error: {e}\n{traceback.format_exc()}".encode())
