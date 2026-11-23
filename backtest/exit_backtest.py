"""2.0 第 1 步:出场规则回测

对买入类信号(新建仓 ≥5%、单季加仓 ≥20% 且权重 ≥2%)比较不同出场方式。
入场统一为 13F 披露后第一个交易日收盘。只用入场后至少有 24 个月数据的信号,
保证每种规则都能走完。超额 = 个股收益 − 同期 SPY 收益(均含分红)。
先运行 backtest/prices.py 生成行情缓存。
"""
import os, json, glob, math, collections
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..", "data")
close = pd.read_pickle(os.path.join(HERE, "cache", "close.pkl")).ffill()
splits = pd.read_pickle(os.path.join(HERE, "cache", "splits.pkl"))
emap = json.load(open(os.path.join(D, "entity_map.json")))
BAD = {"ROKU", "ASHTY", "SEMR"}
days, spy = close.index, close["SPY"]
CAP = 504          # 最长持有 24 个月


def entry_idx(filing_date):
    i = days.searchsorted(pd.Timestamp(filing_date), side="right")
    return i if i < len(days) else None


def split_factor(ent, d0, d1):
    if ent not in splits.columns:
        return 1.0
    s = splits[ent][(splits.index > d0) & (splits.index <= d1)]
    s = s[s > 0]
    return float(np.prod(s.values)) if len(s) else 1.0


filings = collections.defaultdict(list)
for p in sorted(glob.glob(os.path.join(D, "13f", "*.json"))):
    d = json.load(open(p))
    pos = {}
    for h in d["holdings"]:
        m = emap.get(h["cusip"]) or {}
        ent = m.get("entity")
        k = ent or "?" + h["cusip"]
        e = pos.setdefault(k, {"entity": ent, "weight": 0.0, "units": 0.0})
        e["weight"] += h["weight"]; e["units"] += h["shares"] * m.get("factor", 1)
    d["pos"] = pos
    filings[d["key"]].append(d)

events = []
for key, fs in filings.items():
    chg = []                                  # 每期相对上期的股数变动 {实体: 变动}, -1 = 清仓
    for a, b in zip(fs, fs[1:]):
        d0, d1 = pd.Timestamp(a["report_date"]), pd.Timestamp(b["report_date"])
        c = {}
        for k, x in b["pos"].items():
            if k in a["pos"] and x["entity"]:
                pu = a["pos"][k]["units"] * split_factor(x["entity"], d0, d1)
                c[k] = x["units"] / pu - 1 if pu else 0.0
            elif k not in a["pos"]:
                c[k] = None
        for k in a["pos"]:
            if k not in b["pos"]:
                c[k] = -1.0
        chg.append((b, c, (d1 - d0).days < 120))
    for j, (b, c, ok) in enumerate(chg):
        if not ok:
            continue
        for k, v in c.items():
            x = b["pos"].get(k)
            if not x or not x["entity"] or x["entity"] in BAD or x["entity"] not in close.columns:
                continue
            kind = "NEW" if v is None and x["weight"] >= 5 else "ADD" if v is not None and v >= 0.2 and x["weight"] >= 2 else None
            i0 = entry_idx(b["filing_date"])
            if not kind or i0 is None or i0 + CAP >= len(days) or np.isnan(close[x["entity"]].iloc[i0]):
                continue
            # 之后该投资者首次减仓 ≥20% / 首次清仓 的披露日
            trim_i = exit_i = None
            for b2, c2, _ in chg[j + 1:]:
                v2 = c2.get(k)
                if v2 is not None and v2 <= -0.2 and trim_i is None:
                    trim_i = entry_idx(b2["filing_date"])
                if v2 == -1.0:
                    exit_i = entry_idx(b2["filing_date"]); break
            events.append({"investor": key, "kind": kind, "ent": x["entity"], "i0": i0, "trim_i": trim_i, "exit_i": exit_i})


def run(rule, only=None):
    out = []
    for e in events:
        if only and e["investor"] not in only:
            continue
        px = close[e["ent"]].values[e["i0"]:e["i0"] + CAP + 1]
        if np.isnan(px).any():
            continue
        n = rule(e, px)
        n = max(1, min(int(n), CAP))
        r = px[n] / px[0] - 1
        s = spy.values[e["i0"] + n] / spy.values[e["i0"]] - 1
        out.append((r - s, r, n))
    a = np.array(out)
    ex, yrs = a[:, 0], a[:, 2] / 252
    return {"n": len(a), "mean_excess": float(ex.mean()), "median_excess": float(np.median(ex)),
            "hit": float((ex > 0).mean()), "avg_months": float(a[:, 2].mean() / 21),
            "excess_per_year": float(ex.sum() / yrs.sum()), "worst": float(a[:, 1].min()),
            "mean_return": float(a[:, 1].mean())}


