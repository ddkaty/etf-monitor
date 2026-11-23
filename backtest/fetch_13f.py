"""回测第 1 步:补齐七位投资者 2016Q4 以来的 13F(四位跟踪对象 + 三位对照),缓存到 data/13f/。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from follow import INVESTORS, sync_13f

EXTRA = {"Akre": "1112520", "Dorsey": "1671657", "Russo": "860643"}   # 仅用于回测对照

if __name__ == "__main__":
    for key, cik in {**{k: v["cik"] for k, v in INVESTORS.items()}, **EXTRA}.items():
        print("==", key)
        sync_13f(key, cik)
