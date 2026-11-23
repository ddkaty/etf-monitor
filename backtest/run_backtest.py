"""回测第 3 步:三个问题
A. 照抄前 N 大持仓能否跑赢标普 500
B. 哪类 13F 动作之后有超额收益(事件研究)
C. 现价是否低于大师当季均价,对后续收益有无影响
所有买入均发生在 13F 披露日之后的第一个交易日收盘。基准为 SPY 含分红收益。
"""
import os, json, glob, math, collections
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, "cache")
NAMES = {"Buffett": "巴菲特", "Duan": "段永平", "LiLu": "李录", "Hohn": "Chris Hohn",
         "Akre": "Chuck Akre", "Dorsey": "Pat Dorsey", "Russo": "Tom Russo"}

close = pd.read_pickle(os.path.join(C, "close.pkl")).ffill()
splits = pd.read_pickle(os.path.join(C, "splits.pkl"))
D = os.path.join(HERE, "..", "data")
emap = json.load(open(os.path.join(D, "entity_map.json")))
BAD = {"ROKU", "ASHTY", "SEMR"}
days = close.index
spy = close["SPY"]


def has_px(ent, d):
    return ent and ent not in BAD and ent in close.columns and not np.isnan(close[ent].get(d, np.nan))


def entry_day(filing_date):
    i = days.searchsorted(pd.Timestamp(filing_date), side="right")
    return days[i] if i < len(days) else None


def split_factor(ent, d0, d1):
    if ent not in splits.columns:
        return 1.0
    s = splits[ent][(splits.index > d0) & (splits.index <= d1)]
    s = s[s > 0]
    return float(np.prod(s.values)) if len(s) else 1.0


# ---------- 载入并转成"实体"口径 ----------
filings = collections.defaultdict(list)
for p in sorted(glob.glob(os.path.join(D, "13f", "*.json"))):
    d = json.load(open(p))
    pos = {}
    for h in d["holdings"]:
        m = emap.get(h["cusip"])
        ent = m["entity"] if m and m["entity"] else None
        key = ent or ("?" + h["cusip"])
        e = pos.setdefault(key, {"entity": ent, "issuer": h["issuer"], "weight": 0.0, "units": 0.0})
        e["weight"] += h["weight"]; e["units"] += h["shares"] * (m["factor"] if m else 1)
    d["pos"] = pos
    d["entry"] = entry_day(d["filing_date"])
    filings[d["key"]].append(d)

# ---------- A. 照抄组合 ----------
def clone(key, n=5, weighting="equal"):
    fs = [f for f in filings[key] if f["entry"] is not None]
    curve, per, cover = [], [], []
    for i, f in enumerate(fs):
        t0 = f["entry"]; t1 = fs[i + 1]["entry"] if i + 1 < len(fs) else days[-1]
        if t1 <= t0:
            continue
        top = sorted(f["pos"].values(), key=lambda x: -x["weight"])[:n]
        ok = [x for x in top if has_px(x["entity"], t0)]
        cover.append((sum(x["weight"] for x in ok), sum(x["weight"] for x in top)))
        if not ok:
            continue
        w = np.array([x["weight"] for x in ok]) if weighting == "prop" else np.ones(len(ok))
        w = w / w.sum()
        seg = close.loc[t0:t1, [x["entity"] for x in ok]]
        val = (seg / seg.iloc[0]).values @ w
        curve.append(pd.Series(val, index=seg.index))
        per.append((t0, t1, val[-1] - 1, spy[t1] / spy[t0] - 1))
    eq = [1.0]; idx = [curve[0].index[0]]
    for s in curve:
        base = eq[-1]
        eq.extend((base * s.values[1:]).tolist()); idx.extend(s.index[1:])
    eq = pd.Series(eq, index=idx)
    b = spy.loc[eq.index[0]:eq.index[-1]]; b = b / b.iloc[0]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; bc = b.iloc[-1] ** (1 / yrs) - 1
    mdd = float((eq / eq.cummax() - 1).min()); bmdd = float((b / b.cummax() - 1).min())
    win = np.mean([r > s for _, _, r, s in per])
    def sub(a, z):
        e = eq.loc[a:z]; bb = b.loc[a:z]
        if len(e) < 200: return None
        y = (e.index[-1] - e.index[0]).days / 365.25
        return [float((e.iloc[-1] / e.iloc[0]) ** (1 / y) - 1), float((bb.iloc[-1] / bb.iloc[0]) ** (1 / y) - 1)]
    return {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "years": round(yrs, 1),
            "cagr": float(cagr), "spy_cagr": float(bc), "excess": float(cagr - bc),
            "total": float(eq.iloc[-1] - 1), "spy_total": float(b.iloc[-1] - 1),
            "mdd": mdd, "spy_mdd": bmdd, "win_rate": float(win), "n_periods": len(per),
            "coverage": float(sum(a for a, _ in cover) / sum(z for _, z in cover)),
            "h1": sub("2017-01-01", "2021-12-31"), "h2": sub("2022-01-01", "2026-12-31"),
            "curve": eq, "bench": b}