def fixed(n):
    return lambda e, px: n

def on_filing(field, cap=CAP):
    return lambda e, px: min(e[field] - e["i0"], cap) if e[field] is not None else cap

def stop(pct, cap):
    def f(e, px):
        hit = np.where(px[1:cap + 1] <= px[0] * (1 - pct))[0]
        return hit[0] + 1 if len(hit) else cap
    return f

def trailing(pct, cap):
    def f(e, px):
        peak = np.maximum.accumulate(px[:cap + 1])
        hit = np.where(px[1:cap + 1] <= peak[1:cap + 1] * (1 - pct))[0]
        return hit[0] + 1 if len(hit) else cap
    return f

def first_of(*rules):
    return lambda e, px: min(r(e, px) for r in rules)

RULES = {
    "固定持有 6 个月": fixed(126),
    "固定持有 12 个月": fixed(252),
    "固定持有 24 个月": fixed(504),
    "大师减仓 ≥20% 即走(最长 24 个月)": on_filing("trim_i"),
    "大师减仓 ≥20% 即走(最长 12 个月)": on_filing("trim_i", 252),
    "大师清仓才走(最长 24 个月)": on_filing("exit_i"),
    "跌破入场价 15% 止损,否则 12 个月": stop(0.15, 252),
    "跌破入场价 25% 止损,否则 12 个月": stop(0.25, 252),
    "跌破入场价 25% 止损,否则 24 个月": stop(0.25, 504),
    "从最高点回撤 20% 止盈止损(最长 24 个月)": trailing(0.20, 504),
    "从最高点回撤 30% 止盈止损(最长 24 个月)": trailing(0.30, 504),
    "大师减仓或跌破 25%,先到先走(最长 24 个月)": first_of(on_filing("trim_i"), stop(0.25, 504)),
    "大师减仓或跌破 25%,先到先走(最长 12 个月)": first_of(on_filing("trim_i", 252), stop(0.25, 252)),
}

if __name__ == "__main__":
    res = {k: run(r) for k, r in RULES.items()}
    kinds = collections.Counter(e["kind"] for e in events)
    meta = {"n_events": len(events), "kinds": dict(kinds), "cap_months": 24,
            "first_entry": str(days[min(e["i0"] for e in events)].date()),
            "last_entry": str(days[max(e["i0"] for e in events)].date()),
            "trimmed_within_cap": sum(1 for e in events if e["trim_i"] is not None and e["trim_i"] - e["i0"] <= CAP),
            "by_investor": dict(collections.Counter(e["investor"] for e in events))}
    four = ("Buffett", "Duan", "LiLu", "Hohn")
    res4 = {k: run(r, four) for k, r in RULES.items()}
    json.dump({"meta": meta, "rules": res, "rules_followed_four": res4}, open(os.path.join(HERE, "exit_results.json"), "w"), ensure_ascii=False, indent=1)
    print(meta)
    print(f"{'规则':34s} {'n':>4s} {'平均超额':>8s} {'中位数':>7s} {'跑赢':>5s} {'持有月':>6s} {'每年超额':>8s} {'平均收益':>8s} {'最差一笔':>8s}")
    for k, r in res.items():
        print(f"{k:34s} {r['n']:4d} {r['mean_excess']*100:+8.1f} {r['median_excess']*100:+7.1f} {r['hit']*100:5.0f} {r['avg_months']:6.1f} {r['excess_per_year']*100:+8.1f} {r['mean_return']*100:+8.1f} {r['worst']*100:+8.1f}")
    print("\n== 仅四位跟踪对象")
    for k, r in res4.items():
        print(f"{k:34s} {r['n']:4d} {r['mean_excess']*100:+8.1f} {r['median_excess']*100:+7.1f} {r['hit']*100:5.0f} {r['avg_months']:6.1f} {r['excess_per_year']*100:+8.1f} {r['mean_return']*100:+8.1f} {r['worst']*100:+8.1f}")
