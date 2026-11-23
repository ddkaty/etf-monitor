"""看板与邮件的页面生成。由 follow.py 调用。

docs/index.html      行动备忘录(信号按标的归组)+ 快速披露
docs/investors.html  四位投资者的最新持仓与本季变动
docs/evidence.html   规则与回测依据
"""

import os
import json
import hashlib
from html import escape

from follow import HERE, INVESTORS, RULES, CN_NAMES, quarter_name

DOCS = os.path.join(HERE, "docs")
SITE_URL = "https://ddkaty.github.io/etf-monitor/"
KIND = {"NEW": "新建仓", "ADD": "加仓", "EXIT": "清仓"}
OBSOLETE = ["consensus.html", "history.html", "Buffett.html", "Duan.html", "LiLu.html",
            "Marks.html", "Pabrai.html", os.path.join("data", "snapshot.json")]


def name_of(entity, issuer):
    if entity and entity in CN_NAMES:
        return f"{CN_NAMES[entity]} {entity}"
    title = " ".join(w.capitalize() if len(w) > 3 else w for w in (issuer or "").split())
    return f"{title} {entity}" if entity else title


def pct(v, signed=True, digits=1):
    if v is None:
        return "—"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def money(v):
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"


def action_text(s):
    if s["kind"] == "NEW":
        return f"新建仓,权重 {s['weight']:.1f}%"
    if s["kind"] == "ADD":
        return f"加仓 {s['chg'] * 100:.0f}%,权重 {s['weight']:.1f}%"
    return f"清仓(原权重 {s['weight']:.1f}%)"


def perf_text(s):
    if s.get("ret") is None:
        return "披露次日收盘价待定" if s["age_days"] <= 3 else "无行情数据"
    return f"披露后 {pct(s['ret'])} · 同期标普 {pct(s['spy_ret'])}"


