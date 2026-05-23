from flask import Flask, render_template_string, request
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.utils
import json
import yaml
from pathlib import Path
from datetime import datetime, date
import pytz
from data.db import get_prices, get_signals, get_connection
from signals.technical import compute_rsi, compute_macd, compute_sma
from backtest.validator import signal_win_rates, signal_detail_report, random_baseline
from signals.engine import COOLDOWN_DAYS
from markupsafe import Markup

app = Flask(__name__)

# ── Market hours & holidays ─────────────────────────────────────────────────
ET = pytz.timezone("US/Eastern")

# NYSE & NASDAQ share the same schedule: Mon-Fri 9:30-16:00 ET, closed on holidays
# 2025-2026 US market holidays (NYSE/NASDAQ)
MARKET_HOLIDAYS = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
}

# Early close days (1:00 PM ET): day before Independence Day, after Thanksgiving, Christmas Eve
EARLY_CLOSE_HOLIDAYS = {
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 7, 2), date(2026, 11, 27), date(2026, 12, 24),
}


def get_market_status() -> dict:
    """Return real-time open/closed status for NYSE and NASDAQ."""
    now_et = datetime.now(ET)
    today = now_et.date()
    t = now_et.time()
    weekday = now_et.weekday()  # 0=Mon, 6=Sun

    is_weekend = weekday >= 5
    is_holiday = today in MARKET_HOLIDAYS
    is_early_close = today in EARLY_CLOSE_HOLIDAYS

    from datetime import time as dtime
    market_open = dtime(9, 30)
    market_close = dtime(16, 0)
    early_close = dtime(13, 0)

    if is_weekend or is_holiday:
        nyse_open = False
        reason = "Weekend" if is_weekend else "Holiday"
    elif is_early_close and t >= early_close:
        nyse_open = False
        reason = "Early close"
    elif t < market_open or t >= market_close:
        nyse_open = False
        reason = "After hours" if t >= market_close else "Pre-market"
    else:
        nyse_open = True
        reason = ""

    # NYSE and NASDAQ share the same schedule
    return {
        "nyse_open": nyse_open,
        "nasdaq_open": nyse_open,
        "reason": reason,
        "time_et": now_et.strftime("%H:%M:%S"),
        "date_et": now_et.strftime("%Y-%m-%d"),
    }


SIGNAL_TYPES = [
    ("all", "All Signals"),
    ("rsi", "RSI"),
    ("macd", "MACD Crossover"),
    ("ma_crossover", "Moving Average Crossover"),
    ("percent_change", "% Change"),
    ("volume_spike", "Volume Spike"),
    ("adjusted_drop", "Market-Adjusted Drop"),
    ("adjusted_surge", "Market-Adjusted Surge"),
    ("price_threshold", "Price Threshold"),
    ("confluence", "Confluence (2+ signals)"),
]

