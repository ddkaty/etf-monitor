"""回测第 2 步:下载 data/entity_map.json 中全部代码的复权日线与拆股记录到 backtest/cache/。"""
import os, json
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, "cache")

if __name__ == "__main__":
    os.makedirs(C, exist_ok=True)
    emap = json.load(open(os.path.join(HERE, "..", "data", "entity_map.json")))
    tickers = sorted({v["entity"] for v in emap.values() if v["entity"]} | {"SPY"})
    df = yf.download(tickers, start="2016-06-01", auto_adjust=True, actions=True, progress=False, threads=True)
    close = df["Close"].dropna(how="all")
    close.to_pickle(os.path.join(C, "close.pkl"))
    df["Stock Splits"].fillna(0).to_pickle(os.path.join(C, "splits.pkl"))
    print(len(tickers), "个代码,", close.index[0].date(), "→", close.index[-1].date())
    print("无数据:", [t for t in tickers if t not in close.columns or close[t].dropna().empty])