# ============ 网页 ============

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#14181f;--muted:#667085;--line:#e4e7ec;--accent:#0e7490;
--good:#0f7b4f;--bad:#b42318;--warn:#b54708;--soft:#eef2f6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c23;--text:#e8ecf1;--muted:#98a2b3;
--line:#2a313b;--accent:#4fc3d9;--good:#4ade80;--bad:#f97066;--warn:#fdb022;--soft:#1e252e}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--text);line-height:1.55;font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:860px;margin:0 auto;padding:0 16px 48px}
header{padding:20px 0 12px}
.brand{font-size:12px;letter-spacing:.12em;color:var(--accent);font-weight:700}
h1{font-size:24px;margin-top:4px}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
nav{display:flex;gap:6px;margin:14px 0 4px;flex-wrap:wrap}
nav a{padding:6px 14px;border-radius:999px;background:var(--soft);color:var(--text);font-size:14px;font-weight:600}
nav a.on{background:var(--accent);color:#fff}
@media (prefers-color-scheme:dark){nav a.on{color:#0f1318}}
h2{font-size:17px;margin:28px 0 4px}
.hint{color:var(--muted);font-size:13px;margin-bottom:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.card h3{font-size:16px;display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.tag{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;color:#fff;white-space:nowrap}
.row{padding:8px 0;border-top:1px solid var(--line);margin-top:8px;font-size:14px}
.row .who{font-weight:600}
.row .perf{color:var(--muted);font-size:13px}
.row .perf.up{color:var(--good)}.row .perf.down{color:var(--bad)}
.up{color:var(--good)}.down{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.note{font-size:13px;margin-top:8px;padding:8px 10px;border-radius:8px;background:var(--soft)}
.empty{color:var(--muted);font-size:14px;padding:6px 0}
.banner{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:10px;padding:12px 14px;margin-top:14px;font-size:14px}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:8px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.fast .row:first-child{border-top:none;margin-top:0;padding-top:0}
.wide table{min-width:520px}.wide td:first-child{min-width:9.5em}
.def{font-size:14px;color:var(--muted);margin-top:2px}
ul.plain{margin:6px 0 0 18px;font-size:14px}ul.plain li{margin:4px 0}
footer{margin-top:32px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
"""

GLOSSARY = [
    ("13F", "管理规模超过 1 亿美元的机构每季度必须向美国证监会提交的持仓报告。只包含美股多头持仓,最晚在季度结束 45 天后公布,所以看到时已经是一个半月前的状态。"),
    ("Form 4 / 13D / 13G", "比 13F 快的披露。持股超过 5% 时要报 13D 或 13G;持股超过 10% 后每次买卖要在两个工作日内报 Form 4。"),
    ("权重", "这只股票的市值占该投资者 13F 全部持仓市值的比例。权重越高,说明他押得越重。"),
    ("新建仓", "上一季度没有、这一季度出现的持仓。"),
    ("加仓 X%", "持股数量比上一季度增加的比例。例如上季 659 万股、本季 1482 万股,1482 ÷ 659 − 1 = 125%,即加仓 125%,股数变成原来的 2.25 倍。算的是股数而不是市值,所以不受股价涨跌影响;期间如有拆股,会先换算再比较。百分比大不等于仓位重,要和权重一起看。"),
    ("清仓", "上一季度持有、这一季度完全消失的持仓。"),
    ("参考价", "13F 披露日之后第一个交易日的收盘价。用它而不是大师的实际买入价,是因为普通人只能在披露之后才知道这笔交易。"),
    ("披露后涨跌", "从参考价到最新收盘价的涨跌幅,已计入分红。"),
    ("同期标普", "同一段时间里标普 500 指数基金(SPY)的涨跌幅,用来判断这只股票是跑赢还是跑输大盘。"),
    ("超额", "股票涨跌幅减去同期标普涨跌幅。股票涨 10%、标普涨 4%,超额就是 +6%。"),
    ("有效 / 失效 / 分歧", "信号发出后 12 个月内为有效。发出信号的投资者随后自己减仓 20% 以上或清仓,信号失效。另一位跟踪对象在同期或之后减仓 20% 以上,则标为有分歧。"),
    ("信号回测", "用历史数据检验规则:找出过去每一次符合条件的情况,假设在披露后第一个交易日买入,看之后 12 个月相对标普 500 的表现,再把所有情况汇总。它只说明过去怎样,不保证将来。"),
    ("样本数", "回测区间内这种情况一共出现了多少次。次数越少,结论越不可靠。"),
    ("平均超额", "所有样本的超额收益取平均。容易被个别暴涨暴跌拉高或拉低。"),
    ("中位数", "把所有样本从差到好排队,排在正中间那一个的结果。比平均数更能代表通常的情况。"),
    ("跑赢比例", "样本中超额收益为正的占比。58% 表示每 10 次约有 6 次跑赢、4 次跑输。"),
    ("最大回撤", "回测期间从最高点到之后最低点的最大跌幅,衡量最坏的时候会亏多少。"),
]

CSS_VER = hashlib.md5(CSS.encode()).hexdigest()[:8]      # 样式变了就换地址,避免浏览器用旧缓存


def page(title, active, body, payload):
    nav = "".join(f'<a href="{href}"{" class=on" if key == active else ""}>{label}</a>'
                  for key, label, href in (("memo", "行动备忘录", "index.html"),
                                           ("investors", "四位持仓", "investors.html"),
                                           ("evidence", "规则与依据", "evidence.html")))
    stale = "(行情更新失败,价格为上次数据)" if payload.get("prices_stale") else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 大师跟随</title><link rel="stylesheet" href="assets/style.css?v={CSS_VER}"></head>
<body><div class="wrap">
<header><div class="brand">大师跟随</div><h1>{title}</h1>
<div class="sub">价格截至 {payload.get('px_date') or '—'}{stale} · 页面生成于 {payload['generated'].replace('T', ' ')} 北京时间</div>
<nav>{nav}</nav></header>
{body}
<footer>数据来自 SEC EDGAR 与公开行情。13F 只披露美股多头持仓,且最晚在季度结束 45 天后公布。
本页是按固定规则生成的研究清单,不构成投资建议。</footer>
</div></body></html>"""


def badge(key):
    i = INVESTORS[key]
    return f'<span class="tag" style="background:{i["color"]}">{escape(i["cn"])}</span>'


def signal_row(s):
    cls = "" if s.get("ret") is None else (" up" if s["ret"] - (s["spy_ret"] or 0) >= 0 else " down")
    extra = ""
    if s["status"] == "invalid":
        extra = f' <span class="warn">· 已失效({escape(s["status_note"] or "")})</span>'
    elif s["status"] == "expired":
        extra = ' <span class="muted">· 已过 12 个月</span>'
    return (f'<div class="row"><span class="who">{escape(INVESTORS[s["investor"]]["cn"])}</span> '
            f'{s["quarter_name"]} {action_text(s)}{extra}'
            f'<div class="perf{cls}">{perf_text(s)}'
            f'{" · 参考价 $" + format(s["entry_px"], ",.2f") + " → $" + format(s["last_px"], ",.2f") if s.get("entry_px") else ""}'
            f'</div></div>')


def group_card(g, show="buys"):
    rows = sorted(g[show], key=lambda s: (s["quarter"], s["investor"]), reverse=True)
    who = "".join(badge(k) for k in (g["investors"] if show == "buys" and g["investors"]
                                     else sorted({s["investor"] for s in rows})))
    note = ""
    if show == "buys" and g.get("divergence"):
        txt = ";".join(f'{INVESTORS[d["investor"]]["cn"]} {d["quarter"]} {d["action"]}'
                       for d in sorted(g["divergence"], key=lambda d: d["quarter"]))
        note = f'<div class="note"><span class="warn">反向操作:</span>{escape(txt)}</div>'
    return (f'<div class="card"><h3>{escape(name_of(g["entity"], g["issuer"]))} {who}</h3>'
            f'{"".join(signal_row(s) for s in rows)}{note}</div>')


def section(title, hint, groups, show="buys", empty="暂无"):
    cards = "".join(group_card(g, show) for g in groups) or f'<div class="empty">{empty}</div>'
    return f'<h2>{title}</h2><div class="hint">{hint}</div>{cards}'


def fast_section(fast_log, limit=20):
    rows = ""
    for x in fast_log[:limit]:
        d = x.get("direction")
        cls = " up" if d == "buy" else " down" if d == "sell" else ""
        label = escape((x.get("issuer") or "未知标的") + (f' {x["ticker"]}' if x.get("ticker") else ""))
        form = escape(x["form"].replace("SCHEDULE ", "").replace("SC ", ""))
        rows += (f'<div class="row"><span class="who">{label}</span>'
                 f'<div class="perf">{x["filing_date"]} · {escape(INVESTORS[x["investor"]]["cn"])} · '
                 f'<a href="{escape(x["url"])}">{"Form " if form[0] == "4" else ""}{form}</a></div>'
                 f'<div class="perf{cls}">{escape(x.get("summary") or "")}</div></div>')
    if not rows:
        return '<div class="empty">近 180 天没有快速披露</div>'
    return f'<div class="card fast">{rows}</div>'


def render_memo(payload, memo, fast_log):
    q = quarter_name(memo["latest_quarter"]) if memo["latest_quarter"] else "—"
    if memo["new"]:
        items = "".join(f'<li><b>{escape(INVESTORS[s["investor"]]["cn"])}</b> '
                        f'{escape(name_of(s["entity"], s["issuer"]))}:{action_text(s)}</li>' for s in memo["new"])
        new = f'<div class="banner"><b>{q} 新信号 {len(memo["new"])} 条</b><ul class="plain">{items}</ul></div>'
    else:
        new = f'<div class="banner"><b>{q} 没有触发任何信号。</b>四位投资者本季没有大仓位新建、大幅加仓或清仓。</div>'
    body = new + '<div class="hint" style="margin-top:10px">加仓幅度、权重、参考价等用词的含义见 <a href="evidence.html#glossary">名词解释</a>。</div>'
    body += section("优先研究", "有效的买入类信号,且没有其他跟踪对象反向操作。按跟进人数和时间排序。",
                    memo["priority"], empty="目前没有")
    body += section("有分歧", "有人买入,同时另一位跟踪对象在减仓或清仓。需要自己弄清原因。",
                    memo["divergent"], empty="目前没有")
    body += section("持仓复查", "被清仓的原重仓股。回测中这类股票之后一年约三分之二跑输大盘,持有者值得重新审视。",
                    memo["review"], show="exits", empty="近 12 个月没有清仓信号")
    body += section("已失效", "发出信号的投资者随后自己减仓 20% 以上或清仓,信号作废。", memo["invalid"], empty="目前没有")
    body += '<h2>快速披露</h2><div class="hint">Form 4 与 13D/13G 在交易后几天内公布,比 13F 快。每天检查一次。</div>'
    body += fast_section(fast_log)
    return page("行动备忘录", "memo", body, payload)


def render_investors(payload):
    body = ""
    for key, v in payload["investors"].items():
        i = INVESTORS[key]
        rows = ""
        for h in v["holdings"]:
            c = h["chg"]
            if c == "new":
                chg = '<span class="up">新建仓</span>'
            elif abs(c) < 0.005:
                chg = '<span class="muted">未变</span>'
            else:
                chg = f'<span class="{"up" if c > 0 else "down"}">{"加仓" if c > 0 else "减仓"} {abs(c) * 100:.0f}%</span>'
            rows += f'<tr><td>{escape(name_of(h["entity"], h["issuer"]))}</td><td class="num">{h["weight"]:.1f}%</td><td class="num">{chg}</td></tr>'
        for x in v["exits"]:
            rows += (f'<tr><td>{escape(name_of(x["entity"], x["issuer"]))}</td><td class="num muted">原 {x["weight"]:.1f}%</td>'
                     f'<td class="num"><span class="down">清仓</span></td></tr>')
        body += (f'<h2>{escape(i["cn"])} <span class="muted" style="font-weight:400;font-size:14px">{escape(i["fund"])}</span></h2>'
                 f'<div class="hint">{quarter_name(v["quarter"])} 季报,{v["filing_date"]} 披露 · {v["n_positions"]} 只持仓 · '
                 f'合计 {money(v["total_value"])} · 前五大占 {v["top5_weight"]:.0f}%</div>'
                 f'<div class="card scroll"><table><tr><th>持仓(前 15)</th><th class="num">权重</th><th class="num">本季股数变动</th></tr>{rows}</table></div>')
    return page("四位持仓", "investors", body, payload)


def render_evidence(payload):
    path = os.path.join(HERE, "backtest", "results.json")
    r = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    body = f"""<h2>跟随规则</h2>
<div class="card"><ul class="plain">
<li><b>跟随:</b>新建仓且权重 ≥{RULES['new_min_weight']:.0f}%;单季加仓 ≥{RULES['add_min_chg'] * 100:.0f}% 且权重 ≥{RULES['add_min_weight']:.0f}%。</li>
<li><b>复查:</b>清仓原权重 ≥{RULES['exit_min_weight']:.0f}% 的仓位。</li>
<li><b>忽略:</b>小仓位新建、小幅加仓、减仓。回测中它们没有信息量。</li>
<li><b>失效:</b>同一位投资者随后减仓 ≥{RULES['invalidate_cut'] * 100:.0f}% 或清仓。</li>
<li><b>有效期:</b>披露后 12 个月。参考价是披露日之后第一个交易日的收盘价。</li>
<li><b>不等回调:</b>回测中,披露后已跌破大师当季均价的标的后续表现最差,所以不设"等跌到成本价再买"的条件。</li>
</ul></div>"""
    if r:
        rows = ""
        for k, s in r["signals"].items():
            x = s.get("12m")
            if x:
                rows += (f'<tr><td>{escape(s["label"])}</td><td class="num">{x["n"]}</td><td class="num">{pct(x["mean"])}</td>'
                         f'<td class="num">{pct(x["median"])}</td><td class="num">{x["hit"] * 100:.0f}%</td></tr>')
        body += ('<h2>信号回测</h2><div class="hint">披露后 12 个月相对标普 500 的表现。样本为七位投资者 2016 年底至 '
                 f'{r["data_end"]} 的全部 13F。</div><div class="card scroll wide"><table><tr><th>动作</th><th class="num">样本数</th>'
                 f'<th class="num">平均超额</th><th class="num">中位数</th><th class="num">跑赢比例</th></tr>{rows}</table></div>')
        rows = ""
        for k, name in r["names"].items():
            c = r["clone"][k]["top5_eq"]
            rows += (f'<tr><td>{escape(name)}</td><td class="num">{pct(c["cagr"], False)}</td><td class="num">{pct(c["spy_cagr"], False)}</td>'
                     f'<td class="num">{pct(c["excess"])}</td><td class="num">{pct(c["mdd"], digits=0)}</td></tr>')
        body += ('<h2>照抄前五大持仓</h2><div class="hint">每次披露后等权买入前五大持仓并持有到下次披露。结论:照抄持仓没有稳定优势,'
                 '所以本系统只跟动作,不跟持仓。</div><div class="card scroll wide"><table><tr><th>投资者</th><th class="num">年化</th>'
                 f'<th class="num">同期标普</th><th class="num">超额</th><th class="num">最大回撤</th></tr>{rows}</table></div>')
        rows = "".join(f'<tr><td>{escape(b)}</td><td class="num">{s["12m"]["n"]}</td><td class="num">{pct(s["12m"]["mean"])}</td>'
                       f'<td class="num">{s["12m"]["hit"] * 100:.0f}%</td></tr>' for b, s in r["price_cond"].items() if s.get("12m"))
        body += ('<h2>价格条件</h2><div class="hint">买入类信号按"披露时现价相对大师当季均价"分组后的 12 个月表现。</div>'
                 f'<div class="card scroll wide"><table><tr><th>现价相对当季均价</th><th class="num">样本数</th><th class="num">平均超额</th>'
                 f'<th class="num">跑赢比例</th></tr>{rows}</table></div>')
    body += """<h2>这些结论的局限</h2>
<div class="card"><ul class="plain">
<li>样本小。大仓位新建仓只有 33 个完整样本,只能算有迹象,不算确证。</li>
<li>回测的十年是美股大牛市,换一种市场环境结论可能不同。</li>
<li>跟踪对象是因为已经成功才被选中的,结果偏乐观。</li>
<li>已退市的股票缺少行情,被剔除在外。</li>
<li>信号的优势体现在 12 个月的平均值上,单条信号的输赢接近各半。</li>
</ul></div>"""
    items = "".join(f'<div class="row"><span class="who">{escape(t)}</span><div class="def">{escape(d)}</div></div>'
                    for t, d in GLOSSARY)
    body += f'<h2 id="glossary">名词解释</h2><div class="card fast">{items}</div>'
    return page("规则与依据", "evidence", body, payload)


def build(payload, memo, fast_log):
    os.makedirs(os.path.join(DOCS, "assets"), exist_ok=True)
    for name in OBSOLETE:      # 旧版看板的页面
        p = os.path.join(DOCS, name)
        if os.path.exists(p):
            os.remove(p)
    out = {os.path.join("assets", "style.css"): CSS, "index.html": render_memo(payload, memo, fast_log),
           "investors.html": render_investors(payload), "evidence.html": render_evidence(payload)}
    for name, content in out.items():
        with open(os.path.join(DOCS, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"   ✅ docs/{name}")


# ============ 邮件 ============

def email_html(payload, memo, fast_log, new_13f=None, new_fast=None):
    P = 'style="margin:0 0 6px;font-size:14px;line-height:1.6;color:#14181f"'
    H = 'style="font-size:16px;margin:22px 0 8px;color:#0e7490"'

    def lines(groups, show="buys"):
        out = ""
        for g in groups:
            out += f'<p {P}><b>{escape(name_of(g["entity"], g["issuer"]))}</b></p>'
            for s in sorted(g[show], key=lambda s: s["quarter"], reverse=True):
                if show == "buys" and s["status"] != "active":
                    continue
                out += (f'<p style="margin:0 0 4px 14px;font-size:13px;line-height:1.6;color:#475467">'
                        f'{escape(INVESTORS[s["investor"]]["cn"])} {s["quarter_name"]} {action_text(s)} · {perf_text(s)}</p>')
            if show == "buys" and g.get("divergence"):
                txt = ";".join(f'{INVESTORS[d["investor"]]["cn"]} {d["quarter"]} {d["action"]}' for d in g["divergence"])
                out += f'<p style="margin:0 0 8px 14px;font-size:13px;color:#b54708">反向操作:{escape(txt)}</p>'
        return out or f'<p {P}>目前没有。</p>'

    top = ""
    if new_fast:
        top += f'<h2 {H}>新的快速披露</h2>'
        for x in new_fast:
            top += (f'<p {P}><b>{escape(INVESTORS[x["investor"]]["cn"])}</b> · {escape(x["form"])} · '
                    f'{escape(x.get("issuer") or "未知标的")}<br>{escape(x.get("summary") or "")} '
                    f'<a href="{escape(x["url"])}">原文</a></p>')
    if new_13f:
        who = "、".join(INVESTORS[k]["cn"] for k in new_13f)
        top += f'<h2 {H}>{escape(who)} 披露了新的 13F</h2>'
    q = quarter_name(memo["latest_quarter"]) if memo["latest_quarter"] else ""
    if memo["new"]:
        top += f'<p {P}><b>{q} 触发 {len(memo["new"])} 条信号:</b></p>'
        for s in memo["new"]:
            top += (f'<p style="margin:0 0 4px 14px;font-size:14px;color:#14181f">{escape(INVESTORS[s["investor"]]["cn"])} · '
                    f'{escape(name_of(s["entity"], s["issuer"]))} · {action_text(s)}</p>')
    else:
        top += f'<p {P}>{q} 目前没有触发信号。</p>'
    return f"""<html><body style="margin:0;padding:20px 14px;background:#f6f7f9;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif">
<div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:20px 22px">
<div style="font-size:12px;letter-spacing:.12em;color:#0e7490;font-weight:700">大师跟随</div>
<div style="font-size:12px;color:#667085;margin-bottom:6px">价格截至 {payload.get('px_date') or '—'}</div>
{top}
<h2 {H}>优先研究</h2>{lines(memo['priority'])}
<h2 {H}>有分歧</h2>{lines(memo['divergent'])}
<h2 {H}>持仓复查</h2>{lines(memo['review'], 'exits')}
<p style="margin:22px 0 0;font-size:13px"><a href="{SITE_URL}" style="color:#0e7490">打开看板查看全部</a></p>
<p style="margin:10px 0 0;font-size:11px;color:#98a2b3">按固定规则生成的研究清单,不构成投资建议。13F 最晚在季度结束 45 天后披露。</p>
</div></body></html>"""