# ---------- 可跟随度 ----------
def profile(key):
    fs = filings[key]
    n = [len(f["pos"]) for f in fs]
    t5 = [sum(sorted((x["weight"] for x in f["pos"].values()), reverse=True)[:5]) for f in fs]
    turn = []
    for a, b in zip(fs, fs[1:]):
        new = sum(x["weight"] for k, x in b["pos"].items() if k not in a["pos"])
        gone = sum(x["weight"] for k, x in a["pos"].items() if k not in b["pos"])
        turn.append((new + gone) / 2)
    # ≥5% 仓位的平均连续持有季度数
    runs, cur = [], collections.Counter()
    for f in fs:
        big = {k for k, x in f["pos"].items() if x["weight"] >= 5}
        for k in list(cur):
            if k not in big: runs.append(cur.pop(k))
        for k in big: cur[k] += 1
    runs += list(cur.values())
    return {"n_filings": len(fs), "first": fs[0]["report_date"], "median_holdings": int(np.median(n)),
            "top5_avg": float(np.mean(t5)), "turnover_q": float(np.mean(turn)),
            "avg_hold_q_big": float(np.mean(runs)) if runs else None}

# ---------- B/C. 事件研究 ----------
H = {"3m": 63, "6m": 126, "12m": 252}

def fwd_excess(ent, t0):
    i = days.get_loc(t0); out = {}
    last = close[ent].last_valid_index()
    for k, h in H.items():
        if i + h < len(days) and days[i + h] <= last:
            out[k] = float(close[ent].iloc[i + h] / close[ent].iloc[i] - spy.iloc[i + h] / spy.iloc[i])
        else:
            out[k] = None
    return out