# Strategy tone mapping for StrategyTag
STRATEGY_TONES = {
    "macd": ("MACD", "accent"),
    "volume_spike": ("VOL SPIKE", "accent"),
    "ma_crossover": ("MA CROSS", "warn"),
    "adjusted_drop": ("ADJ DROP", "down"),
    "adjusted_surge": ("ADJ SURGE", "down"),
    "rsi": ("RSI", "up"),
    "confluence": ("CONFLUENCE", "up"),
    "percent_change": ("PCT CHG", "warn"),
    "price_threshold": ("THRESHOLD", "accent"),
}

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>RapidSift</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: oklch(0.155 0.012 250);
            --bg-elev: oklch(0.185 0.012 250);
            --surface: oklch(0.205 0.012 250);
            --surface-2: oklch(0.235 0.012 250);
            --line: oklch(0.30 0.010 250 / 0.9);
            --line-soft: oklch(0.30 0.010 250 / 0.45);
            --grid: oklch(0.28 0.010 250 / 0.25);
            --text: oklch(0.965 0.005 250);
            --text-dim: oklch(0.74 0.008 250);
            --text-mute: oklch(0.55 0.010 250);
            --text-faint: oklch(0.42 0.010 250);
            --accent: oklch(0.80 0.135 200);
            --accent-dim: oklch(0.55 0.085 200);
            --accent-soft: oklch(0.80 0.135 200 / 0.12);
            --up: oklch(0.80 0.165 145);
            --up-soft: oklch(0.80 0.165 145 / 0.14);
            --down: oklch(0.72 0.185 25);
            --down-soft: oklch(0.72 0.185 25 / 0.14);
            --warn: oklch(0.83 0.150 80);
            --warn-soft: oklch(0.83 0.150 80 / 0.14);
            --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
            --sans: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
            --row-h: 44px;
            --r-sm: 4px;
            --r-md: 6px;
            --r-lg: 10px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--sans);
            font-size: 13px;
            line-height: 1.45;
            background: var(--bg);
            color: var(--text);
            min-width: 1200px;
        }
        body::before {
            content: "";
            position: fixed; inset: 0;
            pointer-events: none;
            background-image:
                linear-gradient(var(--grid) 1px, transparent 1px),
                linear-gradient(90deg, var(--grid) 1px, transparent 1px);
            background-size: 80px 80px;
            mask-image: radial-gradient(ellipse at 50% 0%, #000 0%, transparent 70%);
            opacity: 0.4;
            z-index: 0;
        }
        .num { font-family: var(--mono); font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }

        /* ── Top Bar ── */
        .topbar {
            display: flex; align-items: center; justify-content: space-between;
            padding: 14px 28px;
            border-bottom: 0.5px solid var(--line);
            background: oklch(0.16 0.012 250 / 0.85);
            backdrop-filter: blur(12px);
            position: sticky; top: 0; z-index: 10;
        }
        .topbar-left { display: flex; align-items: center; gap: 18px; }
        .topbar-right { display: flex; align-items: center; gap: 18px; }
        .logo-group { display: flex; align-items: center; gap: 10px; }
        .logo-text { display: flex; flex-direction: column; line-height: 1.1; }
        .logo-name { font-size: 13px; font-weight: 600; letter-spacing: 0.01em; }
        .logo-sub { font-size: 10px; color: var(--text-mute); font-family: var(--mono); letter-spacing: 0.05em; }
        .vdiv { width: 1px; height: 22px; background: var(--line); }

        /* Tab nav */
        .tab-nav { display: flex; gap: 2px; }
        .tab-btn {
            appearance: none; border: 0; background: transparent;
            color: var(--text-mute); padding: 7px 12px; border-radius: 6px;
            font-size: 12px; font-weight: 400; letter-spacing: 0.005em;
            cursor: pointer; font-family: inherit; position: relative;
            text-decoration: none; display: inline-block;
        }
        .tab-btn:hover { color: var(--text-dim); }
        .tab-btn.active {
            color: var(--text); font-weight: 500; background: var(--surface);
        }
        .tab-btn.active::after {
            content: ""; position: absolute; left: 8px; right: 8px; bottom: -1px;
            height: 1.5px; background: var(--accent); border-radius: 1px;
            box-shadow: 0 0 6px var(--accent);
        }

        /* Market status */
        .market-status { display: flex; align-items: center; gap: 14px; font-size: 11px; color: var(--text-mute); }
        .pulse-dot {
            width: 6px; height: 6px; border-radius: 50%; background: var(--up);
            box-shadow: 0 0 6px var(--up); animation: pulseDot 2s ease-in-out infinite;
            display: inline-block;
        }
        .status-dot {
            width: 6px; height: 6px; border-radius: 50%;
            display: inline-block; transition: all 0.3s;
        }
        .status-dot.open {
            background: var(--up); box-shadow: 0 0 6px var(--up);
            animation: pulseDot 2s ease-in-out infinite;
        }
        .status-dot.closed {
            background: var(--down); box-shadow: 0 0 4px var(--down);
            opacity: 0.7;
        }
        @keyframes pulseDot {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.35; }
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Refresh button */
        .refresh-btn {
            appearance: none; display: inline-flex; align-items: center; gap: 8px;
            padding: 7px 13px 7px 11px; border-radius: 7px;
            font-size: 12px; font-weight: 500; font-family: inherit;
            color: var(--bg); background: var(--accent);
            border: 0.5px solid var(--accent);
            box-shadow: 0 0 0 1px oklch(0.80 0.135 200 / 0.25), 0 4px 14px oklch(0.80 0.135 200 / 0.25);
            cursor: pointer;
        }
        .refresh-btn:hover { filter: brightness(1.1); }
        .refresh-btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .refresh-btn .spinner {
            display: none; width: 12px; height: 12px;
            border: 2px solid var(--bg); border-top-color: transparent;
            border-radius: 50%; animation: spin 0.6s linear infinite;
        }
        .refresh-btn.loading .spinner { display: inline-block; }
        .refresh-btn.loading .btn-text { display: none; }

        /* ── KPI Strip ── */
        .kpi-strip {
            display: grid; grid-template-columns: repeat(4, 1fr);
            gap: 1px; background: var(--line);
            border-top: 0.5px solid var(--line); border-bottom: 0.5px solid var(--line);
        }
        .kpi-cell {
            background: var(--bg); padding: 18px 24px 20px;
            display: flex; flex-direction: column; gap: 8px;
            position: relative; overflow: hidden;
        }
        .kpi-label {
            font-size: 10.5px; font-weight: 500; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--text-mute);
        }
        .kpi-value-row { display: flex; align-items: baseline; gap: 10px; }
        .kpi-value { font-family: var(--mono); font-size: 32px; font-weight: 500; letter-spacing: -0.02em; color: var(--text); }
        .kpi-trend {
            font-family: var(--mono); font-size: 11px; font-weight: 500;
            display: inline-flex; align-items: center; gap: 3px;
        }
        .kpi-trend.up { color: var(--up); }
        .kpi-trend.down { color: var(--down); }
        .kpi-sub { font-size: 11px; color: var(--text-mute); }
        .kpi-corner {
            position: absolute; top: 0; right: 0; width: 24px; height: 24px;
        }
        .kpi-corner.up { background: linear-gradient(135deg, transparent 50%, var(--up-soft) 50%); }
        .kpi-corner.down { background: linear-gradient(135deg, transparent 50%, var(--down-soft) 50%); }

        /* ── Controls / Filter Bar ── */
        .filter-bar {
            display: flex; align-items: flex-end; gap: 14px; padding: 14px 28px;
            border-bottom: 0.5px solid var(--line); flex-wrap: wrap;
        }
        .filter-group { display: flex; flex-direction: column; gap: 3px; }
        .filter-label {
            font-size: 9.5px; font-weight: 500; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--text-mute);
        }
        .filter-select {
            appearance: none; -webkit-appearance: none;
            padding: 6px 28px 6px 10px;
            background: var(--surface); border: 0.5px solid var(--line);
            border-radius: 7px; color: var(--text);
            font-family: inherit; font-size: 12px; cursor: pointer; min-width: 128px;
            background-image: url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 1 L5 5 L10 1' fill='none' stroke='%23757b85' stroke-width='1' stroke-linecap='round'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 10px center;
        }
        .filter-select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-soft); }
        .filter-select option { background: var(--bg-elev); color: var(--text); }

        /* ── Cards ── */
        .card {
            border: 0.5px solid var(--line); border-radius: var(--r-lg);
            background: var(--bg-elev); overflow: hidden; margin: 0 28px 28px;
        }
        .card-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 14px 18px; border-bottom: 0.5px solid var(--line);
            background: oklch(0.19 0.012 250);
        }
        .card-header h2 { font-size: 13px; font-weight: 500; letter-spacing: 0.01em; margin: 0; }
        .card-footer {
            display: flex; align-items: center; justify-content: space-between;
            padding: 12px 18px; border-top: 0.5px solid var(--line);
            background: oklch(0.18 0.012 250); font-size: 11px; color: var(--text-mute);
        }
        .chart-pad { padding: 24px; }

        /* ── Signals Table ── */
        .sig-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12.5px; }
        .sig-table thead th {
            text-align: left; padding: 11px 14px;
            font-size: 9.5px; font-weight: 500; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--text-mute);
            border-bottom: 0.5px solid var(--line);
            background: oklch(0.18 0.012 250);
            position: sticky; top: 0; white-space: nowrap;
        }
        .sig-table thead th.r { text-align: right; }
        .sig-table tbody td {
            padding: 0 14px; height: var(--row-h);
            border-bottom: 0.5px solid var(--line-soft);
            color: var(--text); vertical-align: middle;
        }
        .sig-table tbody td.r { text-align: right; }
        .sig-table tbody tr { transition: background 0.12s; cursor: pointer; }
        .sig-table tbody tr:hover { background: oklch(0.21 0.014 250); }

        /* ── Inline Components ── */
        .strategy-tag {
            display: inline-flex; align-items: center; gap: 6px;
            font-family: var(--mono); font-size: 10.5px; font-weight: 500;
            letter-spacing: 0.04em; text-transform: uppercase;
            padding: 2.5px 7px; border-radius: 3px; background: transparent;
        }
        .strategy-tag .dot { width: 3px; height: 3px; border-radius: 50%; }
        .strategy-tag.tone-accent { color: var(--accent); border: 0.5px solid var(--accent); }
        .strategy-tag.tone-accent .dot { background: var(--accent); }
        .strategy-tag.tone-up { color: var(--up); border: 0.5px solid var(--up); }
        .strategy-tag.tone-up .dot { background: var(--up); }
        .strategy-tag.tone-down { color: var(--down); border: 0.5px solid var(--down); }
        .strategy-tag.tone-down .dot { background: var(--down); }
        .strategy-tag.tone-warn { color: var(--warn); border: 0.5px solid var(--warn); }
        .strategy-tag.tone-warn .dot { background: var(--warn); }

        .result-pill {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 11px; font-weight: 500; letter-spacing: 0.02em;
            padding: 3px 8px 3px 7px; border-radius: 999px;
        }
        .result-pill .rdot { width: 5px; height: 5px; border-radius: 50%; }
        .result-pill.win { color: var(--up); background: var(--up-soft); border: 0.5px solid var(--up); }
        .result-pill.win .rdot { background: var(--up); box-shadow: 0 0 5px var(--up); }
        .result-pill.loss { color: var(--down); background: var(--down-soft); border: 0.5px solid var(--down); }
        .result-pill.loss .rdot { background: var(--down); box-shadow: 0 0 5px var(--down); }
        .result-pill.pending { color: var(--text-dim); background: oklch(0.30 0.008 250 / 0.5); border: 0.5px solid var(--line); }
        .result-pill.pending .rdot { background: var(--text-dim); animation: pulseDot 1.6s ease-in-out infinite; }

        .wr-gauge { display: flex; align-items: center; gap: 8px; }
        .wr-bar { display: flex; gap: 2px; width: 64px; }
        .wr-seg { flex: 1; height: 8px; border-radius: 1px; }
        .wr-seg.unlit { background: oklch(0.32 0.008 250); opacity: 0.7; }
        .wr-num { font-family: var(--mono); font-weight: 500; font-size: 12px; min-width: 42px; text-align: right; }

        .dd-wrap { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
        .dd-track { position: relative; width: 56px; height: 4px; border-radius: 1px; background: oklch(0.28 0.008 250); }
        .dd-fill { position: absolute; right: 0; top: 0; bottom: 0; border-radius: 1px; }
        .dd-num { font-family: var(--mono); font-weight: 500; font-size: 12px; min-width: 42px; text-align: right; }

        /* Sparkline popovers */
        .spark-wrap { position: relative; display: inline-block; cursor: default; }
        .spark-detail {
            display: none; position: absolute; z-index: 100;
            left: 50%; top: 100%; transform: translateX(-50%);
            padding: 6px; background: var(--bg-elev);
            border: 0.5px solid var(--line); border-radius: 8px;
            box-shadow: 0 4px 16px oklch(0 0 0 / 0.4); white-space: nowrap;
        }
        .spark-wrap:hover .spark-detail { display: block; }

        .no-data { color: var(--text-mute); font-style: italic; padding: 40px; text-align: center; }
        .content-area { position: relative; z-index: 1; }

        /* Legend dots */
        .legend-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }

        /* Kbd chips */
        .kbd {
            font-family: var(--mono); font-size: 10px; color: var(--text-dim);
            padding: 1px 5px; border-radius: 3px;
            border: 0.5px solid var(--line); background: var(--surface);
        }

        /* Streaming dot */
        .stream-dot {
            width: 5px; height: 5px; border-radius: 50%; background: var(--up);
            box-shadow: 0 0 4px var(--up); animation: pulseDot 1.6s ease-in-out infinite;
            display: inline-block;
        }
        .count-pill {
            padding: 2px 7px; border-radius: 999px;
            border: 0.5px solid var(--line); background: var(--surface);
            font-family: var(--mono); font-size: 10.5px; color: var(--text-dim);
        }
    </style>
    <meta http-equiv="refresh" content="300">
</head>
<body>
<div class="topbar">
    <div class="topbar-left">
        <div class="logo-group">
            <svg width="22" height="22" viewBox="0 0 22 22">
                <rect x="1" y="1" width="20" height="20" rx="4" fill="none" stroke="var(--accent)" stroke-width=".75"/>
                <path d="M4 14 L8 9 L12 12 L18 5" fill="none" stroke="var(--accent)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
                <circle cx="18" cy="5" r="1.6" fill="var(--accent)"/>
            </svg>
            <div class="logo-text">
                <span class="logo-name">RAPIDSIFT</span>
            </div>
        </div>
        <div class="vdiv"></div>
        <nav class="tab-nav">
            <a href="/" class="tab-btn {{ 'active' if active_tab == 'charts' }}">Charts</a>
            <a href="/buy-signals" class="tab-btn {{ 'active' if active_tab == 'buy_signals' }}">Buy signals</a>
            <a href="/reliability" class="tab-btn {{ 'active' if active_tab == 'reliability' }}">Signal reliability</a>
            <a href="/strategies" class="tab-btn {{ 'active' if active_tab == 'strategies' }}">Strategies</a>
        </nav>
    </div>
    <div class="topbar-right">
        <div class="market-status" id="market-status-block">
            <div style="display:flex;align-items:center;gap:6px">
                <span id="nyse-dot" class="status-dot"></span>
                <span style="color:var(--text-dim)">NYSE</span>
                <span class="num" id="nyse-label">--</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
                <span id="nasdaq-dot" class="status-dot"></span>
                <span style="color:var(--text-dim)">NASDAQ</span>
                <span class="num" id="nasdaq-label">--</span>
            </div>
            <div class="vdiv" style="height:14px"></div>
            <div style="display:flex;align-items:center;gap:6px">
                <span style="color:var(--text-mute)">ET</span>
                <span class="num" style="color:var(--text-dim)" id="et-clock">--:--:--</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
                <span>next refresh</span>
                <span class="num" style="color:var(--text-dim)" id="refresh-countdown">300s</span>
            </div>
        </div>
        <div class="vdiv"></div>
        <button class="refresh-btn" onclick="refreshData(this)">
            <svg width="11" height="11" viewBox="0 0 12 12">
                <path d="M6 1.5 v 3.5 M6 1.5 L 4.2 3.2 M6 1.5 L 7.8 3.2 M2 7 a 4 4 0 1 0 8 0 v -1.2"
                      fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
            <span class="btn-text">Refresh now</span>
            <span class="spinner"></span>
        </button>
    </div>
