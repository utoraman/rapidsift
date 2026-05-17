from flask import Flask, render_template_string, request
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.utils
import json
import yaml
from pathlib import Path
from data.db import get_prices, get_signals, get_connection
from signals.technical import compute_rsi, compute_macd, compute_sma
from backtest.validator import signal_win_rates, signal_detail_report

app = Flask(__name__)

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
]

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Market Monitor</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f8f9fa; color: #212529; }
        h1 { color: #1a73e8; margin-bottom: 4px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .subtitle { color: #666; font-size: 0.85em; margin-bottom: 24px; }

        .tabs { display: flex; gap: 0; margin-bottom: 24px; border-bottom: 2px solid #e8e8e8; }
        .tab { padding: 12px 24px; font-size: 0.95em; font-weight: 600; color: #666; text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
        .tab:hover { color: #1a73e8; }
        .tab.active { color: #1a73e8; border-bottom-color: #1a73e8; }

        .controls { display: flex; gap: 16px; align-items: center; margin-bottom: 24px; flex-wrap: wrap; }
        .control-group { display: flex; flex-direction: column; gap: 4px; }
        .control-group label { font-size: 0.8em; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }
        select { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 0.95em; background: #fff; min-width: 180px; }
        select:focus { outline: none; border-color: #1a73e8; box-shadow: 0 0 0 2px rgba(26,115,232,0.2); }

        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
        .stat-card { background: #fff; padding: 16px; border-radius: 8px; border: 1px solid #e8e8e8; }
        .stat-value { font-size: 1.6em; font-weight: 700; color: #1a73e8; }
        .stat-value.buy-color { color: #0d904f; }
        .stat-label { color: #888; font-size: 0.82em; margin-top: 2px; }

        .chart-section { background: #fff; border-radius: 10px; padding: 24px; border: 1px solid #e8e8e8; margin-bottom: 24px; }
        .chart-title { font-size: 1.2em; font-weight: 600; color: #333; margin-bottom: 12px; }

        .signals-table { width: 100%; border-collapse: collapse; margin-top: 16px; }
        .signals-table th { padding: 10px 12px; text-align: left; border-bottom: 2px solid #e8e8e8; color: #666; font-size: 0.82em; text-transform: uppercase; letter-spacing: 0.5px; }
        .signals-table td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 0.9em; }
        .signals-table tr:hover { background: #f8f9fa; }
        .buy { color: #0d904f; font-weight: 600; }
        .sell { color: #d93025; font-weight: 600; }
        .alert { color: #e37400; font-weight: 600; }

        .signal-badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 0.78em; font-weight: 600; }
        .signal-badge.rsi { background: #ede9fe; color: #6d28d9; }
        .signal-badge.macd { background: #dbeafe; color: #1d4ed8; }
        .signal-badge.ma_crossover { background: #fef3c7; color: #92400e; }
        .signal-badge.percent_change { background: #fee2e2; color: #991b1b; }
        .signal-badge.volume_spike { background: #d1fae5; color: #065f46; }
        .signal-badge.adjusted_drop { background: #ecfdf5; color: #047857; }
        .signal-badge.adjusted_surge { background: #fef2f2; color: #b91c1c; }
        .signal-badge.price_threshold { background: #f3f4f6; color: #374151; }

        .win-rate-bar { display: inline-block; height: 8px; border-radius: 4px; background: #e8e8e8; width: 60px; vertical-align: middle; margin-right: 6px; }
        .win-rate-fill { display: block; height: 100%; border-radius: 4px; }

        .no-data { color: #999; font-style: italic; padding: 40px; text-align: center; }
        tr.clickable { cursor: pointer; }
        tr.clickable:hover { background: #eef2ff; }
    </style>
    <meta http-equiv="refresh" content="300">
</head>
<body>
<div class="container">
    <h1>Market Monitor</h1>
    <p class="subtitle">Auto-refreshes every 5 minutes</p>

    <div class="tabs">
        <a href="/" class="tab {{ 'active' if active_tab == 'charts' }}">Charts</a>
        <a href="/buy-signals" class="tab {{ 'active' if active_tab == 'buy_signals' }}">Buy Signals</a>
        <a href="/reliability" class="tab {{ 'active' if active_tab == 'reliability' }}">Signal Reliability</a>
    </div>

    {% block content %}{% endblock %}
</div>
</body>
</html>
"""

CHARTS_TAB = """
{% extends "base" %}
{% block content %}
    <form class="controls" method="GET" action="/">
        <div class="control-group">
            <label>Ticker</label>
            <select name="ticker" onchange="this.form.submit()">
                {% for t in watchlist %}
                <option value="{{ t }}" {{ 'selected' if t == selected_ticker }}>{{ t }}</option>
                {% endfor %}
            </select>
        </div>
        <div class="control-group">
            <label>Signal Strategy</label>
            <select name="signal_type" onchange="this.form.submit()">
                {% for value, label in signal_types %}
                <option value="{{ value }}" {{ 'selected' if value == selected_signal_type }}>{{ label }}</option>
                {% endfor %}
            </select>
        </div>
    </form>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{{ watchlist|length }}</div>
            <div class="stat-label">Tickers Tracked</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ total_signals }}</div>
            <div class="stat-label">Signals (30d)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ buy_signals }}</div>
            <div class="stat-label">Buy Signals</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ sell_signals }}</div>
            <div class="stat-label">Sell Signals</div>
        </div>
    </div>

    {% if chart_json %}
    <div class="chart-section">
        <div class="chart-title">{{ selected_ticker }}</div>
        <div id="main-chart"></div>
        <script>
            var data = {{ chart_json | safe }};
            var layout = {{ layout_json | safe }};
            Plotly.newPlot('main-chart', data, layout, {responsive: true});
        </script>
    </div>
    {% else %}
    <div class="chart-section"><p class="no-data">No price data available for {{ selected_ticker }}.</p></div>
    {% endif %}

    {% if signals_list %}
    <div class="chart-section">
        <div class="chart-title">Signals — {{ selected_ticker }} {% if selected_signal_type != 'all' %}({{ selected_signal_label }}){% endif %}</div>
        <table class="signals-table">
            <tr><th>Time</th><th>Direction</th><th>Type</th><th>Detail</th><th>Price</th><th>Result (+5%)</th></tr>
            {% for sig in signals_list %}
            <tr>
                <td>{{ sig.timestamp }}</td>
                <td class="{{ sig.direction }}">{{ sig.direction|upper }}</td>
                <td><span class="signal-badge {{ sig.signal_type }}">{{ sig.signal_type }}</span></td>
                <td>{{ sig.details }}</td>
                <td>{{ "%.2f"|format(sig.price_at_signal) }}</td>
                <td>
                    {% if sig.matured is defined and sig.matured %}
                        {% if sig.hit_5pct %}<span class="buy">+5% in {{ sig.days_to_hit_5pct }}d</span>
                        {% else %}<span class="sell">missed</span>{% endif %}
                    {% elif sig.direction == 'buy' %}
                        <span style="color:#999">pending</span>
                    {% else %}
                        <span style="color:#999">—</span>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% else %}
    <div class="chart-section"><p class="no-data">No signals found for this ticker/strategy combination.</p></div>
    {% endif %}
{% endblock %}
"""

BUY_SIGNALS_TAB = """
{% extends "base" %}
{% block content %}
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value buy-color">{{ buy_count }}</div>
            <div class="stat-label">Buy Signals (90d)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ unique_tickers }}</div>
            <div class="stat-label">Tickers with Signals</div>
        </div>
    </div>

    <div class="controls">
        <div class="control-group">
            <label>Gain Target</label>
            <select id="gain-target" onchange="toggleTarget(this.value)">
                <option value="5" selected>+5%</option>
                <option value="10">+10%</option>
            </select>
        </div>
    </div>

    <div class="chart-section">
        <div class="chart-title">Latest Buy Signals</div>
        {% if buy_signals_list %}
        <table class="signals-table">
            <tr>
                <th>#</th><th>Time</th><th>Ticker</th><th>Strategy</th><th>Detail</th>
                <th>Price</th><th>Result</th><th>Win Rate</th><th>Fired</th>
            </tr>
            {% for sig in buy_signals_list %}
            <tr class="clickable" onclick="window.location='/?ticker={{ sig.ticker }}&signal_type={{ sig.signal_type }}'">
                <td>{{ loop.index }}</td>
                <td>{{ sig.timestamp }}</td>
                <td><strong>{{ sig.ticker }}</strong></td>
                <td><span class="signal-badge {{ sig.signal_type }}">{{ sig.signal_type }}</span></td>
                <td>{{ sig.details }}</td>
                <td>${{ "%.2f"|format(sig.price_at_signal) }}</td>
                <td>
                    <span class="target-5">
                    {% if sig.matured is defined and sig.matured %}
                        {% if sig.hit_5pct %}<span class="buy">+5% in {{ sig.days_to_hit_5pct }}d</span>
                        {% else %}<span class="sell">missed</span>{% endif %}
                    {% else %}<span style="color:#999">pending</span>{% endif %}
                    </span>
                    <span class="target-10" style="display:none">
                    {% if sig.matured is defined and sig.matured %}
                        {% if sig.hit_10pct %}<span class="buy">+10% in {{ sig.days_to_hit_10pct }}d</span>
                        {% else %}<span class="sell">missed</span>{% endif %}
                    {% else %}<span style="color:#999">pending</span>{% endif %}
                    </span>
                </td>
                <td>
                    <span class="target-5">
                    {% if sig.win_rate_5pct is defined and sig.win_rate_5pct is not none %}
                        <span class="win-rate-bar"><span class="win-rate-fill" style="width:{{ sig.win_rate_5pct }}%; background:{{ '#0d904f' if sig.win_rate_5pct >= 50 else '#d93025' }}"></span></span>
                        <strong style="color:{{ '#0d904f' if sig.win_rate_5pct >= 50 else '#d93025' }}">{{ sig.win_rate_5pct }}%</strong>
                    {% else %}<span style="color:#999">—</span>{% endif %}
                    </span>
                    <span class="target-10" style="display:none">
                    {% if sig.win_rate_10pct is defined and sig.win_rate_10pct is not none %}
                        <span class="win-rate-bar"><span class="win-rate-fill" style="width:{{ sig.win_rate_10pct }}%; background:{{ '#0d904f' if sig.win_rate_10pct >= 50 else '#d93025' }}"></span></span>
                        <strong style="color:{{ '#0d904f' if sig.win_rate_10pct >= 50 else '#d93025' }}">{{ sig.win_rate_10pct }}%</strong>
                    {% else %}<span style="color:#999">—</span>{% endif %}
                    </span>
                </td>
                <td>
                    {% if sig.times_fired is defined and sig.times_fired is not none %}
                        {{ sig.times_fired }}
                    {% else %}<span style="color:#999">—</span>{% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
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
    <div class="controls">
        <div class="control-group">
            <label>Gain Target</label>
            <select id="gain-target" onchange="toggleTarget(this.value)">
                <option value="5" selected>+5%</option>
                <option value="10">+10%</option>
            </select>
        </div>
    </div>

    {% if win_rates %}
    <div class="chart-section">
        <div class="chart-title">Signal Reliability — within 14 days</div>
        <table class="signals-table">
            <tr><th>Ticker</th><th>Strategy</th><th>Fired</th><th>Win Rate</th><th>Summary</th></tr>
            {% for wr in win_rates %}
            <tr class="clickable" onclick="window.location='/?ticker={{ wr.ticker }}&signal_type={{ wr.signal_type }}'">
                <td><strong>{{ wr.ticker }}</strong></td>
                <td><span class="signal-badge {{ wr.signal_type }}">{{ wr.signal_type }}</span></td>
                <td>{{ wr.total_signals }}</td>
                <td>
                    <span class="target-5">
                        <span class="win-rate-bar"><span class="win-rate-fill" style="width:{{ wr.win_rate_5pct }}%; background:{{ '#0d904f' if wr.win_rate_5pct >= 50 else '#d93025' }}"></span></span>
                        <strong style="color: {{ '#0d904f' if wr.win_rate_5pct >= 50 else '#d93025' }}">{{ wr.win_rate_5pct }}%</strong>
                    </span>
                    <span class="target-10" style="display:none">
                        <span class="win-rate-bar"><span class="win-rate-fill" style="width:{{ wr.win_rate_10pct }}%; background:{{ '#0d904f' if wr.win_rate_10pct >= 50 else '#d93025' }}"></span></span>
                        <strong style="color: {{ '#0d904f' if wr.win_rate_10pct >= 50 else '#d93025' }}">{{ wr.win_rate_10pct }}%</strong>
                    </span>
                </td>
                <td>
                    <span class="target-5">{{ wr.summary_5pct }}</span>
                    <span class="target-10" style="display:none">{{ wr.summary_10pct }}</span>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% else %}
    <div class="chart-section"><p class="no-data">No matured signals yet. Signals need to be at least 14 days old to validate.</p></div>
    {% endif %}

    <script>
    function toggleTarget(val) {
        document.querySelectorAll('.target-5').forEach(el => el.style.display = val === '5' ? '' : 'none');
        document.querySelectorAll('.target-10').forEach(el => el.style.display = val === '10' ? '' : 'none');
    }
    </script>
{% endblock %}
"""


from jinja2 import DictLoader, Environment

jinja_env = Environment(loader=DictLoader({
    "base": TEMPLATE,
    "charts": CHARTS_TAB,
    "buy_signals": BUY_SIGNALS_TAB,
    "reliability": RELIABILITY_TAB,
}))


def render(template_name, **kwargs):
    return jinja_env.get_template(template_name).render(**kwargs)


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_chart(ticker: str, signal_type: str = "all"):
    df = get_prices(ticker, days=60)
    if df.empty:
        return None, None

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2],
                        vertical_spacing=0.05,
                        subplot_titles=["Price", "RSI (14)", "Volume"])

    fig.add_trace(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price"
    ), row=1, col=1)

    sma20 = compute_sma(df, 20)
    sma50 = compute_sma(df, 50)
    fig.add_trace(go.Scatter(x=df["date"], y=sma20, name="SMA20",
                             line=dict(color="#f59e0b", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=sma50, name="SMA50",
                             line=dict(color="#3b82f6", width=1.5)), row=1, col=1)

    signals_df = get_signals(ticker, days=60)
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
                mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", size=12, color="#0d904f")
            ), row=1, col=1)
        if not sell_sigs.empty:
            sell_prices = []
            for t in sell_sigs["timestamp"]:
                matching = df[df["date"] <= t]
                sell_prices.append(matching["close"].iloc[-1] if not matching.empty else None)
            fig.add_trace(go.Scatter(
                x=sell_sigs["timestamp"], y=sell_prices,
                mode="markers", name="Sell",
                marker=dict(symbol="triangle-down", size=12, color="#d93025")
            ), row=1, col=1)

    rsi = compute_rsi(df, 14)
    fig.add_trace(go.Scatter(x=df["date"], y=rsi, name="RSI",
                             line=dict(color="#8b5cf6", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="#d93025", opacity=0.5, row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="#0d904f", opacity=0.5, row=2, col=1)

    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], name="Volume",
                         marker_color="#93c5fd"), row=3, col=1)

    fig.update_layout(
        template="plotly_white",
        height=600,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="-apple-system, BlinkMacSystemFont, sans-serif"),
        margin=dict(t=30, b=20),
    )

    chart_json = json.dumps(fig.data, cls=plotly.utils.PlotlyJSONEncoder)
    layout_json = json.dumps(fig.layout, cls=plotly.utils.PlotlyJSONEncoder)
    return chart_json, layout_json


@app.route("/")
def index():
    config = load_config()
    watchlist = config["watchlist"]

    selected_ticker = request.args.get("ticker", watchlist[0])
    selected_signal_type = request.args.get("signal_type", "all")

    if selected_ticker not in watchlist:
        selected_ticker = watchlist[0]

    chart_json, layout_json = build_chart(selected_ticker, selected_signal_type)

    # Get validated signal details for this ticker
    detail_df = signal_detail_report(gain_targets=[5.0, 10.0], lookahead_days=14)
    if not detail_df.empty:
        detail_df = detail_df[detail_df["ticker"] == selected_ticker]
        if selected_signal_type != "all":
            detail_df = detail_df[detail_df["signal_type"] == selected_signal_type]
        signals_list = detail_df.head(50).to_dict("records")
        # Add direction field for template compatibility
        for sig in signals_list:
            sig["direction"] = "buy"
    else:
        signals_df = get_signals(selected_ticker, days=30)
        if not signals_df.empty and selected_signal_type != "all":
            signals_df = signals_df[signals_df["signal_type"] == selected_signal_type]
        signals_list = signals_df.to_dict("records") if not signals_df.empty else []

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
                  chart_json=chart_json,
                  layout_json=layout_json,
                  signals_list=signals_list,
                  total_signals=total_signals,
                  buy_signals=buy_signals,
                  sell_signals=sell_signals)


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
    if not win_rates_df.empty:
        for _, row in win_rates_df.iterrows():
            key = (row["ticker"], row["signal_type"])
            wr_lookup[key] = {
                "win_rate_5pct": row["win_rate_5pct"],
                "win_rate_10pct": row["win_rate_10pct"],
                "times_fired": int(row["total_signals"]),
            }

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

    return render("buy_signals",
                  active_tab="buy_signals",
                  buy_signals_list=buy_signals_list,
                  buy_count=buy_count,
                  unique_tickers=unique_tickers)


@app.route("/reliability")
def reliability_page():
    win_rates_df = signal_win_rates(gain_targets=[5.0, 10.0], lookahead_days=14)
    win_rates = win_rates_df.to_dict("records") if not win_rates_df.empty else []

    return render("reliability",
                  active_tab="reliability",
                  win_rates=win_rates)


def run_dashboard(host="127.0.0.1", port=8050):
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_dashboard()