events = []
for key, fs in filings.items():
    prev_add = set()
    for i in range(1, len(fs)):
        a, b = fs[i - 1], fs[i]
        t0 = b["entry"]
        if t0 is None: continue
        d0, d1 = pd.Timestamp(a["report_date"]), pd.Timestamp(b["report_date"])
        gap_ok = (d1 - d0).days < 120      # 相邻季度才比较
        qavg = lambda ent: float(close[ent].loc[d0 + pd.Timedelta(days=1):d1].mean())
        cur_add = set()
        def emit(kind, ent, x, extra=None):
            if not has_px(ent, t0): return
            e = {"investor": key, "kind": kind, "entity": ent, "issuer": x["issuer"], "quarter": b["report_date"],
                 "entry": str(t0.date()), "weight": round(x["weight"], 2), **fwd_excess(ent, t0)}
            qa = qavg(ent)
            e["px_ratio"] = float(close[ent][t0] / qa) if qa and not math.isnan(qa) else None
            if extra: e.update(extra)
            events.append(e)
        for k, x in b["pos"].items():
            ent = x["entity"]
            # 基线:所有 ≥5% 的持仓
            if x["weight"] >= 5: emit("HOLD5", ent, x)
            if not gap_ok: continue
            if k not in a["pos"]:
                if x["weight"] >= 5: emit("NEW5", ent, x)
                elif x["weight"] >= 1: emit("NEW1_5", ent, x)
                if x["weight"] >= 1: cur_add.add(k)
            elif ent:
                pu = a["pos"][k]["units"] * split_factor(ent, d0, d1)
                chg = x["units"] / pu - 1 if pu else 0
                if chg >= 0.10 and x["weight"] >= 1: cur_add.add(k)
                if chg >= 0.20 and x["weight"] >= 2: emit("ADD20", ent, x, {"chg": round(chg, 3)})
                if chg >= 0.10 and x["weight"] >= 2 and k in prev_add: emit("ADD_STREAK", ent, x, {"chg": round(chg, 3)})
                if chg <= -0.20 and a["pos"][k]["weight"] >= 3: emit("TRIM20", ent, x, {"chg": round(chg, 3)})
        if gap_ok:
            for k, x in a["pos"].items():
                if k not in b["pos"] and x["weight"] >= 3: emit("EXIT3", x["entity"], x)
        prev_add = cur_add if gap_ok else set()
        b["_buys"] = {k for k in cur_add if b["pos"][k]["entity"]}

# 多人同季买入
byq = collections.defaultdict(lambda: collections.defaultdict(list))
for key, fs in filings.items():
    for f in fs:
        for k in f.get("_buys", ()):
            byq[f["report_date"]][f["pos"][k]["entity"]].append((key, f))
for q, ents in byq.items():
    for ent, lst in ents.items():
        if len(lst) >= 2:
            t0 = max(f["entry"] for _, f in lst)      # 最后一位披露后才可见
            if has_px(ent, t0):
                x = lst[0][1]["pos"][ent] if ent in lst[0][1]["pos"] else next(v for v in lst[0][1]["pos"].values() if v["entity"] == ent)
                events.append({"investor": "+".join(k for k, _ in lst), "kind": "MULTI", "entity": ent, "issuer": x["issuer"],
                               "quarter": q, "entry": str(t0.date()), "weight": None, **fwd_excess(ent, t0), "px_ratio": None})

ev = pd.DataFrame(events)

def stat(df):
    out = {"n": int(len(df))}
    for k in H:
        s = df[k].dropna().astype(float)
        if len(s) == 0: out[k] = None; continue
        t = s.mean() / (s.std(ddof=1) / math.sqrt(len(s))) if len(s) > 2 and s.std(ddof=1) > 0 else None
        out[k] = {"n": int(len(s)), "mean": float(s.mean()), "median": float(s.median()), "hit": float((s > 0).mean()), "t": t}
    return out

LABEL = {"HOLD5": "基线: 持有全部 ≥5% 仓位", "NEW5": "新建仓且权重 ≥5%", "NEW1_5": "新建仓,权重 1%–5%",
         "ADD20": "单季加仓 ≥20%", "ADD_STREAK": "连续两季加仓 ≥10%", "MULTI": "≥2 人同季买入",
         "TRIM20": "减仓 ≥20% (仍持有)", "EXIT3": "清仓原 ≥3% 仓位"}
res = {"signals": {k: {"label": LABEL[k], **stat(ev[ev.kind == k])} for k in LABEL},
       "signals_by_investor": {inv: {k: stat(ev[(ev.kind == k) & (ev.investor == inv)]) for k in ("HOLD5", "NEW5", "ADD20", "EXIT3")} for inv in NAMES}}