</div>

<div class="content-area">
    {% block content %}{% endblock %}
</div>

<script>
// ET clock (ticks locally between API polls)
function updateETClock() {
    var d = new Date();
    var et = d.toLocaleTimeString('en-US', {hour12:false, timeZone:'America/New_York'});
    var el = document.getElementById('et-clock');
    if (el) el.textContent = et;
}
setInterval(updateETClock, 1000);
updateETClock();

// Market status polling
function fetchMarketStatus() {
    fetch('/api/market-status')
        .then(r => r.json())
        .then(data => {
            var nyseDot = document.getElementById('nyse-dot');
            var nyseLabel = document.getElementById('nyse-label');
            var nasdaqDot = document.getElementById('nasdaq-dot');
            var nasdaqLabel = document.getElementById('nasdaq-label');
            if (nyseDot && nyseLabel) {
                nyseDot.className = 'status-dot ' + (data.nyse_open ? 'open' : 'closed');
                nyseLabel.textContent = data.nyse_open ? 'OPEN' : 'CLOSED';
                nyseLabel.style.color = data.nyse_open ? 'var(--up)' : 'var(--down)';
            }
            if (nasdaqDot && nasdaqLabel) {
                nasdaqDot.className = 'status-dot ' + (data.nasdaq_open ? 'open' : 'closed');
                nasdaqLabel.textContent = data.nasdaq_open ? 'OPEN' : 'CLOSED';
                nasdaqLabel.style.color = data.nasdaq_open ? 'var(--up)' : 'var(--down)';
            }
        })
        .catch(() => {});
}
fetchMarketStatus();
setInterval(fetchMarketStatus, 30000);

// Refresh countdown
var refreshLeft = 300;
function updateCountdown() {
    refreshLeft = Math.max(0, refreshLeft - 1);
    var el = document.getElementById('refresh-countdown');
    if (el) el.textContent = String(refreshLeft).padStart(3,'0') + 's';
    if (refreshLeft <= 0) refreshLeft = 300;
}
setInterval(updateCountdown, 1000);

