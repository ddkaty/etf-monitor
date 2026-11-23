"""
大师跟随系统 —— 把四位长期投资者的 SEC 披露变成可操作的研究清单

每天运行一次:
  1. 检查新的 13F(季度持仓)与快速披露(Form 4 / 13D / 13G)
  2. 按回测验证过的规则计算信号(规则与证据见 backtest/ 与看板"依据"页)
  3. 更新价格、重建看板 docs/
  4. 有新披露时发邮件;每周一发一份状态汇总

规则(2016–2026 回测,详见 backtest/results.json):
  跟随  新建仓且权重 ≥5%;单季加仓 ≥20% 且权重 ≥2%
  复查  清仓原权重 ≥3% 的仓位
  忽略  小仓位新建、小幅加仓、减仓
  失效  同一位投资者随后减仓 ≥20% 或清仓
  有效期 披露后 12 个月
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta, timezone
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
F13_DIR = os.path.join(DATA, "13f")
EMAP_PATH = os.path.join(DATA, "entity_map.json")
SIGNALS_PATH = os.path.join(DATA, "signals.json")
FAST_PATH = os.path.join(DATA, "fast_filings.json")
STATE_PATH = os.path.join(HERE, "follow_state.json")

USER_AGENT = "SuperinvestorMonitor your_email@gmail.com"   # SEC 要求带联系方式,可改成自己的
START = "2016-12-31"

INVESTORS = {
    "Buffett": {"cn": "巴菲特", "name": "Warren Buffett", "fund": "Berkshire Hathaway", "cik": "1067983", "color": "#0e7490"},
    "Duan":    {"cn": "段永平", "name": "Duan Yongping", "fund": "H&H International", "cik": "1759760", "color": "#059669"},
    "LiLu":    {"cn": "李录", "name": "Li Lu", "fund": "Himalaya Capital", "cik": "1709323", "color": "#7c3aed"},
    "Hohn":    {"cn": "Chris Hohn", "name": "Chris Hohn", "fund": "TCI Fund", "cik": "1647251", "color": "#c2410c"},
}

RULES = {"new_min_weight": 5.0, "add_min_chg": 0.20, "add_min_weight": 2.0,
         "exit_min_weight": 3.0, "invalidate_cut": 0.20, "active_days": 365}

FAST_FORMS = {"4", "4/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
              "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}

# 同一公司的不同股份类别合并为一个实体: 代码 -> (实体代码, 折算系数)
CLASS_MERGE = {"GOOG": ("GOOGL", 1), "BRK/A": ("BRK-B", 1500), "BRK/B": ("BRK-B", 1),
               "BF/A": ("BF-B", 1), "BF/B": ("BF-B", 1)}

CN_NAMES = {"AAPL": "苹果", "GOOGL": "谷歌", "PDD": "拼多多", "BRK-B": "伯克希尔", "TSLA": "特斯拉",
            "NVDA": "英伟达", "TME": "腾讯音乐", "BAC": "美国银行", "OXY": "西方石油", "AXP": "美国运通",
            "KO": "可口可乐", "CVX": "雪佛龙", "MSFT": "微软", "TSM": "台积电", "BABA": "阿里巴巴",
            "META": "Meta", "AMZN": "亚马逊", "GE": "GE 航空航天", "V": "Visa", "MCO": "穆迪",
            "SPGI": "标普全球", "CP": "加拿大太平洋铁路", "EWBC": "华美银行", "CROX": "Crocs",
            "DIS": "迪士尼", "KHC": "卡夫亨氏", "CNI": "加拿大国家铁路", "MU": "美光", "NTES": "网易"}


# ============ SEC 访问 ============

def sec_get(url, retries=3):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*"}
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(1 + attempt)
    return None


def get_submissions(cik):
    raw = sec_get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")
    return json.loads(raw) if raw else None


def _strip_ns(text):
    text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", text)
    text = re.sub(r'\s\w+:\w+="[^"]*"', "", text)
    return re.sub(r"<(/?)\w+:", r"<\1", text)


def fetch_13f_rows(cik, accession):
    """读取一份 13F 的信息表,返回原始行"""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    idx = sec_get(f"{base}/index.json")
    if not idx:
        return []
    xmls = [i["name"] for i in json.loads(idx).get("directory", {}).get("item", [])
            if i.get("name", "").lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
    xmls.sort(key=lambda n: 0 if "info" in n.lower() else 1)
    for name in xmls:
        raw = sec_get(f"{base}/{name}")
        if not raw or b"infoTable" not in raw:
            time.sleep(0.2)
            continue
        rows = []
        try:
            root = ET.fromstring(_strip_ns(raw.decode("utf-8", errors="ignore")))
        except ET.ParseError:
            return []
        for e in root.findall(".//infoTable"):
            try:
                rows.append({
                    "issuer": (e.findtext("nameOfIssuer") or "").strip(),
                    "cusip": (e.findtext("cusip") or "").strip().upper(),
                    "title_class": (e.findtext("titleOfClass") or "").strip(),
                    "value": int(float(e.findtext("value") or 0)),
                    "shares": int(float(e.findtext(".//sshPrnamt") or 0)),
                    "share_type": (e.findtext(".//sshPrnamtType") or "SH").strip().upper(),
                    "put_call": (e.findtext("putCall") or "").strip(),
                })
            except (ValueError, TypeError):
                continue
        return rows
    return []


def aggregate_rows(rows):
    """同一 CUSIP 多行合并;剔除期权行与非股票行;计算权重"""
    agg = {}
    for h in rows:
        if h["put_call"] or h["share_type"] != "SH" or not h["cusip"]:
            continue
        a = agg.setdefault(h["cusip"], {"cusip": h["cusip"], "issuer": h["issuer"],
                                         "title_class": h["title_class"], "value": 0, "shares": 0})
        a["value"] += h["value"]
        a["shares"] += h["shares"]
    hs = sorted(agg.values(), key=lambda x: -x["value"])
    total = sum(h["value"] for h in hs) or 1
    for h in hs:
        h["weight"] = h["value"] / total * 100
    return hs


def sync_13f(key, cik, log=print):
    """补齐某位投资者 START 以来缺失的 13F。返回本次新增的报告期列表"""
    sub = get_submissions(cik)
    if not sub:
        log(f"   ⚠️ {key}: 无法读取 SEC 提交记录")
        return []
    r = sub["filings"]["recent"]
    orig, amend = {}, {}
    for i, form in enumerate(r["form"]):
        rd = r["reportDate"][i]
        if rd < START or form not in ("13F-HR", "13F-HR/A"):
            continue
        f = {"accession": r["accessionNumber"][i], "filing_date": r["filingDate"][i], "report_date": rd}
        bucket = orig if form == "13F-HR" else amend
        if rd not in bucket or f["filing_date"] < bucket[rd]["filing_date"]:
            bucket[rd] = f
    os.makedirs(F13_DIR, exist_ok=True)
    added = []
    for rd in sorted(orig):
        path = os.path.join(F13_DIR, f"{key}_{rd}.json")
        if os.path.exists(path):
            continue
        f = orig[rd]
        rows = fetch_13f_rows(cik, f["accession"])
        hs = aggregate_rows(rows)
        note = None
        if not hs and rd in amend:      # 原件有误(如全部误标为期权)时改用修正件
            f = amend[rd]
            rows = fetch_13f_rows(cik, f["accession"])
            hs = aggregate_rows(rows)
            note = "原件无有效持仓,改用修正件"
        if not hs:
            log(f"   ⚠️ {key} {rd}: 未读到持仓,下次重试")
            continue
        out = {"key": key, **f, "n_raw_rows": len(rows), "holdings": hs}
        if note:
            out["note"] = note
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        log(f"   ✅ {key} {rd} (披露 {f['filing_date']}): {len(hs)} 只")
        added.append(rd)
        time.sleep(0.25)
    return added


def load_filings(key):
    out = []
    if os.path.isdir(F13_DIR):
        for name in sorted(os.listdir(F13_DIR)):
            if name.startswith(key + "_") and name.endswith(".json"):
                with open(os.path.join(F13_DIR, name), encoding="utf-8") as fh:
                    out.append(json.load(fh))
    return out


# ============ CUSIP → 代码 ============

def load_entity_map():
    if os.path.exists(EMAP_PATH):
        with open(EMAP_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def resolve_cusips(emap, needed, log=print):
    """用 OpenFIGI 补齐未知 CUSIP 的代码。needed: {cusip: issuer}"""
    todo = [c for c in needed if c not in emap]
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]).encode()
        try:
            req = urllib.request.Request("https://api.openfigi.com/v3/mapping", data=body,
                                         headers={"Content-Type": "application/json"})
            res = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:
            log(f"   ⚠️ 代码映射服务不可用({e}),下次重试")
            return
        for c, item in zip(batch, res):
            data = item.get("data") or []
            eq = [x for x in data if x.get("marketSector") == "Equity"] or data
            ticker = eq[0]["ticker"] if eq else None
            if ticker:
                ent, fac = CLASS_MERGE.get(ticker, (ticker.replace("/", "-"), 1))
            else:
                ent, fac = None, 1
            emap[c] = {"issuer": needed[c], "entity": ent, "factor": fac}
            log(f"   🔎 {needed[c]} ({c}) → {ent or '未匹配'}")
        time.sleep(2.6)
    if todo:
        with open(EMAP_PATH, "w", encoding="utf-8") as fh:
            json.dump(emap, fh, ensure_ascii=False, indent=1, sort_keys=True)


def to_positions(filing, emap):
    """把一份 13F 转成 {实体: {weight, units, issuer, entity}};未映射的用 ?CUSIP 作键"""
    pos = {}
    for h in filing["holdings"]:
        m = emap.get(h["cusip"]) or {}
        ent = m.get("entity")
        k = ent or ("?" + h["cusip"])
        p = pos.setdefault(k, {"entity": ent, "issuer": h["issuer"], "weight": 0.0, "units": 0.0, "value": 0})
        p["weight"] += h["weight"]
        p["units"] += h["shares"] * m.get("factor", 1)
        p["value"] += h["value"]
    return pos


# ============ 行情 ============

def load_prices(tickers, log=print):
    """返回 (含分红复权收盘价, 拆股, 仅拆股调整的收盘价);失败返回 (None, None, None)。
    前者用于算涨跌幅,后者用于展示当时的实际价格水平"""
    try:
        import yfinance as yf
        start = (date.today() - timedelta(days=900)).isoformat()
        df = yf.download(sorted(set(tickers) | {"SPY"}), start=start, auto_adjust=False,
                         actions=True, progress=False, threads=True)
        close = df["Adj Close"].dropna(how="all").ffill()
        raw = df["Close"].dropna(how="all").ffill()
        if close.empty or "SPY" not in close.columns or close["SPY"].dropna().empty:
            raise RuntimeError("行情为空")
        return close, df["Stock Splits"].fillna(0), raw
    except Exception as e:
        log(f"   ⚠️ 行情下载失败({e}),沿用上次价格")
        return None, None, None


def split_factor(splits, ent, d0, d1):
    if splits is None or ent not in splits.columns:
        return 1.0
    s = splits[ent]
    s = s[(s.index > d0) & (s.index <= d1) & (s > 0)]
    f = 1.0
    for v in s.values:
        f *= float(v)
    return f


# ============ 信号 ============

def quarter_name(report_date):
    y, m, _ = report_date.split("-")
    return f"{y}Q{(int(m) - 1) // 3 + 1}"


def compute_signals(emap, close, splits, prev_signals=None, raw=None, prev_view=None):
    """对四位投资者计算信号。返回 (signals, investors_view)"""
    import pandas as pd
    today = date.today()
    prev_px = {(s["investor"], s["kind"], s["key"], s["quarter"]): s for s in (prev_signals or [])}
    prev_rows = {(k, h["quarter"], r["key"]): r for k, v in (prev_view or {}).items()
                 for h in v.get("history", []) for r in h["rows"]}
    signals, changes, view, history = [], {}, {}, {}

    for key, info in INVESTORS.items():
        fs = load_filings(key)
        if not fs:
            continue
        changes[key] = {}
        pos_list = [to_positions(f, emap) for f in fs]
        for i in range(1, len(fs)):
            a, b, pa, pb = fs[i - 1], fs[i], pos_list[i - 1], pos_list[i]
            d0, d1 = pd.Timestamp(a["report_date"]), pd.Timestamp(b["report_date"])
            if (d1 - d0).days > 120:
                continue
            ch = changes[key][b["report_date"]] = {}
            rows = history.setdefault(key, {})[b["report_date"]] = {
                "quarter": b["report_date"], "filing_date": b["filing_date"], "n_positions": len(pb),
                "total_value": sum(p["value"] for p in pb.values()), "unchanged": 0, "rows": []}

            def record(kind, k, p, chg, before, after):
                signal = ((kind == "new" and after >= RULES["new_min_weight"])
                          or (kind == "add" and chg >= RULES["add_min_chg"] and after >= RULES["add_min_weight"])
                          or (kind == "exit" and before >= RULES["exit_min_weight"]))
                row = {"kind": kind, "key": k, "entity": p["entity"], "issuer": p["issuer"],
                       "chg": None if chg is None else round(chg, 3),
                       "before": round(before, 2), "after": round(after, 2), "signal": signal}
                # 13F 不披露成交价:用当季每日收盘价的均值与区间估算,再对比现价
                old = prev_rows.get((key, b["report_date"], k), {})
                for f in ("q_avg", "q_low", "q_high", "last_px", "px_chg", "spy_chg"):
                    row[f] = old.get(f)
                ent = p["entity"]
                if raw is not None and ent and ent in raw.columns:
                    seg = raw[ent].loc[d0 + pd.Timedelta(days=1):d1].dropna()
                    last = raw[ent].dropna()
                    if len(seg) >= 20 and len(last):
                        avg, lp_ = float(seg.mean()), float(last.iloc[-1])
                        # 标普用同一口径:当季均价 → 现价
                        spy_seg = raw["SPY"].loc[d0 + pd.Timedelta(days=1):d1].dropna()
                        row.update({"q_avg": round(avg, 2), "q_low": round(float(seg.min()), 2),
                                    "q_high": round(float(seg.max()), 2), "last_px": round(lp_, 2),
                                    "px_chg": round(lp_ / avg - 1, 4),
                                    "spy_chg": round(float(raw["SPY"].dropna().iloc[-1] / spy_seg.mean() - 1), 4)})
                rows["rows"].append(row)

            def emit(kind, k, p, chg=None):
                signals.append({"investor": key, "kind": kind, "key": k, "entity": p["entity"],
                                "issuer": p["issuer"], "quarter": b["report_date"],
                                "filing_date": b["filing_date"], "weight": round(p["weight"], 2),
                                "chg": None if chg is None else round(chg, 3)})

            for k, p in pb.items():
                if k not in pa:
                    ch[k] = None
                    record("new", k, p, None, 0.0, p["weight"])
                    if p["weight"] >= RULES["new_min_weight"]:
                        emit("NEW", k, p)
                else:
                    pu = pa[k]["units"] * (split_factor(splits, p["entity"], d0, d1) if p["entity"] else 1.0)
                    chg = p["units"] / pu - 1 if pu else 0.0
                    ch[k] = chg
                    if abs(chg) < 0.005:
                        rows["unchanged"] += 1
                    else:
                        record("add" if chg > 0 else "trim", k, p, chg, pa[k]["weight"], p["weight"])
                    if chg >= RULES["add_min_chg"] and p["weight"] >= RULES["add_min_weight"]:
                        emit("ADD", k, p, chg)
            for k, p in pa.items():
                if k not in pb:
                    ch[k] = -1.0
                    record("exit", k, p, -1.0, p["weight"], 0.0)
                    if p["weight"] >= RULES["exit_min_weight"]:
                        emit("EXIT", k, p)

        # 投资者视图:最新持仓 + 最近一季变动
        last, lp = fs[-1], pos_list[-1]
        lch = changes[key].get(last["report_date"], {})
        top = sorted(lp.items(), key=lambda kv: -kv[1]["weight"])
        view[key] = {
            "quarter": last["report_date"], "filing_date": last["filing_date"],
            "n_positions": len(lp), "total_value": sum(p["value"] for p in lp.values()),
            "top5_weight": round(sum(p["weight"] for _, p in top[:5]), 1),
            "holdings": [{"key": k, "entity": p["entity"], "issuer": p["issuer"],
                          "weight": round(p["weight"], 2),
                          "chg": ("new" if lch.get(k, 0) is None else round(lch.get(k, 0), 3))}
                         for k, p in top[:15]],
            "exits": [{"key": k, "issuer": pos_list[-2][k]["issuer"], "entity": pos_list[-2][k]["entity"],
                       "weight": round(pos_list[-2][k]["weight"], 2)}
                      for k, c in lch.items() if c == -1.0 and len(pos_list) > 1],
            # 最近 5 期 13F 的逐只变动
            "history": [history[key][q] for q in sorted(history.get(key, {}), reverse=True)[:5]],
        }

    # 状态、分歧、价格
    cut = -RULES["invalidate_cut"]
    for s in signals:
        s["quarter_name"] = quarter_name(s["quarter"])
        s["status"], s["status_note"], s["divergence"] = "active", None, []
        if s["kind"] in ("NEW", "ADD"):
            for q, ch in sorted(changes[s["investor"]].items()):
                c = ch.get(s["key"])
                if q > s["quarter"] and c is not None and c <= cut:
                    s["status"] = "invalid"
                    s["status_note"] = f"{quarter_name(q)} " + ("清仓" if c == -1.0 else f"减仓 {abs(c) * 100:.0f}%")
                    break
            for other, qs in changes.items():
                if other == s["investor"]:
                    continue
                for q, ch in sorted(qs.items()):
                    c = ch.get(s["key"])
                    if q >= s["quarter"] and c is not None and c <= cut:
                        s["divergence"].append({"investor": other, "quarter": quarter_name(q),
                                                "action": "清仓" if c == -1.0 else f"减仓 {abs(c) * 100:.0f}%"})
        else:   # EXIT: 之后又买回则不再提示
            for q, ch in changes[s["investor"]].items():
                if q > s["quarter"] and s["key"] in ch and ch[s["key"]] is None:
                    s["status"] = "invalid"
                    s["status_note"] = f"{quarter_name(q)} 重新买入"

        # 建仓参考价:披露日之后第一个交易日收盘
        old = prev_px.get((s["investor"], s["kind"], s["key"], s["quarter"]), {})
        for f in ("entry_date", "entry_px", "last_px", "ret", "spy_ret", "px_date"):
            s[f] = old.get(f)
        ent = s["entity"]
        if close is not None and ent and ent in close.columns:
            i = close.index.searchsorted(pd.Timestamp(s["filing_date"]), side="right")
            if i < len(close.index):
                t0, t1 = close.index[i], close.index[-1]
                p0, p1 = close[ent].get(t0), close[ent].get(t1)
                if p0 == p0 and p1 == p1 and p0:
                    show = raw if raw is not None and ent in raw.columns else close
                    s.update({"entry_date": str(t0.date()), "entry_px": round(float(show[ent].get(t0, p0)), 2),
                              "last_px": round(float(show[ent].get(t1, p1)), 2), "px_date": str(t1.date()),
                              "ret": round(float(p1 / p0 - 1), 4),
                              "spy_ret": round(float(close["SPY"][t1] / close["SPY"][t0] - 1), 4)})
        ref = datetime.strptime(s["entry_date"] or s["filing_date"], "%Y-%m-%d").date()
        s["age_days"] = (today - ref).days
        if s["status"] == "active" and s["age_days"] > RULES["active_days"]:
            s["status"] = "expired"

    signals.sort(key=lambda s: (s["quarter"], s["investor"]), reverse=True)
    return signals, view


def build_memo(signals, view):
    """把仍在 12 个月窗口内的信号按标的归组成备忘录"""
    latest_q = max((v["quarter"] for v in view.values()), default=None)
    recent = [s for s in signals if s["age_days"] <= RULES["active_days"]]
    groups = {}
    for s in recent:
        g = groups.setdefault(s["key"], {"key": s["key"], "entity": s["entity"], "issuer": s["issuer"],
                                         "buys": [], "exits": []})
        (g["exits"] if s["kind"] == "EXIT" else g["buys"]).append(s)
    memo = {"latest_quarter": latest_q, "priority": [], "divergent": [], "invalid": [], "review": [], "new": []}
    for g in groups.values():
        active = [s for s in g["buys"] if s["status"] == "active"]
        g["investors"] = sorted({s["investor"] for s in active})
        g["divergence"] = [d for s in active for d in s["divergence"]]
        g["divergence"] = [dict(t) for t in {tuple(sorted(d.items())) for d in g["divergence"]}]
        g["latest"] = max(s["quarter"] for s in g["buys"] + g["exits"])
        if active:
            (memo["divergent"] if g["divergence"] else memo["priority"]).append(g)
        elif g["buys"]:
            memo["invalid"].append(g)
        if [s for s in g["exits"] if s["status"] == "active"] and not active:
            memo["review"].append(g)
    order = lambda g: (len(g["investors"]), g["latest"], max((s["weight"] for s in g["buys"] + g["exits"]), default=0))
    for k in ("priority", "divergent", "invalid", "review"):
        memo[k].sort(key=order, reverse=True)
    memo["new"] = [s for s in recent if s["quarter"] == latest_q]
    return memo


# ============ 快速披露 (Form 4 / 13D / 13G) ============

def _txt(xml, tag):
    m = re.search(rf"<{tag}>\s*(?:<value>)?\s*([^<]+?)\s*<", xml, re.I)
    return m.group(1).strip() if m else None


def parse_fast_filing(cik, form, accession, primary_doc):
    """解析一份快速披露。返回 None 表示与跟踪无关(例如伯克希尔董事买卖伯克希尔自身股票)"""
    doc = primary_doc.split("/")[-1] if primary_doc else ""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    url = f"{base}/{primary_doc}" if primary_doc else base
    out = {"form": form, "accession": accession, "url": url, "issuer": None, "ticker": None, "summary": ""}
    raw = sec_get(f"{base}/{doc}") if doc.endswith(".xml") else None
    if not raw:
        return out
    xml = _strip_ns(raw.decode("utf-8", errors="ignore"))
    issuer_cik = _txt(xml, "issuerCik")
    if issuer_cik and int(issuer_cik) == int(cik):
        return None
    out["issuer"] = _txt(xml, "issuerName")
    if form.startswith("4"):
        owner = _txt(xml, "rptOwnerCik")
        if owner and int(owner) != int(cik):
            return None
        out["ticker"] = _txt(xml, "issuerTradingSymbol")
        buy = sell = 0.0
        prices = []
        for t in re.findall(r"<nonDerivativeTransaction>.*?</nonDerivativeTransaction>", xml, re.S):
            code, sh, px = _txt(t, "transactionCode"), _txt(t, "transactionShares"), _txt(t, "transactionPricePerShare")
            try:
                sh = float(sh)
            except (TypeError, ValueError):
                continue
            if code == "P":
                buy += sh
            elif code == "S":
                sell += sh
            else:
                continue
            try:
                prices.append(float(px))
            except (TypeError, ValueError):
                pass
        parts = []
        if buy:
            parts.append(f"买入 {buy:,.0f} 股")
        if sell:
            parts.append(f"卖出 {sell:,.0f} 股")
        if prices:
            parts.append(f"价格 ${min(prices):,.2f}–${max(prices):,.2f}" if min(prices) != max(prices) else f"价格 ${prices[0]:,.2f}")
        out["summary"] = ",".join(parts) or "非公开市场买卖(赠与、行权等)"
        out["direction"] = "buy" if buy > sell else "sell" if sell > buy else None
    else:
        pct = _txt(xml, "percentOfClass") or _txt(xml, "classPercent")
        amt = _txt(xml, "aggregateAmountOwned") or _txt(xml, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares")
        parts = []
        try:
            parts.append(f"持有 {float(amt):,.0f} 股")
        except (TypeError, ValueError):
            pass
        if pct:
            parts.append(f"占该类股份 {pct}%")
        out["summary"] = ",".join(parts) or "持股比例变动披露"
    return out


def check_fast_filings(state, log=print):
    """返回本次新发现的快速披露列表,并维护 data/fast_filings.json(保留 180 天)"""
    seen = state.setdefault("seen_fast", {})
    first_run = not seen
    log_items = json.load(open(FAST_PATH, encoding="utf-8")) if os.path.exists(FAST_PATH) else []
    cutoff = (date.today() - timedelta(days=180)).isoformat()
    new_items = []
    for key, info in INVESTORS.items():
        sub = get_submissions(info["cik"])
        if not sub:
            continue
        r = sub["filings"]["recent"]
        known = set(seen.setdefault(key, []))
        for i, form in enumerate(r["form"]):
            acc, fd = r["accessionNumber"][i], r["filingDate"][i]
            if form not in FAST_FORMS or fd < cutoff or acc in known:
                continue
            item = parse_fast_filing(info["cik"], form, acc, r["primaryDocument"][i])
            seen[key].append(acc)
            time.sleep(0.15)
            if item is None:
                continue
            item.update({"investor": key, "filing_date": fd})
            log_items.append(item)
            if not first_run:
                new_items.append(item)
                log(f"   ⚡ {info['cn']} {form} {item.get('issuer')}: {item['summary']}")
        time.sleep(0.2)
    log_items = sorted((x for x in log_items if x["filing_date"] >= cutoff),
                       key=lambda x: (x["filing_date"], x["accession"]), reverse=True)
    with open(FAST_PATH, "w", encoding="utf-8") as fh:
        json.dump(log_items, fh, ensure_ascii=False, indent=1)
    return new_items, log_items


# ============ 主流程 ============

def beijing_now():
    return datetime.now(timezone(timedelta(hours=8)))


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def send_email(subject, html):
    from monitor import send_email as _send      # 复用 ETF 监控的发信配置
    return _send(subject, html)


def main():
    import follow_site
    print(f"\n{'=' * 56}\n大师跟随系统 {beijing_now():%Y-%m-%d %H:%M} 北京时间\n{'=' * 56}")
    state = load_state()

    print("\n1) 检查 13F")
    new_13f = {}
    for key, info in INVESTORS.items():
        added = sync_13f(key, info["cik"])
        known = state.setdefault("seen_13f", {}).setdefault(key, [])
        fresh = [rd for rd in added if rd not in known]
        if fresh:
            new_13f[key] = max(fresh)
        state["seen_13f"][key] = sorted(set(known) | {f["report_date"] for f in load_filings(key)})

    print("\n2) 检查快速披露")
    new_fast, fast_log = check_fast_filings(state)

    print("\n3) 代码映射与行情")
    emap = load_entity_map()
    needed = {}
    for key in INVESTORS:
        for f in load_filings(key)[-7:]:      # 覆盖"历次变动"页用到的全部持仓
            for h in f["holdings"]:
                needed.setdefault(h["cusip"], h["issuer"])
    resolve_cusips(emap, needed)
    tickers = {emap[c]["entity"] for c in needed if emap.get(c, {}).get("entity")}
    close, splits, raw = load_prices(tickers)

    print("\n4) 计算信号")
    old = json.load(open(SIGNALS_PATH, encoding="utf-8")) if os.path.exists(SIGNALS_PATH) else {}
    signals, view = compute_signals(emap, close, splits, old.get("signals"), raw, old.get("investors"))
    signals = [s for s in signals if s["age_days"] <= 2 * RULES["active_days"]]
    memo = build_memo(signals, view)
    px_date = str(close.index[-1].date()) if close is not None else next((s["px_date"] for s in signals if s.get("px_date")), None)
    payload = {"generated": beijing_now().strftime("%Y-%m-%d %H:%M"), "px_date": px_date,
               "prices_stale": close is None, "rules": RULES, "signals": signals, "investors": view}
    with open(SIGNALS_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"   优先研究 {len(memo['priority'])} · 有分歧 {len(memo['divergent'])} · "
          f"已失效 {len(memo['invalid'])} · 持仓复查 {len(memo['review'])}")

    print("\n5) 重建看板")
    follow_site.build(payload, memo, fast_log)

    print("\n6) 邮件")
    today = date.today()
    sent = False
    new_13f = {**state.get("pending_memo", {}), **new_13f}      # 上次发信失败的,这次补发
    if new_13f:
        who = "、".join(INVESTORS[k]["cn"] for k in new_13f)
        q = quarter_name(max(new_13f.values()))
        sent = send_email(f"📋 行动备忘录 · {q} · {who} 披露新 13F",
                          follow_site.email_html(payload, memo, fast_log, new_13f=new_13f, new_fast=new_fast))
        state["pending_memo"] = {} if sent else new_13f
    elif new_fast:
        names = "、".join(sorted({x.get("issuer") or "未知标的" for x in new_fast}))
        sent = send_email(f"⚡ 快速披露 · {names}",
                          follow_site.email_html(payload, memo, fast_log, new_fast=new_fast))
    elif today.weekday() == 0 and state.get("last_weekly") != today.isoformat():
        sent = send_email(f"📊 跟随周报 · {today:%Y-%m-%d}", follow_site.email_html(payload, memo, fast_log))
        if sent:
            state["last_weekly"] = today.isoformat()
    else:
        print("   今天没有需要发送的邮件")
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    print("\n完成\n")


if __name__ == "__main__":
    main()
