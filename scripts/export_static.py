#!/usr/bin/env python3
"""
Export the RapidSift dashboard as a static site.

Renders all pages to _site/ as plain HTML files.
For the Charts tab, pre-renders chart SVGs for every ticker as individual
HTML snippet files so client-side JS can swap them without a server.

Usage:
    python3 scripts/export_static.py

Output:
    _site/
      index.html          (Charts tab, default ticker)
      buy-signals.html
      reliability.html
      strategies.html
      api/market-status.json
      charts/<TICKER>.html (chart SVG snippet per ticker)
"""

import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.app import (
    app, render, load_config, build_chart_svg,
    _strategy_tag_html, _win_rate_gauge_html, _drawdown_bar_html,
    get_market_status, SIGNAL_TYPES, STRATEGY_TONES,
)
from data.db import get_prices, get_signals, get_connection
from backtest.validator import signal_win_rates, signal_detail_report, random_baseline
from signals.engine import COOLDOWN_DAYS

SITE_DIR = ROOT / "_site"


def clean():
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True)
    (SITE_DIR / "charts").mkdir()
    (SITE_DIR / "api").mkdir()


def export_charts_tab():
    """Export the Charts tab for the default ticker, plus chart SVG snippets for all tickers."""
    config = load_config()
    watchlist = config["watchlist"]
    default_ticker = watchlist[0]

    CHART_TYPES = ["line", "candlestick"]
    DAY_OPTIONS = [30, 60, 90, 180, 365]

    total_combos = len(watchlist) * len(CHART_TYPES) * len(DAY_OPTIONS)
    print(f"  Rendering chart SVGs for {len(watchlist)} tickers × {len(CHART_TYPES)} types × {len(DAY_OPTIONS)} ranges = {total_combos} combinations...")

    count = 0
    for ticker in watchlist:
        for ct in CHART_TYPES:
            for days in DAY_OPTIONS:
                svg = build_chart_svg(ticker, "all", ct, days)
                svg_str = str(svg) if svg else '<p class="no-data">No price data available.</p>'
                (SITE_DIR / "charts" / f"{ticker}_{ct}_{days}.html").write_text(svg_str)
                count += 1
        if count % 500 == 0:
            print(f"    {count}/{total_combos} done")
    print(f"    {count}/{total_combos} done")

    # Render the full Charts page for the default ticker (line chart default)
    default_chart_type = "line"
    default_days = 60
    with app.test_request_context("/"):
        chart_svg = build_chart_svg(default_ticker, "all", default_chart_type, default_days)

        detail_df = signal_detail_report(gain_targets=[5.0, 10.0], lookahead_days=14)
        if not detail_df.empty:
            detail_df = detail_df[detail_df["ticker"] == default_ticker]
            signals_list = detail_df.head(200).to_dict("records")
            for sig in signals_list:
                sig["direction"] = "buy"
        else:
            signals_df = get_signals(default_ticker, days=default_days)
            signals_list = signals_df.to_dict("records") if not signals_df.empty else []

        for sig in signals_list:
            sig["strategy_tag"] = _strategy_tag_html(sig.get("signal_type", ""))
            ts = sig.get("timestamp")
            if ts is not None and not isinstance(ts, str):
                sig["timestamp"] = str(ts)

        all_signals = get_signals(days=30)
        total_signals = len(all_signals) if not all_signals.empty else 0
        buy_signals_count = len(all_signals[all_signals["direction"] == "buy"]) if not all_signals.empty else 0
        sell_signals_count = len(all_signals[all_signals["direction"] == "sell"]) if not all_signals.empty else 0

        html = render("charts",
                      active_tab="charts",
                      watchlist=watchlist,
                      signal_types=SIGNAL_TYPES,
                      selected_ticker=default_ticker,
                      selected_signal_type="all",
                      selected_signal_label="All",
                      chart_type=default_chart_type,
                      days=default_days,
                      chart_svg=chart_svg,
                      signals_list=signals_list,
                      total_signals=total_signals,
                      buy_signals=buy_signals_count,
                      sell_signals=sell_signals_count)

    # Inject client-side JS: chart switching via live API + refresh button
    client_js = """
<script>
(function() {
    // ── Chart switching: use live API with fallback to pre-rendered files ──
    var form = document.getElementById('chart-form');
    if (!form) return;

    function loadChart() {
        var ticker = form.querySelector('[name=ticker]').value;
        var chartType = form.querySelector('[name=chart_type]').value;
        var days = form.querySelector('[name=days]').value;
        var chartDiv = document.querySelector('.chart-pad');
        if (!chartDiv) return;

        var titleEl = chartDiv.closest('.card').querySelector('h2');
        if (titleEl) titleEl.textContent = ticker;
        var pill = chartDiv.closest('.card').querySelector('.count-pill');
        if (pill) pill.textContent = days + 'd';

        // Try live API first, fall back to pre-rendered static file
        fetch('/api/chart?ticker=' + ticker + '&type=' + chartType + '&days=' + days)
            .then(function(r) {
                if (!r.ok) throw new Error('API error');
                return r.text();
            })
            .then(function(svg) { chartDiv.innerHTML = svg; })
            .catch(function() {
                // Fallback to pre-rendered static file
                fetch('/charts/' + ticker + '_' + chartType + '_' + days + '.html')
                    .then(function(r) { return r.text(); })
                    .then(function(svg) { chartDiv.innerHTML = svg; });
            });
    }

    var selects = form.querySelectorAll('select');
    selects.forEach(function(sel) {
        sel.removeAttribute('onchange');
        sel.addEventListener('change', loadChart);
    });

    // ── Refresh button: call live API ──
    window.refreshData = function(btn) {
        btn.classList.add('loading');
        btn.disabled = true;

        var ticker = form ? form.querySelector('[name=ticker]').value : null;
        var url = '/api/refresh' + (ticker ? '?ticker=' + ticker : '');

        fetch(url, {method: 'POST'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                // Update KPI counts if available
                var kpis = document.querySelectorAll('.kpi-value');
                if (kpis.length >= 3 && data.total_signals !== undefined) {
                    kpis[0].textContent = data.total_signals;
                    kpis[1].textContent = data.buy_signals;
                    kpis[2].textContent = data.sell_signals;
                }
                // Reload the chart with fresh data
                loadChart();
                btn.classList.remove('loading');
                btn.disabled = false;
            })
            .catch(function(err) {
                console.error('Refresh failed:', err);
                btn.classList.remove('loading');
                btn.disabled = false;
            });
    };
})();
</script>
"""
    html = html.replace("</body>", client_js + "</body>")
    (SITE_DIR / "index.html").write_text(html)
    print(f"  index.html written (default: {default_ticker}, line, 60d)")


