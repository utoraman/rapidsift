"""Vercel serverless function: live NYSE/NASDAQ market status."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, date, time as dtime
import json

try:
    import pytz
    ET = pytz.timezone("US/Eastern")
except ImportError:
    ET = None

MARKET_HOLIDAYS = {
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
}

EARLY_CLOSE_HOLIDAYS = {
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 7, 2), date(2026, 11, 27), date(2026, 12, 24),
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if ET:
            now_et = datetime.now(ET)
        else:
            from datetime import timezone, timedelta
            now_et = datetime.now(timezone(timedelta(hours=-4)))

        today = now_et.date()
        t = now_et.time()
        weekday = now_et.weekday()

        market_open = dtime(9, 30)
        market_close = dtime(16, 0)
        early_close = dtime(13, 0)

        if weekday >= 5:
            nyse_open = False
            reason = "Weekend"
        elif today in MARKET_HOLIDAYS:
            nyse_open = False
            reason = "Holiday"
        elif today in EARLY_CLOSE_HOLIDAYS and t >= early_close:
            nyse_open = False
            reason = "Early close"
        elif t < market_open or t >= market_close:
            nyse_open = False
            reason = "After hours" if t >= market_close else "Pre-market"
        else:
            nyse_open = True
            reason = ""

        body = json.dumps({
            "nyse_open": nyse_open,
            "nasdaq_open": nyse_open,
            "reason": reason,
            "time_et": now_et.strftime("%H:%M:%S"),
            "date_et": now_et.strftime("%Y-%m-%d"),
        })

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())