buy = ev[ev.kind.isin(["NEW5", "NEW1_5", "ADD20"]) & ev.px_ratio.notna()].copy()
buy = buy[(buy.kind != "NEW1_5") | (buy.weight >= 2)]
buy["bucket"] = pd.cut(buy.px_ratio, [0, 0.95, 1.05, 1.15, 99], labels=["低于均价 5% 以上", "均价 ±5% 内", "高出 5%–15%", "高出 15% 以上"])
res["price_cond"] = {str(b): stat(g) for b, g in buy.groupby("bucket", observed=True)}

res["profile"] = {k: profile(k) for k in NAMES}
res["clone"] = {}
curves = {}
for k in NAMES:
    res["clone"][k] = {}
    for tag, (n, w) in {"top5_eq": (5, "equal"), "top10_eq": (10, "equal"), "top5_prop": (5, "prop"), "top1": (1, "equal")}.items():
        r = clone(k, n, w)
        if tag == "top5_eq":
            m = r["curve"].resample("ME").last(); bm = r["bench"].resample("ME").last()
            curves[k] = {"dates": [str(d.date()) for d in m.index], "clone": [round(v, 4) for v in m.values], "spy": [round(v, 4) for v in bm.values]}
        r.pop("curve"); r.pop("bench"); res["clone"][k][tag] = r
res["curves"] = curves
res["names"] = NAMES
res["data_end"] = str(days[-1].date())
ev.to_csv(os.path.join(HERE, "events.csv"), index=False)
json.dump(res, open(os.path.join(HERE, "results.json"), "w"), ensure_ascii=False, indent=1)

# ---------- 打印 ----------
pc = lambda v: "  —  " if v is None else f"{v*100:+6.1f}%"
print("== 可跟随度")
for k, p in res["profile"].items():
    print(f"{NAMES[k]:11s} 首期{p['first']} 持仓中位数{p['median_holdings']:3d} Top5 {p['top5_avg']:.0f}% 季度换手{p['turnover_q']:.1f}% 重仓平均持有{p['avg_hold_q_big']:.1f}季")
print("\n== A. 照抄 (年化 / SPY / 超额 / 最大回撤 / 季度胜率 / 覆盖率)")
for k in NAMES:
    for tag in ("top5_eq", "top10_eq", "top5_prop", "top1"):
        r = res["clone"][k][tag]
        print(f"{NAMES[k]:11s} {tag:9s} {r['start']}起 {pc(r['cagr'])} {pc(r['spy_cagr'])} {pc(r['excess'])} MDD{pc(r['mdd'])}(SPY{pc(r['spy_mdd'])}) 胜率{r['win_rate']*100:.0f}% 覆盖{r['coverage']*100:.0f}%  前半{[round(x*100,1) for x in r['h1']] if r['h1'] else None} 后半{[round(x*100,1) for x in r['h2']] if r['h2'] else None}")
print("\n== B. 信号 (n / 平均超额 / 中位数 / 胜率 / t)")
for k, s in res["signals"].items():
    line = f"{s['label']:22s} n={s['n']:4d} "
    for h in H:
        x = s[h]
        line += f"| {h}: " + ("—" if not x else f"n{x['n']} {pc(x['mean'])} 中位{pc(x['median'])} 胜{x['hit']*100:.0f}% t={x['t']:.1f}" if x['t'] is not None else f"n{x['n']} {pc(x['mean'])}")
    print(line)
print("\n== B2. 分投资者 12m 平均超额 (n)")
for inv, d in res["signals_by_investor"].items():
    print(f"{NAMES[inv]:11s} " + " | ".join(f"{k}: " + ("—" if not d[k]['12m'] else f"{pc(d[k]['12m']['mean'])}(n{d[k]['12m']['n']})") for k in d))
print("\n== C. 价格条件 (买入类事件)")
for b, s in res["price_cond"].items():
    print(f"{b:12s} n={s['n']:3d} " + " ".join(f"| {h}: " + ("—" if not s[h] else f"{pc(s[h]['mean'])} 中位{pc(s[h]['median'])} 胜{s[h]['hit']*100:.0f}% n{s[h]['n']}") for h in ("6m", "12m")))