def export_buy_signals():
    """Export the Buy Signals tab."""
    from dashboard.app import _build_sparklines

    with app.test_request_context("/buy-signals"):
        all_signals = get_signals(days=90)
        if not all_signals.empty:
            buy_count = len(all_signals[all_signals["direction"] == "buy"])
            unique_tickers = all_signals[all_signals["direction"] == "buy"]["ticker"].nunique()
        else:
            buy_count = 0
            unique_tickers = 0

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

        detail_df = signal_detail_report(gain_targets=[5.0, 10.0], lookahead_days=14)
        if not detail_df.empty:
            buy_signals_list = detail_df.head(200).to_dict("records")
        else:
            if not all_signals.empty:
                buy_only = all_signals[all_signals["direction"] == "buy"].head(200)
                buy_signals_list = buy_only.to_dict("records")
            else:
                buy_signals_list = []

        pending_count = 0
        worst_dd = -0.01
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

        for sig in buy_signals_list:
            sig["strategy_tag"] = _strategy_tag_html(sig.get("signal_type", ""))
            sig["win_rate_gauge_5"] = _win_rate_gauge_html(sig.get("win_rate_5pct"))
            sig["win_rate_gauge_10"] = _win_rate_gauge_html(sig.get("win_rate_10pct"))
            sig["drawdown_html"] = _drawdown_bar_html(sig.get("max_drawdown_pct"), worst_dd)
            ts = sig.get("timestamp")
            if ts is not None and not isinstance(ts, str):
                sig["timestamp"] = str(ts)

        sparklines = _build_sparklines(buy_signals_list)
        for sig in buy_signals_list:
            sig["sparkline"] = sparklines.get(sig.get("signal_id"), "")

        html = render("buy_signals",
                      active_tab="buy_signals",
                      buy_signals_list=buy_signals_list,
                      buy_count=buy_count,
                      unique_tickers=unique_tickers,
                      agg_win_rate=agg_win_rate,
                      pending_count=pending_count)

    (SITE_DIR / "buy-signals.html").write_text(html)
    print("  buy-signals.html written")


def export_reliability():
    """Export the Signal Reliability tab."""
    with app.test_request_context("/reliability"):
        win_rates_df = signal_win_rates(gain_targets=[5.0, 10.0], lookahead_days=14)
        win_rates = win_rates_df.to_dict("records") if not win_rates_df.empty else []
        for wr in win_rates:
            wr["strategy_tag"] = _strategy_tag_html(wr.get("signal_type", ""))
            wr["win_rate_gauge_5"] = _win_rate_gauge_html(wr.get("win_rate_5pct"))
            wr["win_rate_gauge_10"] = _win_rate_gauge_html(wr.get("win_rate_10pct"))

        baseline = random_baseline(gain_targets=[5.0, 10.0], lookahead_days=14)
        html = render("reliability",
                      active_tab="reliability",
                      win_rates=win_rates,
                      baseline=baseline)

    (SITE_DIR / "reliability.html").write_text(html)
    print("  reliability.html written")


