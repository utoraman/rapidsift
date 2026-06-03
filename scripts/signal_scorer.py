"""
Signal Confidence Scoring — three models combined.

Given a new signal, estimates the probability of hitting +5% within 14 trading
days using three complementary approaches:

1. Logistic Regression  — P(win) from signal features (type, sector, market
   regime, stock momentum, volume, volatility)
2. Bayesian Estimate    — prior from signal type WR, updated with ticker-specific
   track record
3. Survival Score       — expected P(hit +5% by day 14) from signal type's
   historical time-to-hit curve

Final confidence = weighted blend of all three.

Usage:
    scorer = SignalScorer()
    scorer.train('data/signal_history.csv')
    score = scorer.score(signal_features)
    # score = {'confidence': 72, 'logistic': 68, 'bayesian': 75, 'survival': 74}
"""

import csv
import math
from collections import defaultdict
from pathlib import Path


# ── Logistic Regression (from scratch, no sklearn needed) ───────────────────

def _sigmoid(z):
    """Numerically stable sigmoid."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    else:
        ez = math.exp(z)
        return ez / (1.0 + ez)


def _train_logistic(X, y, lr=0.05, epochs=500, l2=0.01):
    """
    Train logistic regression with gradient descent + L2 regularization.
    X: list of feature vectors (list of lists)
    y: list of 0/1 labels
    Returns: weight vector (list of floats)
    """
    n = len(X)
    if n == 0:
        return []
    d = len(X[0])
    w = [0.0] * d

    for epoch in range(epochs):
        grad = [0.0] * d
        for i in range(n):
            z = sum(w[j] * X[i][j] for j in range(d))
            p = _sigmoid(z)
            err = p - y[i]
            for j in range(d):
                grad[j] += err * X[i][j]

        for j in range(d):
            grad[j] = grad[j] / n + l2 * w[j]
            w[j] -= lr * grad[j]

    return w


def _predict_logistic(w, x):
    """Predict probability using trained weights."""
    z = sum(w[j] * x[j] for j in range(len(w)))
    return _sigmoid(z)


# ── Feature Encoding ────────────────────────────────────────────────────────

# Signal types for one-hot encoding
SIGNAL_TYPES = [
    "macd", "volume_spike", "gap_and_go", "rel_strength",
    "earnings_drift", "adjusted_drop", "sector_drop", "confluence",
]

# Sectors for one-hot encoding
SECTORS = [
    "XLK", "XLC", "XLY", "XLF", "XLE", "XLV", "XLI",
    "XLB", "XLP", "XLU", "XLRE", "BITO", "SPY",
]


def _encode_features(signal_type, sector, stock_momentum, volume_ratio,
                     volatility, market_regime, ticker_wr):
    """
    Encode a signal into a feature vector for logistic regression.

    Args:
        signal_type: str (e.g., 'adjusted_drop')
        sector: str (e.g., 'XLK')
        stock_momentum: float, 20-day return in %
        volume_ratio: float, signal day volume / 20-day avg
        volatility: float, 20-day daily return std in %
        market_regime: int, 1 if SPY above SMA50, else 0
        ticker_wr: float, ticker's historical WR for this type (0-1)

    Returns:
        list of floats (feature vector)
    """
    features = []

    # One-hot: signal type
    for t in SIGNAL_TYPES:
        features.append(1.0 if signal_type == t else 0.0)

    # One-hot: sector
    for s in SECTORS:
        features.append(1.0 if sector == s else 0.0)

    # Continuous features (normalized)
    features.append(max(-3.0, min(3.0, stock_momentum / 10.0)))   # [-3, 3]
    features.append(max(0.0, min(3.0, (volume_ratio - 1.0) / 2.0)))  # [0, 3]
    features.append(max(0.0, min(3.0, volatility / 3.0)))  # [0, 3]
    features.append(float(market_regime))  # 0 or 1
    features.append(ticker_wr)  # [0, 1]

    # Bias term
    features.append(1.0)

    return features


# ── Bayesian Estimate ───────────────────────────────────────────────────────

def _bayesian_posterior(prior_wr, prior_n, ticker_wins, ticker_total, strength=10):
    """
    Compute Bayesian posterior for a ticker's win rate.

    Uses a Beta-Binomial model:
        prior ~ Beta(alpha0, beta0) derived from signal type's overall WR
        posterior ~ Beta(alpha0 + wins, beta0 + losses)

    Args:
        prior_wr: signal type's overall win rate (0-1)
        prior_n: effective prior sample size (strength of prior belief)
        ticker_wins: number of wins for this ticker+type
        ticker_total: total signals for this ticker+type
        strength: how strongly to weight the prior (default 10)

    Returns:
        float: posterior win rate (0-1)
    """
    alpha0 = prior_wr * strength
    beta0 = (1.0 - prior_wr) * strength
    alpha_post = alpha0 + ticker_wins
    beta_post = beta0 + (ticker_total - ticker_wins)
    return alpha_post / (alpha_post + beta_post)


# ── Survival Analysis ──────────────────────────────────────────────────────

def _build_survival_curves(history):
    """
    Build Kaplan-Meier style survival curves from historical signals.

    For each signal type, computes P(hit +5% by day d) for d = 1..14.

    Args:
        history: list of dicts with 'type', 'result_5', 'days_to_5'

    Returns:
        dict: signal_type -> list of 14 cumulative probabilities
    """
    type_data = defaultdict(list)
    for row in history:
        if row["result_5"] in ("win", "loss"):
            d = row.get("days_to_5")
            if row["result_5"] == "win" and d and not _is_nan(d):
                type_data[row["type"]].append(int(float(d)))
            else:
                type_data[row["type"]].append(None)  # censored at day 14

    curves = {}
    for sig_type, days_list in type_data.items():
        total = len(days_list)
        if total == 0:
            curves[sig_type] = [0.5] * 14
            continue

        cum_probs = []
        for day in range(1, 15):
            hits_by_day = sum(1 for d in days_list if d is not None and d <= day)
            cum_probs.append(hits_by_day / total)
        curves[sig_type] = cum_probs

    return curves


def _is_nan(v):
    try:
        return math.isnan(float(v))
    except (ValueError, TypeError):
        return True


# ── Main Scorer Class ──────────────────────────────────────────────────────

class SignalScorer:
    """Train on historical signals, score new ones."""

    def __init__(self):
        self.weights = []
        self.type_wr = {}           # signal_type -> overall WR
        self.ticker_type_stats = {} # (ticker, type) -> (wins, total)
        self.survival_curves = {}   # type -> [14 cumulative probs]
        self.trained = False

    def train(self, history_path):
        """
        Train all three models from the signal history CSV.

        Args:
            history_path: path to data/signal_history.csv
        """
        history_path = Path(history_path)
        if not history_path.exists():
            print("  SignalScorer: no history file, using defaults")
            self.trained = False
            return

        # Load sector map
        from signals import load_sector_map
        sector_map = load_sector_map()

        # Parse CSV
        rows = []
        with open(history_path, "r") as f:
            for row in csv.DictReader(f):
                if row["result_5"] in ("win", "loss"):
                    rows.append(row)

        if len(rows) < 50:
            print(f"  SignalScorer: only {len(rows)} signals, need 50+")
            self.trained = False
            return

        print(f"  SignalScorer: training on {len(rows)} completed signals")

        # ── 1. Compute base stats ──
        type_wins = defaultdict(int)
        type_total = defaultdict(int)
        ticker_type_wins = defaultdict(int)
        ticker_type_total = defaultdict(int)

        for row in rows:
            t = row["type"]
            tk = row["ticker"]
            win = 1 if row["result_5"] == "win" else 0
            type_wins[t] += win
            type_total[t] += 1
            ticker_type_wins[(tk, t)] += win
            ticker_type_total[(tk, t)] += 1

        self.type_wr = {}
        for t in type_total:
            self.type_wr[t] = type_wins[t] / type_total[t] if type_total[t] > 0 else 0.5

        self.ticker_type_stats = {}
        for key in ticker_type_total:
            self.ticker_type_stats[key] = (ticker_type_wins[key], ticker_type_total[key])

        # ── 2. Train logistic regression ──
        X = []
        y = []
        for row in rows:
            sector = sector_map.get(row["ticker"], "SPY")
            # We don't have all features in the CSV, so use what we have
            # Approximate momentum from current_pct (not perfect but available)
            momentum = float(row.get("current_pct", 0))
            # Approximate volatility from max_drawdown (proxy)
            dd = abs(float(row.get("max_drawdown_pct", 0)))
            vol_proxy = dd / 3.0  # rough proxy
            # Ticker WR for this type
            tk_key = (row["ticker"], row["type"])
            tk_stats = self.ticker_type_stats.get(tk_key, (0, 0))
            tk_wr = tk_stats[0] / tk_stats[1] if tk_stats[1] > 0 else 0.5
            # Market regime: approximate from month (early 2026 was bearish)
            month = int(row["date"][5:7])
            regime = 1 if month >= 3 else 0  # rough proxy

            feat = _encode_features(
                signal_type=row["type"],
                sector=sector,
                stock_momentum=momentum,
                volume_ratio=1.5,  # not in CSV, use neutral
                volatility=vol_proxy,
                market_regime=regime,
                ticker_wr=tk_wr,
            )
            X.append(feat)
            y.append(1 if row["result_5"] == "win" else 0)

        self.weights = _train_logistic(X, y, lr=0.03, epochs=300, l2=0.05)

        # ── 3. Build survival curves ──
        self.survival_curves = _build_survival_curves(rows)

        self.trained = True

        # Report
        for t in sorted(self.type_wr, key=lambda x: -self.type_wr[x]):
            n = type_total[t]
            wr = self.type_wr[t] * 100
            surv = self.survival_curves.get(t, [0]*14)
            d7 = surv[6] * 100 if len(surv) > 6 else 0
            print(f"    {t:20s}  n={n:4d}  wr={wr:5.1f}%  P(hit by d7)={d7:5.1f}%")

    def score(self, signal_type, ticker, sector="SPY",
              stock_momentum=0.0, volume_ratio=1.5, volatility=2.0,
              market_regime=1, days_elapsed=0):
        """
        Score a single signal. Returns dict with confidence + component scores.

        Args:
            signal_type: str
            ticker: str
            sector: str (sector ETF, e.g. 'XLK')
            stock_momentum: 20-day return %
            volume_ratio: signal day volume / 20d avg
            volatility: 20-day daily return std %
            market_regime: 1 if SPY > SMA50, else 0
            days_elapsed: trading days since signal fired (for survival)

        Returns:
            dict: {confidence, logistic, bayesian, survival}
            All values 0-100.
        """
        if not self.trained:
            return {"confidence": 50, "logistic": 50, "bayesian": 50, "survival": 50}

        # ── Logistic score ──
        tk_key = (ticker, signal_type)
        tk_stats = self.ticker_type_stats.get(tk_key, (0, 0))
        tk_wr = tk_stats[0] / tk_stats[1] if tk_stats[1] > 0 else 0.5

        feat = _encode_features(
            signal_type=signal_type,
            sector=sector,
            stock_momentum=stock_momentum,
            volume_ratio=volume_ratio,
            volatility=volatility,
            market_regime=market_regime,
            ticker_wr=tk_wr,
        )
        logistic_p = _predict_logistic(self.weights, feat) if self.weights else 0.5

        # ── Bayesian score ──
        prior_wr = self.type_wr.get(signal_type, 0.65)
        tk_wins, tk_total = tk_stats
        bayesian_p = _bayesian_posterior(prior_wr, 10, tk_wins, tk_total)

        # ── Survival score ──
        curve = self.survival_curves.get(signal_type, [0.65] * 14)
        # P(hit +5% in remaining days)
        if days_elapsed >= 14:
            survival_p = curve[13] if len(curve) >= 14 else 0.5
        else:
            survival_p = curve[13] if len(curve) >= 14 else 0.5
            # If days have elapsed, adjust: P(hit | haven't hit yet)
            if days_elapsed > 0 and days_elapsed < 14:
                p_by_now = curve[days_elapsed - 1] if days_elapsed <= len(curve) else 0
                p_total = curve[13] if len(curve) >= 14 else 0.5
                # Conditional: P(hit in remaining | haven't hit yet)
                if p_by_now < 1.0:
                    survival_p = (p_total - p_by_now) / (1.0 - p_by_now)
                else:
                    survival_p = 0.0

        # ── Composite confidence ──
        # Weight: logistic (feature-rich) 40%, bayesian (ticker-specific) 30%, survival 30%
        confidence = 0.40 * logistic_p + 0.30 * bayesian_p + 0.30 * survival_p

        return {
            "confidence": max(0, min(100, round(confidence * 100))),
            "logistic": max(0, min(100, round(logistic_p * 100))),
            "bayesian": max(0, min(100, round(bayesian_p * 100))),
            "survival": max(0, min(100, round(survival_p * 100))),
        }


# ── Convenience ─────────────────────────────────────────────────────────────

_scorer_instance = None


def get_scorer(history_path=None):
    """Get or create a trained scorer instance."""
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = SignalScorer()
        if history_path is None:
            history_path = Path(__file__).parent.parent / "data" / "signal_history.csv"
        _scorer_instance.train(history_path)
    return _scorer_instance