function refreshData(btn) {
    btn.classList.add('loading');
    btn.disabled = true;
    fetch('/api/refresh', {method: 'POST'})
        .then(r => r.json())
        .then(data => { window.location.reload(); })
        .catch(() => { btn.classList.remove('loading'); btn.disabled = false; });
}
</script>
</body>
</html>
"""

CHARTS_TAB = """
{% extends "base" %}
{% block content %}
    <div class="kpi-strip">
        <div class="kpi-cell">
            <span class="kpi-label">Tickers Tracked</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ watchlist|length }}</span>
            </div>
            <span class="kpi-sub">watchlist</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Total Signals &middot; 30d</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "{:,}".format(total_signals) }}</span>
            </div>
            <span class="kpi-sub">all strategies</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Buy Signals</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "{:,}".format(buy_signals) }}</span>
            </div>
            <span class="kpi-sub">30 day window</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Sell Signals</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "{:,}".format(sell_signals) }}</span>
            </div>
            <span class="kpi-sub">30 day window</span>
            <span class="kpi-corner down"></span>
        </div>
    </div>

    <div class="filter-bar">
        <form style="display:flex;gap:14px;align-items:flex-end;" method="GET" action="/" id="chart-form">
            <div class="filter-group">
                <span class="filter-label">Ticker</span>
                <select name="ticker" class="filter-select" onchange="this.form.submit()">
                    {% for t in watchlist %}
                    <option value="{{ t }}" {{ 'selected' if t == selected_ticker }}>{{ t }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Strategy</span>
                <select name="signal_type" class="filter-select" onchange="this.form.submit()">
                    {% for value, label in signal_types %}
                    <option value="{{ value }}" {{ 'selected' if value == selected_signal_type }}>{{ label }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Chart Type</span>
                <select name="chart_type" class="filter-select" onchange="this.form.submit()">
                    <option value="candlestick" {{ 'selected' if chart_type == 'candlestick' }}>Candlestick</option>
                    <option value="line" {{ 'selected' if chart_type == 'line' }}>Line</option>
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Date Range</span>
                <select name="days" class="filter-select" onchange="this.form.submit()">
                    {% for val, lbl in [('30','1 Month'),('60','2 Months'),('90','3 Months'),('180','6 Months'),('365','1 Year')] %}
                    <option value="{{ val }}" {{ 'selected' if days == val|int }}>{{ lbl }}</option>
                    {% endfor %}
                </select>
            </div>
        </form>
    </div>

    {% if chart_json %}
    <div class="card" style="margin-top:20px">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px">
                <h2>{{ selected_ticker }}</h2>
                <span class="count-pill">{{ days }}d</span>
            </div>
        </div>
        <div class="chart-pad">
            <div id="main-chart"></div>
            <script>
                var data = {{ chart_json | safe }};
                var layout = {{ layout_json | safe }};
                Plotly.newPlot('main-chart', data, layout, {responsive: true});
            </script>
        </div>
    </div>
    {% else %}
    <div class="card" style="margin-top:20px"><p class="no-data">No price data available for {{ selected_ticker }}.</p></div>
    {% endif %}

    {% if signals_list %}
    <div class="card">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px">
                <h2>Signals &mdash; {{ selected_ticker }}</h2>
                {% if selected_signal_type != 'all' %}
                <span class="count-pill">{{ selected_signal_label }}</span>
                {% endif %}
            </div>
        </div>
        <div style="overflow:auto">
            <table class="sig-table">
                <thead>
                    <tr><th>Time</th><th>Direction</th><th>Strategy</th><th>Detail</th><th class="r">Price</th><th>Result (+5%)</th></tr>
                </thead>
                <tbody>
                {% for sig in signals_list %}
                <tr>
                    <td><span class="num" style="color:var(--text-dim)">{{ sig.timestamp }}</span></td>
                    <td>
                        {% if sig.direction == 'buy' %}<span style="color:var(--up);font-weight:600">BUY</span>
                        {% elif sig.direction == 'sell' %}<span style="color:var(--down);font-weight:600">SELL</span>
                        {% else %}<span style="color:var(--warn);font-weight:600">{{ sig.direction|upper }}</span>{% endif %}
                    </td>
                    <td>{{ sig.strategy_tag }}</td>
                    <td style="color:var(--text-dim)">{{ sig.details }}</td>
                    <td class="r"><span class="num" style="font-weight:500">${{ "%.2f"|format(sig.price_at_signal) }}</span></td>
                    <td>
                        {% if sig.matured is defined and sig.matured %}
                            {% if sig.hit_5pct %}
                                <span class="result-pill win"><span class="rdot"></span>+5% in {{ sig.days_to_hit_5pct }}d</span>
                            {% else %}
                                <span class="result-pill loss"><span class="rdot"></span>Missed</span>
                            {% endif %}
                        {% elif sig.direction == 'buy' %}
                            <span class="result-pill pending"><span class="rdot"></span>Pending</span>
                        {% else %}
                            <span style="color:var(--text-faint)">&mdash;</span>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="card-footer">
            <span>{{ signals_list|length }} signal(s)</span>
        </div>
    </div>
    {% else %}
    <div class="card"><p class="no-data">No signals found for this ticker/strategy combination.</p></div>
    {% endif %}
{% endblock %}
"""

BUY_SIGNALS_TAB = """
{% extends "base" %}
{% block content %}
    <div class="kpi-strip">
        <div class="kpi-cell">
            <span class="kpi-label">Buy Signals &middot; 90d</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "{:,}".format(buy_count) }}</span>
            </div>
            <span class="kpi-sub">all strategies</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Tickers with Signals</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ unique_tickers }}</span>
            </div>
            <span class="kpi-sub">unique tickers</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Aggregate Win Rate</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "%.1f"|format(agg_win_rate) }}%</span>
                {% if agg_win_rate >= 50 %}
                <span class="kpi-trend up">
                    <svg width="8" height="8" viewBox="0 0 8 8"><path d="M4 1 L7 5 L1 5 Z" fill="currentColor"/></svg>
                    +5% target
                </span>
                {% endif %}
            </div>
            <span class="kpi-sub">gain target +5%</span>
            <span class="kpi-corner {{ 'up' if agg_win_rate >= 50 else 'down' }}"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Open Positions</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ pending_count }}</span>
            </div>
            <span class="kpi-sub">pending signals</span>
            <span class="kpi-corner down"></span>
        </div>
    </div>

    <div class="filter-bar">
        <div class="filter-group">
            <span class="filter-label">Gain Target</span>
            <select id="gain-target" class="filter-select" onchange="toggleTarget(this.value)">
                <option value="5" selected>+5%</option>
                <option value="10">+10%</option>
            </select>
        </div>
        <div style="flex:1"></div>
        <div style="display:flex;align-items:center;gap:12px;font-size:11px;color:var(--text-mute)">
            <span class="legend-dot" style="background:var(--up);box-shadow:0 0 4px var(--up)"></span> win
            <span class="legend-dot" style="background:var(--down);box-shadow:0 0 4px var(--down)"></span> loss
            <span class="legend-dot" style="background:var(--accent);box-shadow:0 0 4px var(--accent)"></span> pending
        </div>
    </div>

    <div class="card" style="margin-top:0;margin-bottom:28px;border-radius:0 0 var(--r-lg) var(--r-lg)">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px">
                <h2>Latest buy signals</h2>
                <span class="count-pill">{{ buy_signals_list|length }} of {{ "{:,}".format(buy_count) }}</span>
                <span style="display:inline-flex;align-items:center;gap:5px;font-size:10.5px;color:var(--text-mute)">
                    <span class="stream-dot"></span>
                    streaming
                </span>
            </div>
        </div>
        {% if buy_signals_list %}
        <div style="overflow:auto">
            <table class="sig-table">
                <thead>
                    <tr>
                        <th style="width:40px">#</th>
                        <th style="width:108px">Time</th>
                        <th style="width:108px">Ticker</th>
                        <th style="width:112px">14d</th>
                        <th style="width:126px">Strategy</th>
                        <th>Detail</th>
                        <th class="r" style="width:92px">Signal</th>
                        <th class="r" style="width:92px">Entry</th>
                        <th style="width:96px">Result</th>
                        <th class="r" style="width:132px">Drawdown</th>
                        <th style="width:138px">Win Rate</th>
                        <th class="r" style="width:68px">Fired</th>
                    </tr>
                </thead>
                <tbody>
                {% for sig in buy_signals_list %}
                <tr onclick="window.location='/?ticker={{ sig.ticker }}&signal_type={{ sig.signal_type }}'">
                    <td><span class="num" style="color:var(--text-faint)">{{ "%02d"|format(loop.index) }}</span></td>
                    <td>
                        <div style="display:flex;flex-direction:column;line-height:1.25">
                            <span class="num" style="color:var(--text-dim);font-size:12px">{{ sig.timestamp[:10] if sig.timestamp else '' }}</span>
                            <span style="color:var(--text-mute);font-size:10.5px">{{ sig.timestamp[11:19] if sig.timestamp and sig.timestamp|length > 11 else '' }}</span>
                        </div>
                    </td>
                    <td>
                        <div style="display:flex;flex-direction:column;line-height:1.2">
                            <span style="font-family:var(--mono);font-weight:600;font-size:13px;letter-spacing:0.02em">{{ sig.ticker }}</span>
                        </div>
                    </td>
                    <td>{% if sig.sparkline %}{{ sig.sparkline }}{% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}</td>
                    <td>{{ sig.strategy_tag }}</td>
                    <td style="color:var(--text-dim);font-size:12.5px">{{ sig.details }}</td>
                    <td class="r"><span class="num" style="font-weight:500">${{ "%.2f"|format(sig.price_at_signal) }}</span></td>
                    <td class="r">
                        {% if sig.entry_price is defined and sig.entry_price is not none %}
                        <div style="display:flex;flex-direction:column;align-items:flex-end;line-height:1.2">
                            <span class="num" style="color:var(--text-dim)">${{ "%.2f"|format(sig.entry_price) }}</span>
                            {% set slip = ((sig.entry_price - sig.price_at_signal) / sig.price_at_signal * 100) %}
                            <span class="num" style="font-size:10px;color:{{ 'var(--up)' if slip >= 0 else 'var(--down)' }}">{{ "%+.2f"|format(slip) }}%</span>
                        </div>
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                    </td>
                    <td>
                        <span class="target-5">
                        {% if sig.matured is defined and sig.matured %}
                            {% if sig.hit_5pct %}
                                <span class="result-pill win"><span class="rdot"></span>+5% in {{ sig.days_to_hit_5pct }}d</span>
                            {% else %}
                                <span class="result-pill loss"><span class="rdot"></span>Missed</span>
                            {% endif %}
                        {% else %}
                            <span class="result-pill pending"><span class="rdot"></span>Pending</span>
                        {% endif %}
                        </span>
                        <span class="target-10" style="display:none">
                        {% if sig.matured is defined and sig.matured %}
                            {% if sig.hit_10pct %}
                                <span class="result-pill win"><span class="rdot"></span>+10% in {{ sig.days_to_hit_10pct }}d</span>
                            {% else %}
                                <span class="result-pill loss"><span class="rdot"></span>Missed</span>
                            {% endif %}
                        {% else %}
                            <span class="result-pill pending"><span class="rdot"></span>Pending</span>
                        {% endif %}
                        </span>
                    </td>
                    <td class="r">
                        {% if sig.max_drawdown_pct is defined and sig.max_drawdown_pct is not none %}
                            {{ sig.drawdown_html }}
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                    </td>
                    <td>
                        <span class="target-5">
                        {% if sig.win_rate_5pct is defined and sig.win_rate_5pct is not none %}
                            {{ sig.win_rate_gauge_5 }}
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                        </span>
                        <span class="target-10" style="display:none">
                        {% if sig.win_rate_10pct is defined and sig.win_rate_10pct is not none %}
                            {{ sig.win_rate_gauge_10 }}
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                        </span>
                    </td>
                    <td class="r">
                        {% if sig.times_fired is defined and sig.times_fired is not none %}
                            <span class="num" style="color:var(--text-dim)">{{ sig.times_fired }}</span>
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="card-footer">
            <div style="display:flex;gap:18px">
                <span>Showing <span class="num" style="color:var(--text-dim)">1&ndash;{{ buy_signals_list|length }}</span> of <span class="num" style="color:var(--text-dim)">{{ "{:,}".format(buy_count) }}</span></span>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
                <span class="kbd">J</span><span class="kbd">K</span> navigate
                <span class="vdiv" style="height:12px;margin:0 4px"></span>
                <span class="kbd">&crarr;</span> open chart
            </div>
        </div>
        {% else %}
        <p class="no-data">No buy signals found.</p>
        {% endif %}
    </div>

    <script>
    function toggleTarget(val) {
        document.querySelectorAll('.target-5').forEach(el => el.style.display = val === '5' ? '' : 'none');
        document.querySelectorAll('.target-10').forEach(el => el.style.display = val === '10' ? '' : 'none');
    }
    </script>
{% endblock %}
"""

RELIABILITY_TAB = """
{% extends "base" %}
{% block content %}
    <div class="filter-bar">
        <div class="filter-group">
            <span class="filter-label">Gain Target</span>
            <select id="gain-target" class="filter-select" onchange="toggleTarget(this.value)">
                <option value="5" selected>+5%</option>
                <option value="10">+10%</option>
            </select>
        </div>
        {% if baseline %}
        <div style="flex:1"></div>
        <div style="display:flex;align-items:center;gap:18px;font-size:12px">
            <span style="color:var(--text-mute)">Baseline:</span>
            <span class="target-5">
                <span class="num" style="color:var(--accent)">{{ baseline.win_rate_5pct|default(0) }}%</span>
                <span style="color:var(--text-mute);font-size:10px">({{ baseline.total_entries|default(0) }} random entries)</span>
            </span>
            <span class="target-10" style="display:none">
                <span class="num" style="color:var(--accent)">{{ baseline.win_rate_10pct|default(0) }}%</span>
                <span style="color:var(--text-mute);font-size:10px">({{ baseline.total_entries|default(0) }} random entries)</span>
            </span>
        </div>
        {% endif %}
    </div>

    {% if baseline %}
    <div class="kpi-strip">
        <div class="kpi-cell">
            <span class="kpi-label">Random Entries Tested</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ "{:,}".format(baseline.total_entries|default(0)) }}</span>
            </div>
            <span class="kpi-sub">baseline comparison</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell">
            <span class="kpi-label">Baseline Win Rate (+5%)</span>
            <div class="kpi-value-row">
                <span class="target-5"><span class="kpi-value">{{ baseline.win_rate_5pct|default(0) }}%</span></span>
                <span class="target-10" style="display:none"><span class="kpi-value">{{ baseline.win_rate_10pct|default(0) }}%</span></span>
            </div>
            <span class="kpi-sub">random entry benchmark</span>
            <span class="kpi-corner up"></span>
        </div>
        <div class="kpi-cell" style="grid-column: span 2">
            <span class="kpi-label">Signal Strategies</span>
            <div class="kpi-value-row">
                <span class="kpi-value">{{ win_rates|length }}</span>
            </div>
            <span class="kpi-sub">ticker &times; strategy combinations</span>
            <span class="kpi-corner up"></span>
        </div>
    </div>
    {% endif %}

    {% if win_rates %}
    <div class="card" style="margin-top:0">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px">
                <h2>Signal Reliability &mdash; 14 day window</h2>
                <span class="count-pill">{{ win_rates|length }} strategies</span>
            </div>
        </div>
        <div style="overflow:auto">
            <table class="sig-table">
                <thead>
                    <tr>
                        <th>Ticker</th><th>Strategy</th><th class="r">Fired</th>
                        <th style="width:138px">Win Rate</th>
                        <th class="r">vs Baseline</th>
                        <th>Summary</th>
                    </tr>
                </thead>
                <tbody>
                {% for wr in win_rates %}
                <tr onclick="window.location='/?ticker={{ wr.ticker }}&signal_type={{ wr.signal_type }}'">
                    <td>
                        <span style="font-family:var(--mono);font-weight:600;font-size:13px;letter-spacing:0.02em">{{ wr.ticker }}</span>
                    </td>
                    <td>{{ wr.strategy_tag }}</td>
                    <td class="r"><span class="num" style="color:var(--text-dim)">{{ wr.total_signals }}</span></td>
                    <td>
                        <span class="target-5">{{ wr.win_rate_gauge_5 }}</span>
                        <span class="target-10" style="display:none">{{ wr.win_rate_gauge_10 }}</span>
                    </td>
                    <td class="r">
                        {% if baseline %}
                        <span class="target-5">
                            {% set edge5 = wr.win_rate_5pct - baseline.win_rate_5pct %}
                            <span class="num" style="font-weight:500;color:{{ 'var(--up)' if edge5 > 0 else 'var(--down)' }}">{{ "%+.1f"|format(edge5) }}pp</span>
                        </span>
                        <span class="target-10" style="display:none">
                            {% set edge10 = wr.win_rate_10pct - baseline.win_rate_10pct %}
                            <span class="num" style="font-weight:500;color:{{ 'var(--up)' if edge10 > 0 else 'var(--down)' }}">{{ "%+.1f"|format(edge10) }}pp</span>
                        </span>
                        {% else %}<span style="color:var(--text-faint)">&mdash;</span>{% endif %}
                    </td>
                    <td>
                        <span class="target-5" style="color:var(--text-dim)">{{ wr.summary_5pct }}</span>
                        <span class="target-10" style="display:none;color:var(--text-dim)">{{ wr.summary_10pct }}</span>
                    </td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="card-footer">
            <span>{{ win_rates|length }} strategies evaluated</span>
        </div>
    </div>
    {% else %}
    <div class="card" style="margin-top:20px"><p class="no-data">No matured signals yet. Signals need to be at least 14 days old to validate.</p></div>
    {% endif %}

    <script>
    function toggleTarget(val) {
        document.querySelectorAll('.target-5').forEach(el => el.style.display = val === '5' ? '' : 'none');
        document.querySelectorAll('.target-10').forEach(el => el.style.display = val === '10' ? '' : 'none');
    }
    </script>
{% endblock %}
"""


STRATEGIES_TAB = """
{% extends "base" %}
{% block content %}
    <div style="padding:28px;max-width:1100px">

        <div style="margin-bottom:32px">
            <h2 style="font-size:16px;font-weight:600;margin-bottom:6px">How Signals Work</h2>
            <p style="color:var(--text-dim);font-size:13px;line-height:1.6;max-width:720px">
                RapidSift evaluates each ticker against multiple independent strategies every cycle.
                When a strategy's conditions are met, a buy or sell signal fires. A <strong>5-day cooldown</strong>
                prevents the same strategy from re-firing on the same ticker within 5 trading days.
                When 2 or more buy signals fire on the same ticker on the same day, a <strong>Confluence</strong> signal is also generated.
            </p>
        </div>

        {% for s in strategies %}
        <div class="card" style="margin:0 0 16px;border-radius:var(--r-md)">
            <div style="padding:18px 20px">
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                    {{ s.tag }}
                    <span style="font-size:14px;font-weight:500">{{ s.name }}</span>
                    <span style="margin-left:auto;font-size:11px;color:var(--text-mute)">{{ s.direction_label }}</span>
                </div>
                <p style="color:var(--text-dim);font-size:12.5px;line-height:1.65;margin-bottom:16px">
                    {{ s.description }}
                </p>
                <div style="display:flex;flex-wrap:wrap;gap:10px">
                    {% for p in s.params %}
                    <div style="display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:var(--r-sm);background:var(--surface);border:0.5px solid var(--line)">
                        <span style="font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-mute)">{{ p.label }}</span>
                        <span class="num" style="font-size:12px;font-weight:500;color:var(--accent)">{{ p.value }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endfor %}

        <div class="card" style="margin:0 0 16px;border-radius:var(--r-md)">
            <div style="padding:18px 20px">
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">
                    <span class="strategy-tag tone-up"><span class="dot"></span>CONFLUENCE</span>
                    <span style="font-size:14px;font-weight:500">Confluence (Composite)</span>
                    <span style="margin-left:auto;font-size:11px;color:var(--text-mute)">Buy only</span>
                </div>
                <p style="color:var(--text-dim);font-size:12.5px;line-height:1.65;margin-bottom:16px">
                    Not an independent strategy &mdash; fires automatically when <strong>2 or more</strong> buy signals
                    trigger on the same ticker on the same day. The idea is that multiple indicators agreeing
                    simultaneously is a stronger signal than any one alone. Tracked and validated separately
                    so you can compare its win rate against individual strategies.
                </p>
                <div style="display:flex;flex-wrap:wrap;gap:10px">
                    <div style="display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:var(--r-sm);background:var(--surface);border:0.5px solid var(--line)">
                        <span style="font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-mute)">Min signals</span>
                        <span class="num" style="font-size:12px;font-weight:500;color:var(--accent)">2</span>
                    </div>
                </div>
            </div>
        </div>

        <div style="margin-top:28px;padding:18px 20px;border-radius:var(--r-md);background:var(--surface);border:0.5px solid var(--line)">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                <svg width="14" height="14" viewBox="0 0 14 14" style="color:var(--accent)"><circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" stroke-width="1"/><text x="7" y="10.5" text-anchor="middle" font-size="9" font-weight="600" fill="currentColor">i</text></svg>
                <span style="font-size:12px;font-weight:500;color:var(--text-dim)">Validation</span>
            </div>
            <p style="color:var(--text-mute);font-size:12px;line-height:1.6">
                Each buy signal is validated by checking whether the price hit the gain target (+5% or +10%)
                within 14 trading days. Entry price is the <strong>next trading day's open</strong> after the signal fires,
                not the signal-day close. The market benchmark for adjusted returns is <strong>SPY</strong> (S&amp;P 500 ETF).
                Win rates on the Reliability tab compare each strategy against a <strong>random-entry baseline</strong>
                to show real edge.
            </p>
        </div>
    </div>
{% endblock %}
"""


from jinja2 import DictLoader, Environment

jinja_env = Environment(loader=DictLoader({
    "base": TEMPLATE,
    "charts": CHARTS_TAB,
    "buy_signals": BUY_SIGNALS_TAB,
    "reliability": RELIABILITY_TAB,
    "strategies": STRATEGIES_TAB,
}))


def render(template_name, **kwargs):
    return jinja_env.get_template(template_name).render(**kwargs)


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


# ── Inline HTML component builders ──────────────────────────────────────────

def _strategy_tag_html(signal_type: str) -> Markup:
    """Generate a StrategyTag as inline HTML."""
    label, tone = STRATEGY_TONES.get(signal_type, (signal_type.upper(), "accent"))
    return Markup(f'<span class="strategy-tag tone-{tone}"><span class="dot"></span>{label}</span>')


def _win_rate_gauge_html(value: float) -> Markup:
    """Generate a WinRateGauge (10-segment bar + number) as inline HTML."""
    if value is None:
        return Markup('<span style="color:var(--text-faint)">&mdash;</span>')
    cells = 10
    lit = round(value / 100 * cells)
    if value >= 70:
        tone_var = "var(--up)"
    elif value >= 50:
        tone_var = "var(--accent)"
    elif value >= 30:
        tone_var = "var(--warn)"
    else:
        tone_var = "var(--down)"

    segs = []
    for i in range(cells):
        if i < lit:
            segs.append(f'<span class="wr-seg" style="background:{tone_var};box-shadow:0 0 6px {tone_var}"></span>')
        else:
            segs.append('<span class="wr-seg unlit"></span>')

    bar = f'<div class="wr-bar">{"".join(segs)}</div>'
    num = f'<span class="wr-num" style="color:{tone_var}">{value:.1f}%</span>'
    return Markup(f'<div class="wr-gauge">{bar}{num}</div>')


def _drawdown_bar_html(value: float, worst: float) -> Markup:
    """Generate a DrawdownBar as inline HTML."""
    if value is None:
        return Markup('<span style="color:var(--text-faint)">&mdash;</span>')
    if worst == 0:
        pct = 0
    else:
        pct = min(1.0, abs(value) / abs(worst))
    if value <= -2.5:
        tone = "var(--down)"
    elif value <= -1.5:
        tone = "var(--warn)"
    else:
        tone = "var(--text-mute)"
    fill_w = f"{pct * 100:.1f}%"
    return Markup(
        f'<div class="dd-wrap">'
        f'<div class="dd-track"><div class="dd-fill" style="width:{fill_w};background:{tone};box-shadow:0 0 4px {tone}"></div></div>'
        f'<span class="dd-num" style="color:{tone}">{value:.1f}%</span>'
        f'</div>'
    )


def build_chart(ticker: str, signal_type: str = "all", chart_type: str = "candlestick", days: int = 60):
    import pandas as pd
    df = get_prices(ticker, days=days)
    if df.empty:
        return None, None

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2],
                        vertical_spacing=0.05,
                        subplot_titles=["Price", "RSI (14)", "Volume"])

    # Dark theme colors
    accent = "#4dd8e0"
    up_color = "#5dd672"
    down_color = "#e8654a"
    warn_color = "#d4b03a"
    text_dim = "#a8adb5"
    line_color = "#3e434c"

    if chart_type == "line":
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["close"], name="Close",
            line=dict(color=accent, width=2), fill="tozeroy",
            fillcolor="rgba(77,216,224,0.06)"
        ), row=1, col=1)
    else:
        fig.add_trace(go.Candlestick(
            x=df["date"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="Price",
            increasing_line_color=up_color, decreasing_line_color=down_color,
            increasing_fillcolor=up_color, decreasing_fillcolor=down_color,
        ), row=1, col=1)

    sma20 = compute_sma(df, 20)
    sma50 = compute_sma(df, 50)
    fig.add_trace(go.Scatter(x=df["date"], y=sma20, name="SMA20",
                             line=dict(color=warn_color, width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=sma50, name="SMA50",
                             line=dict(color=accent, width=1.5, dash="dot")), row=1, col=1)

    # Get validated buy signals to mark on chart
    con = get_connection()
    signals_df = get_signals(ticker, days=days)
    if not signals_df.empty:
        if signal_type != "all":
            signals_df = signals_df[signals_df["signal_type"] == signal_type]
        buy_sigs = signals_df[signals_df["direction"] == "buy"]
        sell_sigs = signals_df[signals_df["direction"] == "sell"]

        if not buy_sigs.empty:
            buy_prices = []
            for t in buy_sigs["timestamp"]:
                matching = df[df["date"] <= t]
                buy_prices.append(matching["close"].iloc[-1] if not matching.empty else None)
            fig.add_trace(go.Scatter(
                x=buy_sigs["timestamp"], y=buy_prices,
                mode="markers", name="Buy Signal",
                marker=dict(symbol="circle", size=7, color=up_color,
                            line=dict(width=1, color="rgba(93,214,114,0.3)")),
                hovertemplate="%{text}<extra>Buy Signal</extra>",
                text=[f"{row.get('signal_type','')}: ${p:.2f}" if p else "" for p, (_, row) in zip(buy_prices, buy_sigs.iterrows())]
            ), row=1, col=1)

        if not sell_sigs.empty:
            sell_prices = []
            for t in sell_sigs["timestamp"]:
                matching = df[df["date"] <= t]
                sell_prices.append(matching["close"].iloc[-1] if not matching.empty else None)
            fig.add_trace(go.Scatter(
                x=sell_sigs["timestamp"], y=sell_prices,
                mode="markers", name="Sell Signal",
                marker=dict(symbol="circle", size=7, color=down_color,
                            line=dict(width=1, color="rgba(232,101,74,0.3)"))
            ), row=1, col=1)

    con.close()

    rsi = compute_rsi(df, 14)
    fig.add_trace(go.Scatter(x=df["date"], y=rsi, name="RSI",
                             line=dict(color="#a78bfa", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color=down_color, opacity=0.5, row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color=up_color, opacity=0.5, row=2, col=1)

    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], name="Volume",
                         marker_color="rgba(77,216,224,0.35)"), row=3, col=1)

    bg_color = "#1a1d23"
    elev_color = "#22262d"

    fig.update_layout(
        template="plotly_dark",
        height=650,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        paper_bgcolor=elev_color,
        plot_bgcolor=bg_color,
        font=dict(family="Inter, -apple-system, sans-serif", color=text_dim, size=11),
        margin=dict(t=30, b=20, l=50, r=20),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(size=10, color=text_dim),
            bgcolor="rgba(0,0,0,0)",
        ),
    )

    for ax in ["xaxis", "xaxis2", "xaxis3"]:
        fig.update_layout(**{ax: dict(gridcolor=line_color, zerolinecolor=line_color)})
    for ax in ["yaxis", "yaxis2", "yaxis3"]:
        fig.update_layout(**{ax: dict(gridcolor=line_color, zerolinecolor=line_color)})

    chart_json = json.dumps(fig.data, cls=plotly.utils.PlotlyJSONEncoder)
    layout_json = json.dumps(fig.layout, cls=plotly.utils.PlotlyJSONEncoder)
    return chart_json, layout_json


@app.route("/")
def index():
    config = load_config()
    watchlist = config["watchlist"]

    selected_ticker = request.args.get("ticker", watchlist[0])
    selected_signal_type = request.args.get("signal_type", "all")
    chart_type = request.args.get("chart_type", "candlestick")
    days = int(request.args.get("days", "60"))

    if selected_ticker not in watchlist:
        selected_ticker = watchlist[0]
    if chart_type not in ("candlestick", "line"):
        chart_type = "candlestick"
    if days not in (30, 60, 90, 180, 365):
        days = 60

    chart_json, layout_json = build_chart(selected_ticker, selected_signal_type, chart_type, days)

    # Get validated signal details for this ticker
    detail_df = signal_detail_report(gain_targets=[5.0, 10.0], lookahead_days=14)
    if not detail_df.empty:
        detail_df = detail_df[detail_df["ticker"] == selected_ticker]
        if selected_signal_type != "all":
            detail_df = detail_df[detail_df["signal_type"] == selected_signal_type]
        signals_list = detail_df.head(50).to_dict("records")
        for sig in signals_list:
            sig["direction"] = "buy"
    else:
        signals_df = get_signals(selected_ticker, days=days)
        if not signals_df.empty and selected_signal_type != "all":
            signals_df = signals_df[signals_df["signal_type"] == selected_signal_type]
        signals_list = signals_df.to_dict("records") if not signals_df.empty else []

    # Add strategy tags and stringify timestamps
    for sig in signals_list:
        sig["strategy_tag"] = _strategy_tag_html(sig.get("signal_type", ""))
        ts = sig.get("timestamp")
        if ts is not None and not isinstance(ts, str):
            sig["timestamp"] = str(ts)

    all_signals = get_signals(days=30)
    if not all_signals.empty and selected_signal_type != "all":
        all_signals = all_signals[all_signals["signal_type"] == selected_signal_type]

    total_signals = len(all_signals) if not all_signals.empty else 0
    buy_signals = len(all_signals[all_signals["direction"] == "buy"]) if not all_signals.empty else 0
    sell_signals = len(all_signals[all_signals["direction"] == "sell"]) if not all_signals.empty else 0

    selected_signal_label = dict(SIGNAL_TYPES).get(selected_signal_type, "All")

    return render("charts",
                  active_tab="charts",
                  watchlist=watchlist,
                  signal_types=SIGNAL_TYPES,
                  selected_ticker=selected_ticker,
                  selected_signal_type=selected_signal_type,
                  selected_signal_label=selected_signal_label,
                  chart_type=chart_type,
                  days=days,
                  chart_json=chart_json,
                  layout_json=layout_json,
                  signals_list=signals_list,
                  total_signals=total_signals,
                  buy_signals=buy_signals,
                  sell_signals=sell_signals)


def _build_sparklines(signals_list: list) -> dict:
    """Batch-fetch prices around each signal (10 days before + 14 after) and build inline SVG sparklines with hover detail."""
    if not signals_list:
        return {}
    con = get_connection()
    import pandas as pd

    pairs = []
    sig_timestamps = {}
    for sig in signals_list:
        entry_date = sig.get("entry_date")
        ticker = sig.get("ticker")
        ts = sig.get("timestamp")
        if entry_date and ticker:
            sid = sig.get("signal_id")
            pairs.append((sid, ticker, str(entry_date), str(ts)[:10] if ts else str(entry_date)))
            sig_timestamps[sid] = str(ts)[:10] if ts else str(entry_date)

    if not pairs:
        con.close()
        return {}

    cases = " UNION ALL ".join(
        f"SELECT {sid} as signal_id, '{t}' as ticker, '{d}'::DATE as entry_date, '{sd}'::DATE as signal_date"
        for sid, t, d, sd in pairs
    )
    prices_df = con.execute(f"""
        WITH requests AS ({cases})
        SELECT r.signal_id, r.signal_date, r.entry_date, dp.date, dp.close, dp.high, dp.low
        FROM requests r
        JOIN daily_prices dp ON dp.ticker = r.ticker
            AND dp.date >= r.signal_date - INTERVAL '15 days'
            AND dp.date <= r.entry_date + INTERVAL '14 days'
        ORDER BY r.signal_id, dp.date
    """).fetchdf()
    con.close()

    sparklines = {}
    SW, SH = 96, 28       # thumbnail size (per spec)
    BW, BH = 300, 130     # hover chart size
    PAD_L, PAD_R, PAD_T, PAD_B = 38, 8, 8, 16  # hover chart padding for labels

    # Dark theme colors
    up_c = "oklch(0.80 0.165 145)"
    down_c = "oklch(0.72 0.185 25)"
    accent_c = "oklch(0.80 0.135 200)"
    line_soft_c = "oklch(0.30 0.010 250 / 0.45)"
    bg_elev = "oklch(0.185 0.012 250)"
    text_mute = "oklch(0.55 0.010 250)"
    text_dim = "oklch(0.74 0.008 250)"

    for sid, ticker, entry_d, sig_d in pairs:
        chunk = prices_df[prices_df["signal_id"] == sid].copy()
        if len(chunk) < 3:
            continue
        dates = chunk["date"].tolist()
        closes = chunk["close"].tolist()
        entry_price = None
        signal_idx = None
        entry_date_parsed = pd.Timestamp(entry_d).date()
        signal_date_parsed = pd.Timestamp(sig_d).date()

        for i, d in enumerate(dates):
            dd = pd.Timestamp(d).date()
            if dd >= entry_date_parsed and entry_price is None:
                entry_price = closes[i]
            if signal_idx is None and dd >= signal_date_parsed:
                signal_idx = i

        if entry_price is None:
            entry_price = closes[0]
        if signal_idx is None:
            signal_idx = 0

        mn = min(closes) * 0.998
        mx = max(closes) * 1.002
        if mx == mn:
            continue
        n = len(closes)
        last_close = closes[-1]
        is_up = last_close >= entry_price
        color = up_c if is_up else down_c
        pct = (last_close - entry_price) / entry_price * 100

        # --- Thumbnail sparkline (spec: 96x28, with area fill, min/max dots, final halo) ---
        pad = 2
        def scale(vals, w, h):
            pts = []
            for i_v, c in enumerate(vals):
                x = round(pad + i_v / max(n - 1, 1) * (w - pad * 2), 1)
                y = round(pad + (h - pad * 2) * (1 - (c - mn) / (mx - mn)), 1)
                pts.append((x, y))
            return pts

        pts_s = scale(closes, SW, SH)
        # Sparkline path
        path_d = " ".join(f"{'M' if i == 0 else 'L'}{p[0]} {p[1]}" for i, p in enumerate(pts_s))
        # Area fill path
        area_d = f"{path_d} L{pts_s[-1][0]} {SH} L{pts_s[0][0]} {SH} Z"
        # Baseline at first point
        base_y = pts_s[0][1]
        # Min/max indices
        min_idx = closes.index(min(closes))
        max_idx = closes.index(max(closes))
        last_idx = n - 1

        grad_id = f"sg{sid}"
        thumb = f'<svg width="{SW}" height="{SH}" viewBox="0 0 {SW} {SH}" style="display:block;overflow:visible">'
        thumb += f'<defs><linearGradient id="{grad_id}" x1="0" x2="0" y1="0" y2="1">'
        thumb += f'<stop offset="0%" stop-color="{color}" stop-opacity=".25"/>'
        thumb += f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        thumb += f'</linearGradient></defs>'
        # area fill
        thumb += f'<path d="{area_d}" fill="url(#{grad_id})"/>'
        # baseline
        thumb += f'<line x1="{pad}" x2="{SW - pad}" y1="{base_y}" y2="{base_y}" stroke="{line_soft_c}" stroke-width="0.5" stroke-dasharray="2 2"/>'
        # price line
        thumb += f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/>'
        # min/max markers
        thumb += f'<circle cx="{pts_s[min_idx][0]}" cy="{pts_s[min_idx][1]}" r="1.4" fill="{down_c}" opacity=".7"/>'
        thumb += f'<circle cx="{pts_s[max_idx][0]}" cy="{pts_s[max_idx][1]}" r="1.4" fill="{up_c}" opacity=".7"/>'
        # final point halo
        lx, ly = pts_s[last_idx]
        thumb += f'<circle cx="{lx}" cy="{ly}" r="3" fill="{color}" opacity=".18"/>'
        thumb += f'<circle cx="{lx}" cy="{ly}" r="1.6" fill="{color}"/>'
        thumb += '</svg>'

        # --- Hover detail chart (dark) ---
        cw = BW - PAD_L - PAD_R
        ch = BH - PAD_T - PAD_B

        def scale_big(vals, w, h):
            pts = []
            for i_v, c in enumerate(vals):
                x = round(PAD_L + i_v / max(n - 1, 1) * w, 1)
                y = round(PAD_T + h - (c - mn) / (mx - mn) * h, 1)
                pts.append((x, y))
            return pts

        pts_b = scale_big(closes, cw, ch)
        sig_x_b = round(PAD_L + signal_idx / max(n - 1, 1) * cw, 1)
        entry_y_b = round(PAD_T + ch - (entry_price - mn) / (mx - mn) * ch, 1)
        t5 = entry_price * 1.05

        big = f'<svg width="{BW}" height="{BH}" style="display:block">'
        big += f'<rect width="{BW}" height="{BH}" fill="{bg_elev}" rx="6"/>'
        # grid lines + price labels
        grid_c = "oklch(0.30 0.010 250 / 0.3)"
        for frac in (0, 0.25, 0.5, 0.75, 1.0):
            price_val = mn + (mx - mn) * (1 - frac)
            gy = round(PAD_T + ch * frac, 1)
            big += f'<line x1="{PAD_L}" y1="{gy}" x2="{BW - PAD_R}" y2="{gy}" stroke="{grid_c}" stroke-width="0.5"/>'
            big += f'<text x="{PAD_L - 4}" y="{gy + 3}" text-anchor="end" font-size="8" fill="{text_mute}">${price_val:.0f}</text>'
        # entry price line
        big += f'<line x1="{PAD_L}" y1="{entry_y_b}" x2="{BW - PAD_R}" y2="{entry_y_b}" stroke="{accent_c}" stroke-width="1" stroke-dasharray="3,2" opacity="0.6"/>'
        big += f'<text x="{BW - PAD_R + 2}" y="{entry_y_b + 3}" font-size="7" fill="{accent_c}">entry</text>'
        # +5% target line
        if mn <= t5 <= mx:
            ty_b = round(PAD_T + ch - (t5 - mn) / (mx - mn) * ch, 1)
            big += f'<line x1="{PAD_L}" y1="{ty_b}" x2="{BW - PAD_R}" y2="{ty_b}" stroke="oklch(0.83 0.150 80)" stroke-width="1" stroke-dasharray="3,2" opacity="0.6"/>'
            big += f'<text x="{BW - PAD_R + 2}" y="{ty_b + 3}" font-size="7" fill="oklch(0.83 0.150 80)">+5%</text>'
        # signal date vertical line
        big += f'<line x1="{sig_x_b}" y1="{PAD_T}" x2="{sig_x_b}" y2="{PAD_T + ch}" stroke="{accent_c}" stroke-width="1" opacity="0.3"/>'
        big += f'<text x="{sig_x_b}" y="{BH - 2}" text-anchor="middle" font-size="7" fill="{accent_c}">signal</text>'
        # price line: before signal in muted, after in color
        pre = [p for i, p in enumerate(pts_b) if i <= signal_idx]
        post = [p for i, p in enumerate(pts_b) if i >= signal_idx]
        if len(pre) > 1:
            big += f'<polyline points="{" ".join(f"{p[0]},{p[1]}" for p in pre)}" fill="none" stroke="{text_mute}" stroke-width="1.5"/>'
        if len(post) > 1:
            big += f'<polyline points="{" ".join(f"{p[0]},{p[1]}" for p in post)}" fill="none" stroke="{color}" stroke-width="2"/>'
        # endpoint dot + label
        lx, ly = pts_b[-1]
        big += f'<circle cx="{lx}" cy="{ly}" r="3" fill="{color}"/>'
        big += f'<text x="{lx}" y="{max(ly - 6, PAD_T + 8)}" text-anchor="middle" font-size="9" font-weight="bold" fill="{color}">{pct:+.1f}%</text>'
        # title
        big += f'<text x="{PAD_L}" y="{PAD_T - 1}" font-size="9" font-weight="bold" fill="{text_dim}">{ticker} — 14d after signal</text>'
        big += '</svg>'

        html = f'<span class="spark-wrap">{thumb}<span class="spark-detail">{big}</span></span>'
        sparklines[sid] = Markup(html)

    return sparklines


@app.route("/buy-signals")
def buy_signals_page():
    all_signals = get_signals(days=90)

    if not all_signals.empty:
        buy_count = len(all_signals[all_signals["direction"] == "buy"])
        unique_tickers = all_signals[all_signals["direction"] == "buy"]["ticker"].nunique()
    else:
        buy_count = 0
        unique_tickers = 0

    # Get win rates lookup (ticker+signal_type -> win_rates, times_fired)
    win_rates_df = signal_win_rates(gain_targets=[5.0, 10.0], lookahead_days=14)
    wr_lookup = {}
    agg_win_5 = []
    if not win_rates_df.empty:
        for _, row in win_rates_df.iterrows():
            key = (row["ticker"], row["signal_type"])
            wr_lookup[key] = {
                "win_rate_5pct": row["win_rate_5pct"],
                "win_rate_10pct": row["win_rate_10pct"],
                "times_fired": int(row["total_signals"]),
            }
            agg_win_5.append(row["win_rate_5pct"])

    agg_win_rate = sum(agg_win_5) / len(agg_win_5) if agg_win_5 else 0.0

    # Get detailed validation results and enrich with win rate
    detail_df = signal_detail_report(gain_targets=[5.0, 10.0], lookahead_days=14)
    if not detail_df.empty:
        buy_signals_list = detail_df.head(50).to_dict("records")
    else:
        if not all_signals.empty:
            buy_only = all_signals[all_signals["direction"] == "buy"].head(50)
            buy_signals_list = buy_only.to_dict("records")
        else:
            buy_signals_list = []

    pending_count = 0
    worst_dd = -0.01  # avoid div by zero

    for sig in buy_signals_list:
        key = (sig.get("ticker"), sig.get("signal_type"))
        if key in wr_lookup:
            sig["win_rate_5pct"] = wr_lookup[key]["win_rate_5pct"]
            sig["win_rate_10pct"] = wr_lookup[key]["win_rate_10pct"]
            sig["times_fired"] = wr_lookup[key]["times_fired"]
        else:
            sig["win_rate_5pct"] = None
            sig["win_rate_10pct"] = None
            sig["times_fired"] = None

        dd = sig.get("max_drawdown_pct")
        if dd is not None and dd < worst_dd:
            worst_dd = dd

        if not sig.get("matured"):
            pending_count += 1

    # Build inline components and stringify timestamps
    for sig in buy_signals_list:
        sig["strategy_tag"] = _strategy_tag_html(sig.get("signal_type", ""))
        sig["win_rate_gauge_5"] = _win_rate_gauge_html(sig.get("win_rate_5pct"))
        sig["win_rate_gauge_10"] = _win_rate_gauge_html(sig.get("win_rate_10pct"))
        sig["drawdown_html"] = _drawdown_bar_html(sig.get("max_drawdown_pct"), worst_dd)
        # Convert timestamp to string for Jinja slicing
        ts = sig.get("timestamp")
        if ts is not None and not isinstance(ts, str):
            sig["timestamp"] = str(ts)

    sparklines = _build_sparklines(buy_signals_list)
    for sig in buy_signals_list:
        sig["sparkline"] = sparklines.get(sig.get("signal_id"), "")

    return render("buy_signals",
                  active_tab="buy_signals",
                  buy_signals_list=buy_signals_list,
                  buy_count=buy_count,
                  unique_tickers=unique_tickers,
                  agg_win_rate=agg_win_rate,
                  pending_count=pending_count)


@app.route("/reliability")
def reliability_page():
    win_rates_df = signal_win_rates(gain_targets=[5.0, 10.0], lookahead_days=14)
    win_rates = win_rates_df.to_dict("records") if not win_rates_df.empty else []

    # Enrich with inline components
    for wr in win_rates:
        wr["strategy_tag"] = _strategy_tag_html(wr.get("signal_type", ""))
        wr["win_rate_gauge_5"] = _win_rate_gauge_html(wr.get("win_rate_5pct"))
        wr["win_rate_gauge_10"] = _win_rate_gauge_html(wr.get("win_rate_10pct"))

    baseline = random_baseline(gain_targets=[5.0, 10.0], lookahead_days=14)

    return render("reliability",
                  active_tab="reliability",
                  win_rates=win_rates,
                  baseline=baseline)


@app.route("/strategies")
def strategies_page():
    config = load_config()
    sig_config = config["signals"]
    tc = sig_config["technical"]

    strategies = [
        {
            "name": "RSI (Relative Strength Index)",
            "tag": _strategy_tag_html("rsi"),
            "direction_label": "Buy + Sell",
            "description": (
                "Measures momentum by comparing the magnitude of recent gains to recent losses over a "
                "rolling window. When RSI crosses below the oversold threshold, the stock is considered "
                "undervalued and a <strong>buy</strong> signal fires. When it crosses above the overbought "
                "threshold, a <strong>sell</strong> signal fires. Only fires on the <em>transition day</em> "
                "&mdash; the day RSI first enters the zone &mdash; not every day it stays there."
            ),
            "params": [
                {"label": "Period", "value": str(tc["rsi"]["period"])},
                {"label": "Oversold", "value": f'&le; {tc["rsi"]["oversold"]}'},
                {"label": "Overbought", "value": f'&ge; {tc["rsi"]["overbought"]}'},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "MACD Crossover",
            "tag": _strategy_tag_html("macd"),
            "direction_label": "Buy + Sell",
            "description": (
                "Tracks the relationship between two exponential moving averages. The MACD line "
                "(fast EMA minus slow EMA) is compared to a signal line (EMA of MACD). When the MACD line "
                "crosses above the signal line, momentum is shifting bullish &rarr; <strong>buy</strong>. "
                "When it crosses below &rarr; <strong>sell</strong>. Only fires on the actual crossover day."
            ),
            "params": [
                {"label": "Fast EMA", "value": str(tc["macd"]["fast"])},
                {"label": "Slow EMA", "value": str(tc["macd"]["slow"])},
                {"label": "Signal EMA", "value": str(tc["macd"]["signal"])},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Moving Average Crossover",
            "tag": _strategy_tag_html("ma_crossover"),
            "direction_label": "Buy + Sell",
            "description": (
                "Compares a fast simple moving average (SMA) against a slow one. When the fast SMA crosses "
                "above the slow SMA, the short-term trend is outpacing the long-term trend &rarr; "
                "<strong>buy</strong>. Crossing below &rarr; <strong>sell</strong>. "
                "Classic trend-following signal that works best in trending markets."
            ),
            "params": [
                {"label": "Fast SMA", "value": str(tc["moving_average_crossover"]["fast_period"])},
                {"label": "Slow SMA", "value": str(tc["moving_average_crossover"]["slow_period"])},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Volume Spike",
            "tag": _strategy_tag_html("volume_spike"),
            "direction_label": "Buy + Sell",
            "description": (
                "Detects abnormally high trading volume by comparing today's volume against the 20-day "
                "average. High volume on a <strong>down day</strong> (close &lt; open) suggests capitulation "
                "selling &rarr; <strong>buy</strong>. High volume on an <strong>up day</strong> suggests "
                "euphoria &rarr; <strong>sell</strong>. Volume alone is directionally ambiguous, "
                "so the price action that day determines the signal direction."
            ),
            "params": [
                {"label": "Multiplier", "value": f'{sig_config["volume"]["spike_multiplier"]}x'},
                {"label": "Lookback", "value": "20 days"},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Percent Change (Volatility-Normalized)",
            "tag": _strategy_tag_html("percent_change"),
            "direction_label": "Buy + Sell",
            "description": (
                "Fires when a single-day move is unusually large <em>relative to the stock's own volatility</em>. "
                "Instead of a fixed percentage threshold (which would trigger constantly on volatile stocks), "
                "the move is divided by the trailing 20-day standard deviation of returns. "
                "A big drop (&ge; N sigma) &rarr; <strong>buy</strong> (mean reversion). "
                "A big surge &rarr; <strong>sell</strong>. Falls back to a fixed threshold if insufficient history."
            ),
            "params": [
                {"label": "Sigma threshold", "value": "2.0&sigma;"},
                {"label": "Lookback", "value": "20 days"},
                {"label": "Fallback", "value": f'{sig_config["percent_change"]["daily_threshold"]}%'},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Market-Adjusted Drop",
            "tag": _strategy_tag_html("adjusted_drop"),
            "direction_label": "Buy only",
            "description": (
                "Isolates stock-specific weakness by subtracting the market return (SPY) from the stock's "
                "daily return. A stock that drops 7% on a day the market drops 2% has an adjusted return of "
                "&minus;5%. When this adjusted return crosses below the threshold, it suggests the stock is "
                "being punished beyond what the market explains &rarr; <strong>buy</strong> (mean reversion)."
            ),
            "params": [
                {"label": "Threshold", "value": f'{sig_config["adjusted_return"]["drop_threshold"]}%'},
                {"label": "Benchmark", "value": "SPY"},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Market-Adjusted Surge",
            "tag": _strategy_tag_html("adjusted_surge"),
            "direction_label": "Sell only",
            "description": (
                "The inverse of adjusted drop. When a stock surges far beyond what the market did that day "
                "(adjusted return exceeds the threshold), it may be overextended &rarr; <strong>sell</strong>. "
                "Same SPY-based benchmark as adjusted drop."
            ),
            "params": [
                {"label": "Threshold", "value": f'+{sig_config["adjusted_return"]["surge_threshold"]}%'},
                {"label": "Benchmark", "value": "SPY"},
                {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
            ],
        },
        {
            "name": "Price Threshold",
            "tag": _strategy_tag_html("price_threshold"),
            "direction_label": "Buy + Sell",
            "description": (
                "User-defined price alerts. You set a ticker, a target price, and a direction (above/below). "
                "When the current price crosses the target, the signal fires. Useful for watching specific "
                "support/resistance levels. Currently configured per-ticker in <code>config.yaml</code>."
            ),
            "params": [
                {"label": "Rules", "value": "Custom per ticker"},
            ],
        },
    ]

    return render("strategies",
                  active_tab="strategies",
                  strategies=strategies)


@app.route("/api/market-status")
def api_market_status():
    from flask import jsonify
    return jsonify(get_market_status())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    from flask import jsonify
    try:
        config = load_config()
        from data.fetcher import fetch_all
        from signals.engine import evaluate_all
        from backtest.validator import rebuild_cache
        fetch_all(config["watchlist"])
        evaluate_all(config)
        val, wr = rebuild_cache()
        return jsonify({"ok": True, "validated": val, "win_rates": wr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def run_dashboard(host="127.0.0.1", port=8050):
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_dashboard()