def export_strategies():
    """Export the Strategies tab."""
    with app.test_request_context("/strategies"):
        config = load_config()
        sig_config = config["signals"]
        tc = sig_config["technical"]

        strategies = [
            {
                "name": "RSI (Relative Strength Index)",
                "tag": _strategy_tag_html("rsi"),
                "direction_label": "Buy + Sell",
                "description": "Measures momentum by comparing the magnitude of recent gains to recent losses over a rolling window. When RSI crosses below the oversold threshold, a <strong>buy</strong> signal fires. When it crosses above the overbought threshold, a <strong>sell</strong> signal fires. Only fires on the <em>transition day</em>.",
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
                "description": "Tracks the relationship between two exponential moving averages. When the MACD line crosses above the signal line &rarr; <strong>buy</strong>. When it crosses below &rarr; <strong>sell</strong>.",
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
                "description": "When the fast SMA crosses above the slow SMA &rarr; <strong>buy</strong>. Crossing below &rarr; <strong>sell</strong>.",
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
                "description": "Detects abnormally high volume vs 20-day average. Down day + spike &rarr; <strong>buy</strong>. Up day + spike &rarr; <strong>sell</strong>.",
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
                "description": "Fires when a single-day move exceeds N standard deviations of trailing 20-day returns. Big drop &rarr; <strong>buy</strong>. Big surge &rarr; <strong>sell</strong>.",
                "params": [
                    {"label": "Sigma", "value": "2.0&sigma;"},
                    {"label": "Lookback", "value": "20 days"},
                    {"label": "Fallback", "value": f'{sig_config["percent_change"]["daily_threshold"]}%'},
                    {"label": "Cooldown", "value": f"{COOLDOWN_DAYS}d"},
                ],
            },
            {
                "name": "Market-Adjusted Drop",
                "tag": _strategy_tag_html("adjusted_drop"),
                "direction_label": "Buy only",
                "description": "Stock return minus SPY return. When adjusted return crosses below threshold &rarr; <strong>buy</strong>.",
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
                "description": "Inverse of adjusted drop. When adjusted return exceeds threshold &rarr; <strong>sell</strong>.",
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
                "description": "User-defined price alerts per ticker.",
                "params": [{"label": "Rules", "value": "Custom per ticker"}],
            },
        ]

        html = render("strategies",
                      active_tab="strategies",
                      strategies=strategies)

    (SITE_DIR / "strategies.html").write_text(html)
    print("  strategies.html written")


def export_market_status():
    """Export a static market status JSON snapshot."""
    status = get_market_status()
    status["generated_at"] = datetime.utcnow().isoformat() + "Z"
    (SITE_DIR / "api" / "market-status.json").write_text(json.dumps(status))
    print("  api/market-status.json written")


def patch_links(html_file: Path):
    """Patch Flask route links to .html file links for static serving."""
    text = html_file.read_text()
    text = text.replace('href="/"', 'href="/index.html"')
    text = text.replace('href="/buy-signals"', 'href="/buy-signals.html"')
    text = text.replace('href="/reliability"', 'href="/reliability.html"')
    text = text.replace('href="/strategies"', 'href="/strategies.html"')
    # Remove auto-refresh meta tag — user clicks refresh manually
    text = text.replace('<meta http-equiv="refresh" content="300">', '')
    # Neuter the original refreshData — our injected JS overrides window.refreshData
    text = text.replace("fetch('/api/refresh', {method: 'POST'})", "Promise.resolve({json:()=>Promise.resolve({})})")
    html_file.write_text(text)


def main():
    print("RapidSift static export")
    print("=" * 40)

    clean()
    print("Exporting pages...")

    export_charts_tab()
    export_buy_signals()
    export_reliability()
    export_strategies()
    export_market_status()

    # Patch all HTML links for static serving
    print("Patching links for static serving...")
    for html_file in SITE_DIR.glob("*.html"):
        patch_links(html_file)

    total_files = len(list(SITE_DIR.rglob("*")))
    total_size = sum(f.stat().st_size for f in SITE_DIR.rglob("*") if f.is_file())
    print(f"\nDone! {total_files} files, {total_size / 1024:.0f} KB total")
    print(f"Output: {SITE_DIR}")


if __name__ == "__main__":
    main()
