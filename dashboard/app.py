from flask import Flask, render_template_string
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.utils
import json
from data.db import get_prices, get_signals, get_connection
from signals.technical import compute_rsi, compute_macd, compute_sma

app = Flask(__name__)

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Market Monitor</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d4aa; }
        .container { max-width: 1400px; margin: 0 auto; }
        .ticker-section { margin-bottom: 40px; background: #16213e; border-radius: 12px; padding: 20px; }
        .ticker-title { font-size: 1.4em; color: #00d4aa; margin-bottom: 10px; }
        .signals-table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        .signals-table th, .signals-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }
        .signals-table th { color: #888; }
        .buy { color: #28a745; font-weight: bold; }
        .sell { color: #dc3545; font-weight: bold; }
        .alert { color: #ffc107; font-weight: bold; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #0f3460; padding: 15px; border-radius: 8px; }
        .stat-value { font-size: 1.5em; font-weight: bold; color: #00d4aa; }
        .stat-label { color: #888; font-size: 0.9em; }
        .refresh-note { color: #666; font-size: 0.85em; margin-top: 10px; }
        nav { margin-bottom: 20px; }
        nav a { color: #00d4aa; margin-right: 15px; text-decoration: none; }
    </style>
    <meta http-equiv="refresh" content="300">
</head>
<body>
<div class="container">
    <h1>Market Monitor Dashboard</h1>
    <p class="refresh-note">Auto-refreshes every 5 minutes</p>

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
            <div class="stat-label">Buy Signals (30d)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ sell_signals }}</div>
            <div class="stat-label">Sell Signals (30d)</div>
        </div>
    </div>

    {% for ticker_data in tickers %}
    <div class="ticker-section">
        <div class="ticker-title">{{ ticker_data.ticker }}</div>
        <div id="chart-{{ ticker_data.ticker }}"></div>
        <script>
            var data = {{ ticker_data.chart_json | safe }};
            var layout = {{ ticker_data.layout_json | safe }};
            Plotly.newPlot('chart-{{ ticker_data.ticker }}', data, layout, {responsive: true});
        </script>

        {% if ticker_data.signals %}
        <table class="signals-table">
            <tr><th>Time</th><th>Signal</th><th>Type</th><th>Detail</th></tr>
            {% for sig in ticker_data.signals %}
            <tr>
                <td>{{ sig.timestamp }}</td>
                <td class="{{ sig.direction }}">{{ sig.direction|upper }}</td>
                <td>{{ sig.signal_type }}</td>
                <td>{{ sig.details }}</td>
            </tr>
            {% endfor %}
        </table>
        {% endif %}
    </div>
    {% endfor %}
</div>
</body>
</html>
"""


def build_chart(ticker: str):
    df = get_prices(ticker, days=60)
    if df.empty:
        return None, None

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2],
                        vertical_spacing=0.05,
                        subplot_titles=[f"{ticker} Price", "RSI", "Volume"])

    fig.add_trace(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price"
    ), row=1, col=1)

    sma20 = compute_sma(df, 20)
    sma50 = compute_sma(df, 50)
    fig.add_trace(go.Scatter(x=df["date"], y=sma20, name="SMA20",
                             line=dict(color="#ffaa00", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=sma50, name="SMA50",
                             line=dict(color="#00aaff", width=1)), row=1, col=1)

    signals_df = get_signals(ticker, days=60)
    if not signals_df.empty:
        buy_sigs = signals_df[signals_df["direction"] == "buy"]
        sell_sigs = signals_df[signals_df["direction"] == "sell"]
        if not buy_sigs.empty:
            fig.add_trace(go.Scatter(
                x=buy_sigs["timestamp"], y=[df[df["date"] <= t]["close"].iloc[-1] if not df[df["date"] <= t].empty else None for t in buy_sigs["timestamp"]],
                mode="markers", name="Buy Signal",
                marker=dict(symbol="triangle-up", size=12, color="#28a745")
            ), row=1, col=1)
        if not sell_sigs.empty:
            fig.add_trace(go.Scatter(
                x=sell_sigs["timestamp"], y=[df[df["date"] <= t]["close"].iloc[-1] if not df[df["date"] <= t].empty else None for t in sell_sigs["timestamp"]],
                mode="markers", name="Sell Signal",
                marker=dict(symbol="triangle-down", size=12, color="#dc3545")
            ), row=1, col=1)

    rsi = compute_rsi(df, 14)
    fig.add_trace(go.Scatter(x=df["date"], y=rsi, name="RSI",
                             line=dict(color="#aa55ff")), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)

    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], name="Volume",
                         marker_color="#4a90d9"), row=3, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=600,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#16213e",
        plot_bgcolor="#16213e",
    )

    chart_json = json.dumps(fig.data, cls=plotly.utils.PlotlyJSONEncoder)
    layout_json = json.dumps(fig.layout, cls=plotly.utils.PlotlyJSONEncoder)
    return chart_json, layout_json


@app.route("/")
def index():
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    watchlist = config["watchlist"]
    all_signals = get_signals(days=30)

    tickers_data = []
    for ticker in watchlist:
        chart_json, layout_json = build_chart(ticker)
        ticker_signals = get_signals(ticker, days=30)
        tickers_data.append({
            "ticker": ticker,
            "chart_json": chart_json or "[]",
            "layout_json": layout_json or "{}",
            "signals": ticker_signals.to_dict("records") if not ticker_signals.empty else []
        })

    total_signals = len(all_signals)
    buy_signals = len(all_signals[all_signals["direction"] == "buy"]) if not all_signals.empty else 0
    sell_signals = len(all_signals[all_signals["direction"] == "sell"]) if not all_signals.empty else 0

    return render_template_string(TEMPLATE,
                                  watchlist=watchlist,
                                  tickers=tickers_data,
                                  total_signals=total_signals,
                                  buy_signals=buy_signals,
                                  sell_signals=sell_signals)


@app.route("/signals")
def signals_page():
    signals_df = get_signals(days=30)
    return signals_df.to_html() if not signals_df.empty else "<p>No signals yet.</p>"


def run_dashboard(host="127.0.0.1", port=8050):
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_dashboard()
