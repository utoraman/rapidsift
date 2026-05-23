#!/usr/bin/env python3
"""Find top 200 US stocks by average daily volume and update config."""
import sys
sys.path.insert(0, "/Users/ufuk/market-monitor")

import yfinance as yf
import yaml
from pathlib import Path

UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "AMD", "INTC", "CSCO", "IBM", "QCOM", "TXN", "AMAT", "MU",
    "LRCX", "KLAC", "MRVL", "SNPS", "CDNS", "PANW", "CRWD", "FTNT", "ZS", "NET",
    "DDOG", "SNOW", "PLTR", "SHOP", "SQ", "COIN", "HOOD", "MSTR", "RIOT", "MARA",
    "NOW", "WDAY", "TEAM", "DOCU", "ZM", "UBER", "LYFT", "ABNB", "DASH", "RBLX",
    "U", "TTWO", "EA", "ATVI", "NFLX", "DIS", "CMCSA", "WBD", "PARA", "FOX",
    "FOXA", "ROKU", "SPOT", "TME",
    # Semiconductors
    "TSM", "ASML", "ARM", "ON", "NXPI", "SWKS", "QRVO", "MPWR", "GFS",
    "SMCI", "IONQ", "RGTI", "WOLF", "SOUN",
    # EV & Auto
    "F", "GM", "RIVN", "LCID", "NIO", "XPEV", "LI", "GOEV", "FSR", "QS",
    "CHPT", "BLNK", "EVGO", "PLUG", "FCEL", "BE",
    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "BX", "KKR",
    "APO", "PYPL", "V", "MA", "AXP", "COF", "DFS", "SYF", "ALLY", "SOFI",
    "NU", "AFRM", "UPST", "LC", "OPEN",
    # Healthcare & Pharma
    "JNJ", "UNH", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN", "GILD", "REGN",
    "VRTX", "MRNA", "BNTX", "NVAX", "DNA", "CRSP", "EDIT", "NTLA", "BEAM",
    "ISRG", "DXCM", "ILMN", "TMO", "DHR", "ABT", "MDT", "SYK", "BSX", "EW",
    "ZBH", "HIMS", "LFMD",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "MPC", "VLO", "PSX", "DVN",
    "FANG", "HAL", "BKR", "KMI", "WMB", "ET", "EPD", "MLP", "TELL",
    # Consumer
    "WMT", "COST", "TGT", "HD", "LOW", "AMZN", "BABA", "JD", "PDD", "SE",
    "CPNG", "GRAB", "MELI", "ETSY",
    "KO", "PEP", "MCD", "SBUX", "CMG", "DPZ", "YUM", "DNUT",
    "NKE", "LULU", "GPS", "ANF", "BBWI",
    "PG", "CL", "KMB", "EL", "MNST",
    # Industrials
    "CAT", "DE", "BA", "LMT", "RTX", "NOC", "GD", "GE", "HON", "MMM",
    "UPS", "FDX", "UNP", "CSX", "NSC",
    # Telecom & Media
    "T", "VZ", "TMUS",
    # REITs & Real Estate
    "AMT", "PLD", "CCI", "EQIX", "DLR", "SPG", "O", "WELL",
    # Crypto-adjacent
    "BITF", "HUT", "CLSK", "CIFR", "BTBT", "CORZ",
    # AI & Robotics
    "PATH", "AI", "BBAI", "IREN", "APLD",
    # Biotech small-cap
    "SAVA", "ASTS", "MNMD", "TLRY",
    # Materials
    "LIN", "APD", "FCX", "NEM", "GOLD", "AA", "CLF", "X", "NUE", "STLD",
    # Chinese ADRs
    "BIDU", "NTES", "BILI", "TAL", "IQ", "FUTU", "TIGR", "ZH",
    # Misc popular
    "AAL", "DAL", "UAL", "LUV", "CCL", "RCL", "NCLH",
    "SNAP", "PINS", "TWLO", "OKTA", "MDB", "ESTC",
    "NVO", "AZN", "SNY", "GSK", "TEVA",
    "VALE", "RIO", "BHP", "SCCO",
    "DKNG", "PENN", "MGM", "WYNN", "LVS",
    "SMMT", "CELH", "PAYO",
    # Additional mid-cap & popular
    "ACN", "INTU", "ADP", "FIS", "FISV", "GPN",
    "ICE", "CME", "NDAQ", "MCO", "SPGI",
    "CARR", "OTIS", "TT", "IR",
    "ENPH", "SEDG", "RUN", "NOVA", "MAXN",
    "LAZR", "LIDR", "OUST",
    "JOBY", "ACHR", "LILM",
    "CAVA", "BROS", "KRUS",
    "FOUR", "BILL", "HUBS", "VEEV", "PCOR",
    "APP", "IS", "RKLB", "LUNR", "RDW",
    "WBA", "CVS",
    "BMO", "TD", "RY", "BNS", "CM",
    "SMFL", "SMR", "LEU", "CCJ", "UEC", "DNN", "UUUU",
    "SOXX", "SOXL",
    # More volume-heavy tickers
    "SPY", "QQQ", "IWM", "DIA", "VOO",
    "XLF", "XLE", "XLK", "XLV", "XLI",
    "TQQQ", "SQQQ", "SPXL", "SPXS",
    "ARKK", "ARKG",
    "SLV", "GLD", "USO", "UNG",
    "TLT", "HYG", "JNK", "LQD",
    "EEM", "EFA", "FXI", "KWEB",
    "VXX", "UVXY", "SVXY",
    "SOXS",
    "BITX",
    "IBIT", "BITO", "ETHE",
]

UNIVERSE = list(set(UNIVERSE))

print(f"Scanning {len(UNIVERSE)} tickers for volume data...")

data = yf.download(UNIVERSE, period="3mo", interval="1d", group_by="ticker", progress=True, threads=True)

results = []
for ticker in UNIVERSE:
    try:
        if len(UNIVERSE) > 1:
            vol = data[ticker]["Volume"]
        else:
            vol = data["Volume"]
        avg_vol = vol.mean()
        if avg_vol > 0:
            results.append((ticker, avg_vol))
    except Exception:
        pass

results.sort(key=lambda x: x[1], reverse=True)

# Filter out ETFs and leveraged products for signal analysis - keep only stocks
etfs_and_etns = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "XLF", "XLE", "XLK", "XLV", "XLI",
    "TQQQ", "SQQQ", "SPXL", "SPXS", "ARKK", "ARKG", "SLV", "GLD", "USO", "UNG",
    "TLT", "HYG", "JNK", "LQD", "EEM", "EFA", "FXI", "KWEB", "VXX", "UVXY",
    "SVXY", "SOXS", "SOXX", "SOXL", "BITX", "IBIT", "BITO", "ETHE",
}

stocks_only = [(t, v) for t, v in results if t not in etfs_and_etns]
top_200 = stocks_only[:200]

print(f"\nFound {len(stocks_only)} stocks with volume data")
print(f"Top 200 by average daily volume:\n")
for i, (ticker, vol) in enumerate(top_200, 1):
    print(f"  {i:3d}. {ticker:6s}  avg vol: {vol:>14,.0f}")

tickers_list = [t for t, _ in top_200]

config_path = Path("/Users/ufuk/market-monitor/config.yaml")
with open(config_path) as f:
    config = yaml.safe_load(f)

config["watchlist"] = tickers_list

with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print(f"\nUpdated config.yaml with {len(tickers_list)} tickers")
