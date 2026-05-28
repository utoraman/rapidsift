"""Vercel serverless function: live data refresh.

Fetches fresh price data from Yahoo Finance for the requested ticker,
runs signal detection, and returns JSON with signals + KPIs.

Query params:
    ticker – stock ticker symbol (default: all watchlist)
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import traceback


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


def _detect_signals(ticker, df):
    """Run signal detection on a DataFrame. Returns list of signal dicts."""
    import math
    signals = []
    if df.empty or len(df) < 2:
        return signals

    closes = df["close"].tolist()
    opens = df["open"].tolist()
    volumes = df["volume"].tolist()
    latest_close = closes[-1]
    prev_close = closes[-2]

    # --- Percent change (volatility-normalized) ---
    pct_change = ((latest_close - prev_close) / prev_close) * 100
    if len(df) >= 22:
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(max(1, len(closes)-20), len(closes))]
        mean_r = sum(returns) / len(returns)
        std = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
        if std > 0:
            z = pct_change / std
            if abs(z) >= 2.0:
                direction = "sell" if pct_change > 0 else "buy"
                signals.append({
                    "signal_type": "percent_change",
                    "direction": direction,
                    "detail": f"Daily move {pct_change:+.2f}% ({z:+.1f}σ)",
                })
    elif abs(pct_change) >= 3.0:
        direction = "sell" if pct_change > 0 else "buy"
        signals.append({
            "signal_type": "percent_change",
            "direction": direction,
            "detail": f"Daily move {pct_change:+.2f}%",
        })

    # --- RSI (transition-only) ---
    from api.chart import _compute_rsi
    rsi = _compute_rsi(closes, 14)
    if len(rsi) >= 2 and not math.isnan(rsi[-1]) and not math.isnan(rsi[-2]):
        if rsi[-1] <= 30 and rsi[-2] > 30:
            signals.append({"signal_type": "rsi", "direction": "buy", "detail": f"RSI={rsi[-1]:.1f} crossed below 30"})
        elif rsi[-1] >= 70 and rsi[-2] < 70:
            signals.append({"signal_type": "rsi", "direction": "sell", "detail": f"RSI={rsi[-1]:.1f} crossed above 70"})

    # --- MACD crossover ---
    if len(closes) >= 35:
        def _ema(data, period):
            ema = [data[0]]
            k = 2 / (period + 1)
            for i in range(1, len(data)):
                ema.append(data[i] * k + ema[-1] * (1 - k))
            return ema

        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
        signal_line = _ema(macd_line, 9)

        prev_diff = macd_line[-2] - signal_line[-2]
        curr_diff = macd_line[-1] - signal_line[-1]
        if prev_diff < 0 and curr_diff > 0:
            signals.append({"signal_type": "macd", "direction": "buy", "detail": f"MACD bullish crossover"})
        elif prev_diff > 0 and curr_diff < 0:
            signals.append({"signal_type": "macd", "direction": "sell", "detail": f"MACD bearish crossover"})

    # --- MA crossover ---
    from api.chart import _compute_sma
    if len(closes) >= 51:
        sma20 = _compute_sma(closes, 20)
        sma50 = _compute_sma(closes, 50)
        if not math.isnan(sma20[-1]) and not math.isnan(sma20[-2]) and not math.isnan(sma50[-1]) and not math.isnan(sma50[-2]):
            prev_d = sma20[-2] - sma50[-2]
            curr_d = sma20[-1] - sma50[-1]
            if prev_d < 0 and curr_d > 0:
                signals.append({"signal_type": "ma_crossover", "direction": "buy", "detail": "SMA20 crossed above SMA50"})
            elif prev_d > 0 and curr_d < 0:
                signals.append({"signal_type": "ma_crossover", "direction": "sell", "detail": "SMA20 crossed below SMA50"})

    # --- Volume spike ---
    if len(df) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        curr_vol = volumes[-1]
        if avg_vol > 0:
            ratio = curr_vol / avg_vol
            if ratio >= 2.0:
                direction = "buy" if latest_close < opens[-1] else "sell"
                signals.append({
                    "signal_type": "volume_spike",
                    "direction": direction,
                    "detail": f"Volume spike: {ratio:.1f}x average",
                })

    # Tag all signals with ticker and price
    for s in signals:
        s["ticker"] = ticker
        s["price"] = latest_close

    return signals


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            import yfinance as yf
            import pandas as pd

            params = parse_qs(urlparse(self.path).query)
            ticker = params.get("ticker", [None])[0]

            if ticker:
                tickers = [ticker.upper().strip()]
            else:
                # Refresh a subset for speed (top 50 tickers)
                tickers = WATCHLIST[:50]

            all_signals = []
            errors = []
            prices_summary = {}

            for t in tickers:
                try:
                    yf_ticker = yf.Ticker(t)
                    df = yf_ticker.history(period="3mo", interval="1d")
                    if df.empty:
                        continue

                    df = df.reset_index()
                    df = df.rename(columns={
                        "Date": "date", "Open": "open", "High": "high",
                        "Low": "low", "Close": "close", "Volume": "volume",
                    })
                    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
                    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()

                    sigs = _detect_signals(t, df)
                    all_signals.extend(sigs)

                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else df.iloc[0]
                    change_pct = ((latest["close"] - prev["close"]) / prev["close"]) * 100
                    prices_summary[t] = {
                        "price": round(latest["close"], 2),
                        "change_pct": round(change_pct, 2),
                        "volume": int(latest["volume"]),
                    }
                except Exception as e:
                    errors.append(f"{t}: {e}")

            buy_signals = [s for s in all_signals if s["direction"] == "buy"]
            sell_signals = [s for s in all_signals if s["direction"] == "sell"]

            result = {
                "status": "ok",
                "tickers_refreshed": len(prices_summary),
                "total_signals": len(all_signals),
                "buy_signals": len(buy_signals),
                "sell_signals": len(sell_signals),
                "signals": all_signals,
                "prices": prices_summary,
                "errors": errors,
            }

            body = json.dumps(result)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body.encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode())

    def do_GET(self):
        """Allow GET for easy testing."""
        self.do_POST()
