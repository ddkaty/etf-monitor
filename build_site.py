"""
静态站点生成器

读取 history/ 目录下的所有 13F 季度归档,生成 docs/ 下的静态站点:
    - index.html              总览看板
    - consensus.html          跨大师共识标的全表
    - history.html            跨季度时间线 + 共识演化
    - {Investor}.html         每位大师的详情页 (Top 持仓、季度对比、AUM 时间线)
    - data/snapshot.json      完整数据快照 (供未来交互式扩展)
    - assets/style.css        共用样式

生成的站点可通过 GitHub Pages 直接发布. 部署方式: 在仓库 Settings → Pages
中选择 "Deploy from a branch": main 分支 / /docs 目录.

用法:
    python build_site.py
"""

import os
import json
import math
from datetime import datetime, date
from collections import defaultdict

from superinvestors_monitor import (
    CONFIG,
    compare_holdings,
    load_history_for_investor,
    quarter_label,
)

OUTPUT_DIR = "docs"
INVESTORS = CONFIG["investors"]

COLORS = {
    "primary": "#0891b2",
    "primary_dark": "#0e7490",
    "accent": "#10b981",
    "danger": "#dc2626",
    "warning": "#f59e0b",
    "purple": "#8b5cf6",
    "pink": "#ec4899",
    "neutral": "#64748b",
    "bg": "#f1f5f9",
    "bg_card": "#f8fafc",
    "bg_hover": "#eef2f7",
    "border": "#e2e8f0",
    "text": "#0f172a",
    "text_muted": "#64748b",
}


# ============ 数据加载 ============

def load_all_history():
    """加载所有大师的所有季度归档,返回 {key: [quarter_data, ...]} (按 report_date 升序)"""
    return {key: load_history_for_investor(key) for key in INVESTORS}


def latest_quarter_across_investors(all_history):
    """跨大师对齐的'最新季度':取所有大师都已发布的最新 quarter"""
    latest_per_investor = []
    for key, quarters in all_history.items():
        if quarters:
            latest_per_investor.append(quarters[-1]["quarter"])
    if not latest_per_investor:
        return None
    return max(latest_per_investor)  # 字符串比较 "2025Q4" > "2025Q3"


def quarter_to_sort_key(q):
    """'2025Q4' -> (2025, 4) 用于排序"""
    try:
        y, qn = q.split("Q")
        return (int(y), int(qn))
    except Exception:
        return (0, 0)


def all_quarters_seen(all_history):
    """跨大师所有出现过的季度,排序"""
    qs = set()
    for quarters in all_history.values():
        for q in quarters:
            qs.add(q["quarter"])
    return sorted(qs, key=quarter_to_sort_key)


def get_quarter(quarters_list, quarter_label_str):
    """从某大师的季度列表中取指定季度"""
    for q in quarters_list:
        if q["quarter"] == quarter_label_str:
            return q
    return None


# ============ 分析计算 ============

def compute_concentration(holdings):
    """计算集中度: Top5/Top10 占比 + HHI(0-100)"""
    if not holdings:
        return {"top5": 0, "top10": 0, "hhi": 0}
    sorted_h = sorted(holdings, key=lambda x: x.get("weight_pct", 0), reverse=True)
    top5 = sum(h.get("weight_pct", 0) for h in sorted_h[:5])
    top10 = sum(h.get("weight_pct", 0) for h in sorted_h[:10])
    hhi = sum(h.get("weight_pct", 0) ** 2 for h in holdings) / 100  # 归一化到 0-100
    return {"top5": top5, "top10": top10, "hhi": hhi}


def compute_quarter_diff(prev_q, curr_q):
    """季度对比,返回 diff 摘要"""
    if not prev_q or not curr_q:
        return None

    changes = compare_holdings(prev_q["holdings"], curr_q["holdings"])

    # 换手率: (新建仓金额 + 清仓金额) / 上季总市值
    new_buy_value = sum(h["value_usd"] for h in changes["new_buys"])
    sold_value = sum(h["value_usd"] for h in changes["sold_out"])
    prev_total = prev_q.get("total_value", 0) or 1
    turnover = (new_buy_value + sold_value) / prev_total * 100

    return {
        "n_new_buys": len(changes["new_buys"]),
        "n_sold_out": len(changes["sold_out"]),
        "n_added": len(changes["added"]),
        "n_reduced": len(changes["reduced"]),
        "turnover_pct": turnover,
        "raw": changes,
    }


def compute_investor_metrics(quarters):
    """对单位大师计算各季度指标 + QoQ"""
    metrics = []
    for i, q in enumerate(quarters):
        conc = compute_concentration(q["holdings"])
        top_holding = q["holdings"][0] if q["holdings"] else None
        m = {
            "quarter": q["quarter"],
            "report_date": q["report_date"],
            "filing_date": q["filing_date"],
            "n_holdings": len(q["holdings"]),
            "total_value": q["total_value"],
            "concentration": conc,
            "top_holding": top_holding,
            "diff_vs_prev": None,
        }
        if i > 0:
            m["diff_vs_prev"] = compute_quarter_diff(quarters[i - 1], q)
        metrics.append(m)
    return metrics


def detect_investor_style(metrics, quarters):
    """根据数据动态识别投资风格,返回标签列表 + 描述"""
    if not metrics:
        return [], ""

    latest = metrics[-1]
    tags = []

    n = latest["n_holdings"]
    top5 = latest["concentration"]["top5"]
    top10 = latest["concentration"]["top10"]
    hhi = latest["concentration"]["hhi"]

    # 集中度
    if n <= 10:
        tags.append(("极致集中", COLORS["danger"]))
    elif n <= 25:
        tags.append(("高度集中", "#ea580c"))
    elif n <= 60:
        tags.append(("中度集中", COLORS["warning"]))
    else:
        tags.append(("广泛分散", COLORS["accent"]))

    # Top5 占比
    if top5 >= 70:
        tags.append(("Top5 主导", COLORS["primary"]))
    elif top5 >= 50:
        tags.append(("头部集中", COLORS["primary"]))

    # 换手率(取最近 3 季平均)
    recent_turnovers = [
        m["diff_vs_prev"]["turnover_pct"]
        for m in metrics[-3:]
        if m["diff_vs_prev"]
    ]
    if recent_turnovers:
        avg_turnover = sum(recent_turnovers) / len(recent_turnovers)
        if avg_turnover < 5:
            tags.append(("长期持有", COLORS["accent"]))
        elif avg_turnover > 25:
            tags.append(("积极调仓", COLORS["pink"]))

    desc = f"近 {len(metrics)} 季度均值持仓 {sum(m['n_holdings'] for m in metrics)//len(metrics)} 只, Top5 占比约 {sum(m['concentration']['top5'] for m in metrics)/len(metrics):.0f}%"

    return tags, desc


def compute_consensus(all_history, quarter):
    """计算指定季度的跨大师共识"""
    cusip_data = defaultdict(lambda: {"issuer": "", "cusip": "", "holders": [], "values": []})

    for key, quarters_list in all_history.items():
        q = get_quarter(quarters_list, quarter)
        if not q:
            continue
        for h in q["holdings"]:
            cusip = h.get("cusip")
            if not cusip:
                continue
            entry = cusip_data[cusip]
            entry["cusip"] = cusip
            # 选最完整的 issuer 名(SEC 同一 cusip 不同大师写法可能略不同)
            if len(h["issuer"]) > len(entry["issuer"]):
                entry["issuer"] = h["issuer"]
            entry["holders"].append({
                "investor": key,
                "weight_pct": h.get("weight_pct", 0),
                "value_usd": h["value_usd"],
                "shares": h["shares"],
            })

    consensus_list = []
    for cusip, data in cusip_data.items():
        if len(data["holders"]) < 2:
            continue
        total_value = sum(h["value_usd"] for h in data["holders"])
        max_weight = max(h["weight_pct"] for h in data["holders"])
        avg_weight = sum(h["weight_pct"] for h in data["holders"]) / len(data["holders"])
        consensus_list.append({
            "issuer": data["issuer"],
            "cusip": cusip,
            "holders": sorted(data["holders"], key=lambda x: x["weight_pct"], reverse=True),
            "n_holders": len(data["holders"]),
            "total_value": total_value,
            "max_weight": max_weight,
            "avg_weight": avg_weight,
        })

    consensus_list.sort(key=lambda x: (x["n_holders"], x["total_value"]), reverse=True)
    return consensus_list


def compute_highlights(all_history, quarter):
    """本季亮点: 跨大师的新建共识 / 集体减持 / 集体加仓 / 重注"""
    quarters_sorted = sorted({quarter} | set(all_quarters_seen(all_history)), key=quarter_to_sort_key)
    # 找上一季
    try:
        idx = quarters_sorted.index(quarter)
        prev_quarter = quarters_sorted[idx - 1] if idx > 0 else None
    except ValueError:
        prev_quarter = None

    new_buys_cross = defaultdict(list)       # cusip -> [(investor, holding)]
    sold_out_cross = defaultdict(list)
    added_cross = defaultdict(list)
    reduced_cross = defaultdict(list)
    big_bets = []                            # 任一持仓 > 15%

    for key, quarters_list in all_history.items():
        curr_q = get_quarter(quarters_list, quarter)
        prev_q = get_quarter(quarters_list, prev_quarter) if prev_quarter else None
        if not curr_q:
            continue

        for h in curr_q["holdings"]:
            if h.get("weight_pct", 0) >= 15:
                big_bets.append({"investor": key, **h})

        if not prev_q:
            continue
        changes = compare_holdings(prev_q["holdings"], curr_q["holdings"])
        for h in changes["new_buys"]:
            new_buys_cross[h["cusip"]].append({"investor": key, **h})
        for h in changes["sold_out"]:
            sold_out_cross[h["cusip"]].append({"investor": key, **h})
        for h in changes["added"]:
            if h.get("change_pct", 0) >= 20:
                added_cross[h["cusip"]].append({"investor": key, **h})
        for h in changes["reduced"]:
            if h.get("change_pct", 0) <= -20:
                reduced_cross[h["cusip"]].append({"investor": key, **h})

    def filter_multi(d, min_n=2):
        return [{"cusip": k, "events": v} for k, v in d.items() if len(v) >= min_n]

    return {
        "new_consensus_buys": sorted(filter_multi(new_buys_cross),
                                     key=lambda x: len(x["events"]), reverse=True),
        "mass_exits": sorted(filter_multi(sold_out_cross),
                             key=lambda x: len(x["events"]), reverse=True),
        "consensus_adds": sorted(filter_multi(added_cross),
                                 key=lambda x: len(x["events"]), reverse=True),
        "consensus_reduces": sorted(filter_multi(reduced_cross),
                                    key=lambda x: len(x["events"]), reverse=True),
        "big_bets": sorted(big_bets, key=lambda x: x.get("weight_pct", 0), reverse=True),
    }


# ============ HTML 工具 ============

def fmt_money(v):
    if v is None:
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def fmt_pct(v, decimals=1):
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"


def fmt_signed_pct(v, decimals=1):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def investor_badge(key, weight_pct=None):
    info = INVESTORS[key]
    label = info["name_cn"]
    if weight_pct is not None:
        label = f"{label} {weight_pct:.1f}%"
    return f'<span class="investor-badge" style="background:{info["color"]}">{label}</span>'


def consensus_stars(n, total=5):
    return "★" * min(n, total) + "☆" * max(0, total - n)


def weight_bar(pct, color, max_pct=None, width=80):
    """单条进度条 SVG"""
    if max_pct is None:
        max_pct = max(pct * 2, 20)
    w = min(pct / max_pct * 100, 100)
    return (f'<div class="bar-wrap" style="width:{width}px"><div class="bar-fill" '
            f'style="width:{w:.1f}%; background:{color}"></div></div>')


def svg_bar_chart(values, labels, color, height=80, bar_color_fn=None):
    """简单柱状图 SVG"""
    if not values:
        return ""
    n = len(values)
    width = max(n * 40, 100)
    max_v = max(values) if values else 1
    bar_w = width / n - 8
    bars_svg = ""
    for i, (v, lbl) in enumerate(zip(values, labels)):
        bar_h = (v / max_v * (height - 24)) if max_v else 0
        x = i * (width / n) + 4
        y = height - bar_h - 18
        c = bar_color_fn(i, v) if bar_color_fn else color
        bars_svg += (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="{c}" rx="2"/>'
            f'<text x="{x + bar_w/2:.1f}" y="{height - 4}" text-anchor="middle" '
            f'font-size="10" fill="#64748b">{lbl}</text>'
        )
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'class="chart">{bars_svg}</svg>')


# ============ 页面外壳 ============

def page_shell(title, content, active_nav="index"):
    """所有页面共用的外壳: head + nav + content + footer"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    nav_items = [
        ("index", "总览", "index.html"),
        ("consensus", "共识榜", "consensus.html"),
        ("history", "历史时间线", "history.html"),
    ]
    nav_html = ""
    for key, label, href in nav_items:
        active = ' class="active"' if key == active_nav else ""
        nav_html += f'<a href="{href}"{active}>{label}</a>'

    investor_nav = ""
    for key, info in INVESTORS.items():
        active = ' class="active"' if active_nav == f"investor_{key}" else ""
        investor_nav += f'<a href="{key}.html"{active} style="--accent:{info["color"]}">{info["name_cn"]}</a>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Superinvestor Monitor</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site-header">
    <div class="container">
        <div class="brand">
            <div class="brand-tag">SUPERINVESTOR MONITOR</div>
            <h1>{title}</h1>
        </div>
        <nav class="primary-nav">{nav_html}</nav>
        <nav class="investor-nav">{investor_nav}</nav>
    </div>
</header>

<main class="container">
{content}
</main>

<footer class="site-footer">
    <div class="container">
        <div>数据源: <a href="https://www.sec.gov/edgar/sec-api-documentation" target="_blank">SEC EDGAR 13F</a> · 最后更新 {now}</div>
        <div class="muted">13F 数据有 45 天滞后 · 本站仅供学习,不构成投资建议</div>
    </div>
</footer>
</body>
</html>"""


# ============ 各页面渲染 ============

def render_index(all_history, latest_quarter, consensus, highlights, all_metrics):
    """首页 / 总览看板"""
    # 总览统计
    total_aum = 0
    n_total_positions = 0
    for key, quarters in all_history.items():
        q = get_quarter(quarters, latest_quarter)
        if q:
            total_aum += q["total_value"]
            n_total_positions += len(q["holdings"])

    # 5 位大师卡片
    investor_cards = ""
    for key, info in INVESTORS.items():
        q = get_quarter(all_history.get(key, []), latest_quarter)
        if not q:
            investor_cards += f"""
            <a class="investor-card placeholder" href="{key}.html" style="--accent:{info['color']}">
                <div class="investor-card-header">
                    <div class="investor-name">{info['name_cn']}</div>
                    <div class="investor-fund">{info['fund']}</div>
                </div>
                <div class="muted small">{latest_quarter} 暂无 13F 归档</div>
            </a>
            """
            continue

        m = all_metrics[key][-1] if all_metrics.get(key) else None
        top = q["holdings"][0] if q["holdings"] else None
        diff = m["diff_vs_prev"] if m else None

        diff_html = ""
        if diff:
            if diff["n_new_buys"] + diff["n_sold_out"] + diff["n_added"] + diff["n_reduced"] > 0:
                parts = []
                if diff["n_new_buys"]:
                    parts.append(f'<span class="tag tag-success">新 {diff["n_new_buys"]}</span>')
                if diff["n_sold_out"]:
                    parts.append(f'<span class="tag tag-danger">清 {diff["n_sold_out"]}</span>')
                if diff["n_added"]:
                    parts.append(f'<span class="tag tag-warning">加 {diff["n_added"]}</span>')
                if diff["n_reduced"]:
                    parts.append(f'<span class="tag tag-pink">减 {diff["n_reduced"]}</span>')
                diff_html = f'<div class="investor-card-diff">{"".join(parts)}</div>'
            else:
                diff_html = '<div class="investor-card-diff muted small">本季无变化</div>'
        else:
            diff_html = '<div class="investor-card-diff muted small">无对比基准</div>'

        top_html = ""
        if top:
            top_html = f"""
            <div class="investor-card-top">
                <div class="muted small">顶仓</div>
                <div class="investor-top-name">{top['issuer'][:30]}</div>
                <div class="investor-top-weight">{top.get('weight_pct', 0):.1f}%</div>
            </div>
            """

        investor_cards += f"""
        <a class="investor-card" href="{key}.html" style="--accent:{info['color']}">
            <div class="investor-card-header">
                <div class="investor-name">{info['name_cn']}</div>
                <div class="investor-fund">{info['fund']}</div>
            </div>
            <div class="investor-card-stats">
                <div class="kpi-mini">
                    <div class="kpi-mini-value">{fmt_money(q['total_value'])}</div>
                    <div class="kpi-mini-label">AUM</div>
                </div>
                <div class="kpi-mini">
                    <div class="kpi-mini-value">{len(q['holdings'])}</div>
                    <div class="kpi-mini-label">持仓</div>
                </div>
            </div>
            {top_html}
            {diff_html}
        </a>
        """

    # 本季亮点
    def render_highlight_items(items, label_fn, n=5):
        rows = ""
        for item in items[:n]:
            events = item["events"]
            issuer = events[0].get("issuer", "")[:40]
            badges = "".join(investor_badge(e["investor"]) for e in events)
            label = label_fn(events)
            rows += f"""
            <div class="highlight-row">
                <div class="highlight-stock">
                    <div class="highlight-name">{issuer}</div>
                    <div class="highlight-badges">{badges}</div>
                </div>
                <div class="highlight-label">{label}</div>
            </div>
            """
        return rows

    new_consensus_block = ""
    if highlights["new_consensus_buys"]:
        rows = render_highlight_items(
            highlights["new_consensus_buys"],
            lambda evts: f'{len(evts)} 位新建仓',
        )
        new_consensus_block = f"""
        <div class="highlight-card">
            <div class="highlight-card-header"><span class="dot success"></span>新建共识 · ≥2 位大师本季首次买入</div>
            <div class="highlight-card-body">{rows}</div>
        </div>
        """

    mass_exit_block = ""
    if highlights["mass_exits"]:
        rows = render_highlight_items(
            highlights["mass_exits"],
            lambda evts: f'{len(evts)} 位清仓',
        )
        mass_exit_block = f"""
        <div class="highlight-card">
            <div class="highlight-card-header"><span class="dot danger"></span>集体退出 · ≥2 位大师本季清仓</div>
            <div class="highlight-card-body">{rows}</div>
        </div>
        """

    consensus_add_block = ""
    if highlights["consensus_adds"]:
        rows = render_highlight_items(
            highlights["consensus_adds"],
            lambda evts: f'{len(evts)} 位加仓 ≥20%',
        )
        consensus_add_block = f"""
        <div class="highlight-card">
            <div class="highlight-card-header"><span class="dot warning"></span>集体加注 · ≥2 位大师本季大幅加仓</div>
            <div class="highlight-card-body">{rows}</div>
        </div>
        """

    big_bet_block = ""
    if highlights["big_bets"]:
        rows = ""
        for h in highlights["big_bets"][:8]:
            rows += f"""
            <div class="highlight-row">
                <div class="highlight-stock">
                    <div class="highlight-name">{h['issuer'][:40]}</div>
                    <div class="highlight-badges">{investor_badge(h['investor'])}</div>
                </div>
                <div class="highlight-label"><b>{h.get('weight_pct', 0):.1f}%</b> 单仓占比</div>
            </div>
            """
        big_bet_block = f"""
        <div class="highlight-card">
            <div class="highlight-card-header"><span class="dot purple"></span>重注信号 · 单一持仓 > 15%</div>
            <div class="highlight-card-body">{rows}</div>
        </div>
        """

    # 共识 Top 10 摘要
    consensus_top_rows = ""
    for c in consensus[:10]:
        badges = "".join(investor_badge(h["investor"], h["weight_pct"]) for h in c["holders"])
        consensus_top_rows += f"""
        <tr>
            <td><b>{c['issuer'][:40]}</b></td>
            <td>{badges}</td>
            <td class="num">{c['n_holders']}/5</td>
            <td class="num">{fmt_money(c['total_value'])}</td>
        </tr>
        """

    # 解读区块
    insight_text = build_index_insights(all_history, all_metrics, latest_quarter, consensus, highlights)

    content = f"""
    <section class="hero">
        <div class="hero-tag">最新季度 {latest_quarter}</div>
        <h2 class="hero-title">5 位大师合计 13F 持仓 {fmt_money(total_aum)}</h2>
        <div class="hero-sub">{n_total_positions} 个总持仓 · {len(consensus)} 只 ≥2 人共识标的</div>
    </section>

    <section>
        <div class="section-tag">PORTFOLIOS · 各大师概览</div>
        <div class="investor-grid">{investor_cards}</div>
    </section>

    <section>
        <div class="section-tag">THIS QUARTER'S HIGHLIGHTS · 本季亮点</div>
        <div class="highlight-grid">
            {new_consensus_block}
            {consensus_add_block}
            {mass_exit_block}
            {big_bet_block}
        </div>
    </section>

    <section>
        <div class="section-tag">CONSENSUS TOP 10 · 共识标的</div>
        <div class="section-sub">≥ 2 位大师同时持有,按共识人数排序 · <a href="consensus.html">查看完整榜单 →</a></div>
        <table class="data-table">
            <thead><tr><th>标的</th><th>持有人</th><th class="num">共识度</th><th class="num">合计市值</th></tr></thead>
            <tbody>{consensus_top_rows}</tbody>
        </table>
    </section>

    <section>
        <div class="section-tag">INSIGHTS · 本季解读</div>
        <div class="insight-box">{insight_text}</div>
    </section>
    """

    return page_shell("总览看板", content, active_nav="index")


def build_index_insights(all_history, all_metrics, latest_quarter, consensus, highlights):
    """生成首页底部的中文解读段落"""
    parts = []

    # 1. 整体仓位变化
    total_curr = 0
    total_prev = 0
    quarters_sorted = all_quarters_seen(all_history)
    if latest_quarter in quarters_sorted:
        idx = quarters_sorted.index(latest_quarter)
        prev_q = quarters_sorted[idx - 1] if idx > 0 else None
    else:
        prev_q = None
    for key, qs in all_history.items():
        c = get_quarter(qs, latest_quarter)
        p = get_quarter(qs, prev_q) if prev_q else None
        if c:
            total_curr += c["total_value"]
        if p:
            total_prev += p["total_value"]
    if total_prev:
        chg = (total_curr - total_prev) / total_prev * 100
        direction = "增长" if chg >= 0 else "缩减"
        parts.append(
            f"<p><b>整体仓位:</b> 5 位大师合计 13F 持仓 {fmt_money(total_curr)},"
            f"较上季 ({prev_q}) {direction} <b>{abs(chg):.1f}%</b>"
            f"(上季 {fmt_money(total_prev)})。</p>"
        )

    # 2. 最集中 / 最分散
    if all_metrics:
        sorted_by_n = [
            (key, mts[-1]["n_holdings"]) for key, mts in all_metrics.items() if mts
        ]
        if sorted_by_n:
            most_concentrated = min(sorted_by_n, key=lambda x: x[1])
            most_diversified = max(sorted_by_n, key=lambda x: x[1])
            parts.append(
                f"<p><b>持仓集中度:</b> "
                f"{INVESTORS[most_concentrated[0]]['name_cn']} 最集中(仅 {most_concentrated[1]} 只),"
                f"{INVESTORS[most_diversified[0]]['name_cn']} 最分散({most_diversified[1]} 只)。</p>"
            )

    # 3. 共识信号
    strong = [c for c in consensus if c["n_holders"] >= 3]
    if strong:
        names = ", ".join(c["issuer"][:25] for c in strong[:3])
        parts.append(
            f"<p><b>强共识信号:</b> {len(strong)} 只标的获 ≥3 位大师同时持有,"
            f"代表性标的: {names}。详见 <a href='consensus.html'>共识榜</a>。</p>"
        )

    # 4. 新建共识
    if highlights["new_consensus_buys"]:
        first = highlights["new_consensus_buys"][0]
        parts.append(
            f"<p><b>新建共识:</b> 本季有 {len(highlights['new_consensus_buys'])} 只标的"
            f"被多位大师同时新建仓,最强信号为"
            f"<b>{first['events'][0]['issuer'][:30]}</b> ({len(first['events'])} 位)。</p>"
        )

    if highlights["mass_exits"]:
        first = highlights["mass_exits"][0]
        parts.append(
            f"<p><b>集体退出:</b> 本季有 {len(highlights['mass_exits'])} 只标的"
            f"被多位大师同时清仓,最强信号为"
            f"<b>{first['events'][0]['issuer'][:30]}</b> ({len(first['events'])} 位)。</p>"
        )

    parts.append(
        "<p class='muted small'>"
        "解读说明: 13F 仅披露多头美股(不含债券、空头、现金、海外持仓),数据存在 45 天滞后,"
        "对市值波动天然敏感(股价涨跌也会影响 weight%)。建议结合大师本人公开访谈与年报阅读。"
        "</p>"
    )

    return "".join(parts) if parts else "<p>数据尚不足以生成解读。</p>"


def render_investor_page(key, info, quarters, metrics):
    """单位大师的详情页"""
    if not quarters:
        content = f"""
        <section class="hero" style="--accent:{info['color']}">
            <div class="hero-tag">{info['fund']}</div>
            <h2 class="hero-title">{info['name_cn']}</h2>
            <div class="hero-sub">{info.get('note', '')}</div>
        </section>
        <section><p class="muted">尚无 13F 归档,等待下次 SEC 数据抓取。</p></section>
        """
        return page_shell(info["name_cn"], content, active_nav=f"investor_{key}")

    latest = quarters[-1]
    latest_m = metrics[-1]
    conc = latest_m["concentration"]
    style_tags, style_desc = detect_investor_style(metrics, quarters)

    # 标签
    tags_html = "".join(
        f'<span class="style-tag" style="background:{c}">{t}</span>'
        for t, c in style_tags
    )

    # KPI 横条
    kpi_html = f"""
    <div class="kpi-strip" style="--accent:{info['color']}">
        <div class="kpi-cell">
            <div class="kpi-label">总 AUM</div>
            <div class="kpi-value">{fmt_money(latest['total_value'])}</div>
            <div class="kpi-sub">{latest_m['report_date']}</div>
        </div>
        <div class="kpi-cell">
            <div class="kpi-label">持仓数</div>
            <div class="kpi-value">{latest_m['n_holdings']}</div>
        </div>
        <div class="kpi-cell">
            <div class="kpi-label">Top 5 集中度</div>
            <div class="kpi-value">{conc['top5']:.1f}%</div>
        </div>
        <div class="kpi-cell">
            <div class="kpi-label">Top 10 集中度</div>
            <div class="kpi-value">{conc['top10']:.1f}%</div>
        </div>
        <div class="kpi-cell">
            <div class="kpi-label">HHI 集中指数</div>
            <div class="kpi-value">{conc['hhi']:.0f}</div>
        </div>
    """
    diff = latest_m["diff_vs_prev"]
    if diff:
        kpi_html += f"""
        <div class="kpi-cell">
            <div class="kpi-label">本季换手率</div>
            <div class="kpi-value">{diff['turnover_pct']:.1f}%</div>
            <div class="kpi-sub">vs {metrics[-2]['quarter']}</div>
        </div>
        """
    kpi_html += "</div>"

    # Top 20 持仓表
    max_w = max((h.get("weight_pct", 0) for h in latest["holdings"]), default=10)
    holdings_rows = ""
    for i, h in enumerate(latest["holdings"][:20], 1):
        w = h.get("weight_pct", 0)
        holdings_rows += f"""
        <tr>
            <td class="muted num">{i}</td>
            <td><b>{h['issuer'][:50]}</b><div class="muted small">{h.get('title_class', '')}</div></td>
            <td class="num">{fmt_money(h['value_usd'])}</td>
            <td class="num"><b style="color:{info['color']}">{w:.2f}%</b></td>
            <td>{weight_bar(w, info['color'], max_pct=max_w)}</td>
            <td class="num muted">{h['shares']:,}</td>
        </tr>
        """

    # 季度演化时间线
    timeline_html = ""
    for m in reversed(metrics):
        q_data = get_quarter(quarters, m["quarter"])
        if not q_data:
            continue

        diff_html = ""
        if m["diff_vs_prev"]:
            d = m["diff_vs_prev"]
            raw = d["raw"]

            def diff_chips(items, color, label_fn, limit=5):
                if not items:
                    return ""
                chips = "".join(
                    f'<span class="chip" style="background:{color}22; color:{color}">{label_fn(it)}</span>'
                    for it in items[:limit]
                )
                more = f' <span class="muted small">+{len(items)-limit} 更多</span>' if len(items) > limit else ''
                return chips + more

            new_chips = diff_chips(raw["new_buys"], COLORS["accent"],
                                   lambda h: f"{h['issuer'][:18]} {h.get('weight_pct', 0):.1f}%")
            sold_chips = diff_chips(raw["sold_out"], COLORS["danger"],
                                    lambda h: h['issuer'][:18])
            added_chips = diff_chips(raw["added"], COLORS["warning"],
                                     lambda h: f"{h['issuer'][:18]} +{h.get('change_pct', 0):.0f}%")
            reduced_chips = diff_chips(raw["reduced"], COLORS["pink"],
                                       lambda h: f"{h['issuer'][:18]} {h.get('change_pct', 0):.0f}%")

            blocks = []
            if new_chips:
                blocks.append(f'<div class="qoq-block"><div class="qoq-block-label">新建仓 ({d["n_new_buys"]})</div>{new_chips}</div>')
            if added_chips:
                blocks.append(f'<div class="qoq-block"><div class="qoq-block-label">加仓 ({d["n_added"]})</div>{added_chips}</div>')
            if sold_chips:
                blocks.append(f'<div class="qoq-block"><div class="qoq-block-label">清仓 ({d["n_sold_out"]})</div>{sold_chips}</div>')
            if reduced_chips:
                blocks.append(f'<div class="qoq-block"><div class="qoq-block-label">减仓 ({d["n_reduced"]})</div>{reduced_chips}</div>')

            diff_html = "".join(blocks) if blocks else '<div class="muted">本季无持仓变化</div>'
        else:
            diff_html = '<div class="muted small">最早归档季度,无对比基准</div>'

        top_h = q_data["holdings"][0] if q_data["holdings"] else None
        top_label = f"{top_h['issuer'][:25]} ({top_h.get('weight_pct', 0):.1f}%)" if top_h else "—"

        timeline_html += f"""
        <div class="quarter-card">
            <div class="quarter-card-header" style="border-left-color:{info['color']}">
                <div class="quarter-card-title">{m['quarter']}</div>
                <div class="quarter-card-meta">
                    报告期 {m['report_date']} · 提交 {m['filing_date']}
                </div>
                <div class="quarter-card-stats">
                    <span>AUM <b>{fmt_money(m['total_value'])}</b></span>
                    <span>持仓 <b>{m['n_holdings']}</b></span>
                    <span>顶仓 <b>{top_label}</b></span>
                </div>
            </div>
            <div class="quarter-card-body">{diff_html}</div>
        </div>
        """

    # AUM 时间线柱状图
    aum_values = [m["total_value"] / 1e9 for m in metrics]
    aum_labels = [m["quarter"] for m in metrics]
    aum_chart = svg_bar_chart(aum_values, aum_labels, info["color"], height=120)

    # 解读
    insight = build_investor_insights(key, info, quarters, metrics, style_tags, style_desc)

    content = f"""
    <section class="hero" style="--accent:{info['color']}">
        <div class="hero-tag">{info['fund']}</div>
        <h2 class="hero-title">{info['name_cn']} <span class="muted">{info['name']}</span></h2>
        <div class="hero-sub">{info.get('note', '')}</div>
        <div class="hero-tags">{tags_html}</div>
    </section>

    {kpi_html}

    <section>
        <div class="section-tag">PORTFOLIO TOP 20 · 最新持仓 ({latest_m['quarter']})</div>
        <table class="data-table">
            <thead><tr><th>#</th><th>标的</th><th class="num">市值</th><th class="num">权重</th><th>权重条</th><th class="num">股数</th></tr></thead>
            <tbody>{holdings_rows}</tbody>
        </table>
    </section>

    <section>
        <div class="section-tag">AUM TIMELINE · 季度总市值变化</div>
        <div class="chart-wrap">{aum_chart}</div>
    </section>

    <section>
        <div class="section-tag">QUARTERLY EVOLUTION · 季度演化</div>
        <div class="quarter-list">{timeline_html}</div>
    </section>

    <section>
        <div class="section-tag">INSIGHTS · 投资风格解读</div>
        <div class="insight-box">{insight}</div>
    </section>
    """

    return page_shell(info["name_cn"], content, active_nav=f"investor_{key}")


def build_investor_insights(key, info, quarters, metrics, style_tags, style_desc):
    """单位大师的解读段落"""
    parts = []
    latest_m = metrics[-1]
    latest = quarters[-1]

    parts.append(f"<p><b>风格速写:</b> {style_desc}</p>")

    top_h = latest["holdings"][0] if latest["holdings"] else None
    if top_h:
        parts.append(
            f"<p><b>核心仓位:</b> 头号持仓 <b>{top_h['issuer'][:40]}</b> 占组合 "
            f"<b>{top_h.get('weight_pct', 0):.1f}%</b>"
            f"(市值 {fmt_money(top_h['value_usd'])})。"
        )
        if latest_m["concentration"]["top5"] > 70:
            parts[-1] += " 整体呈现'核心持仓主导'的极致集中风格。</p>"
        elif latest_m["concentration"]["top5"] > 50:
            parts[-1] += " Top 5 已占组合大半,头部集中明显。</p>"
        else:
            parts[-1] += " 持仓相对均衡。</p>"

    # AUM 变化趋势
    if len(metrics) >= 2:
        aum_first = metrics[0]["total_value"]
        aum_last = metrics[-1]["total_value"]
        chg = (aum_last - aum_first) / aum_first * 100 if aum_first else 0
        direction = "增长" if chg >= 0 else "缩减"
        parts.append(
            f"<p><b>资金规模:</b> 从 {metrics[0]['quarter']} 的 {fmt_money(aum_first)} "
            f"到 {metrics[-1]['quarter']} 的 {fmt_money(aum_last)},"
            f"累计{direction} <b>{abs(chg):.1f}%</b>。"
            "AUM 变化既包含资金增减,也受持仓股价波动影响,无法直接判断主动加减仓。</p>"
        )

    # 最近一季关键变化
    diff = latest_m["diff_vs_prev"]
    if diff:
        notable = []
        if diff["n_new_buys"]:
            notable.append(f"新建 {diff['n_new_buys']} 只")
        if diff["n_sold_out"]:
            notable.append(f"清仓 {diff['n_sold_out']} 只")
        if diff["n_added"]:
            notable.append(f"加仓 {diff['n_added']} 只")
        if diff["n_reduced"]:
            notable.append(f"减仓 {diff['n_reduced']} 只")
        if notable:
            parts.append(
                f"<p><b>本季动作:</b> {' / '.join(notable)},换手率 <b>{diff['turnover_pct']:.1f}%</b>。"
                + ("低换手率与价投长持风格一致。" if diff['turnover_pct'] < 10 else
                   "换手相对活跃,值得关注新进标的的逻辑。" if diff['turnover_pct'] > 25 else
                   "换手率适中。") + "</p>"
            )

    return "".join(parts)


def render_consensus_page(consensus, latest_quarter, all_history):
    """共识榜全表"""
    if not consensus:
        content = '<section><p class="muted">本季无共识标的(≥2 人持有)。</p></section>'
        return page_shell(f"共识榜 · {latest_quarter}", content, active_nav="consensus")

    # 分层: 强共识 (≥3 人) vs 普通共识 (=2 人)
    strong = [c for c in consensus if c["n_holders"] >= 3]
    normal = [c for c in consensus if c["n_holders"] == 2]

    def render_consensus_row(c, highlight=False):
        badges = "".join(investor_badge(h["investor"], h["weight_pct"]) for h in c["holders"])
        stars = consensus_stars(c["n_holders"])
        avg_w = c["avg_weight"]
        max_w = c["max_weight"]
        row_class = ' class="strong"' if highlight else ""
        return f"""
        <tr{row_class}>
            <td><b>{c['issuer'][:50]}</b><div class="muted small">{c['cusip']}</div></td>
            <td>{badges}</td>
            <td class="num"><b>{c['n_holders']}/5</b><div class="muted small">{stars}</div></td>
            <td class="num">{fmt_money(c['total_value'])}</td>
            <td class="num">{avg_w:.1f}%<div class="muted small">最大 {max_w:.1f}%</div></td>
        </tr>
        """

    strong_html = ""
    if strong:
        rows = "".join(render_consensus_row(c, highlight=True) for c in strong)
        strong_html = f"""
        <section>
            <div class="section-tag">STRONG CONSENSUS · 强共识 ≥3 位</div>
            <div class="section-sub">3 位及以上大师同时持有,通常代表价投圈的高确信标的</div>
            <table class="data-table consensus-table">
                <thead><tr><th>标的</th><th>持有人 (含权重)</th><th class="num">共识度</th><th class="num">合计市值</th><th class="num">平均权重</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </section>
        """

    normal_html = ""
    if normal:
        rows = "".join(render_consensus_row(c) for c in normal)
        normal_html = f"""
        <section>
            <div class="section-tag">2-PERSON CONSENSUS · 双人共识</div>
            <div class="section-sub">恰好 2 位大师同时持有</div>
            <table class="data-table consensus-table">
                <thead><tr><th>标的</th><th>持有人 (含权重)</th><th class="num">共识度</th><th class="num">合计市值</th><th class="num">平均权重</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </section>
        """

    # 解读
    insight = f"""
    <p><b>读榜要点:</b> 共识标的并不等于"买入信号"——大师持有时间、买入成本、组合中的角色定位
    各不相同。但 ≥3 位大师同时持有的标的(本季 <b>{len(strong)}</b> 只),通常代表价值投资圈的
    高确信品种,值得作为研究起点深入挖掘。</p>
    <p><b>关注顺序:</b>
    ① 看持有人覆盖面(5 人 > 4 人 > 3 人);
    ② 看平均权重(平均 > 5% 说明大家都在重仓);
    ③ 看最近季度变化(本季是否多人共同加仓 → 见 <a href="index.html">总览页·本季亮点</a>)。</p>
    <p class="muted small">数据基于 {latest_quarter} 季报。13F 仅披露多头美股,可能遗漏大师在
    海外/港股/现金/对冲头寸上的真实意图。</p>
    """

    content = f"""
    <section class="hero">
        <div class="hero-tag">共识榜 · {latest_quarter}</div>
        <h2 class="hero-title">{len(consensus)} 只标的 ≥2 位大师共识</h2>
        <div class="hero-sub">{len(strong)} 只强共识 (≥3 位) + {len(normal)} 只双人共识</div>
    </section>

    {strong_html}
    {normal_html}

    <section>
        <div class="section-tag">INSIGHTS · 解读</div>
        <div class="insight-box">{insight}</div>
    </section>
    """

    return page_shell(f"共识榜 · {latest_quarter}", content, active_nav="consensus")


def render_history_page(all_history, all_metrics):
    """跨季度时间线 / 共识演化"""
    quarters = all_quarters_seen(all_history)
    if not quarters:
        content = '<section><p class="muted">尚无历史归档。</p></section>'
        return page_shell("历史时间线", content, active_nav="history")

    # 按季度构建矩阵
    rows_html = ""
    for q in reversed(quarters):
        # 每位大师在这一季的快照
        cells = ""
        for key, info in INVESTORS.items():
            q_data = get_quarter(all_history.get(key, []), q)
            if q_data:
                metrics_list = all_metrics.get(key, [])
                m = next((x for x in metrics_list if x["quarter"] == q), None)
                top = q_data["holdings"][0] if q_data["holdings"] else None
                diff_str = ""
                if m and m["diff_vs_prev"]:
                    d = m["diff_vs_prev"]
                    diff_str = (
                        f'<div class="hist-diff muted small">'
                        f'新 {d["n_new_buys"]} · 清 {d["n_sold_out"]} · '
                        f'加 {d["n_added"]} · 减 {d["n_reduced"]}'
                        f'</div>'
                    )

                top_str = ""
                if top:
                    top_str = f'<div class="hist-top muted small">顶仓 {top["issuer"][:20]} {top.get("weight_pct", 0):.1f}%</div>'

                cells += f"""
                <div class="hist-cell" style="--accent:{info['color']}">
                    <div class="hist-cell-name">{info['name_cn']}</div>
                    <div class="hist-cell-aum">{fmt_money(q_data['total_value'])}</div>
                    <div class="muted small">{len(q_data['holdings'])} 只持仓</div>
                    {top_str}
                    {diff_str}
                </div>
                """
            else:
                cells += f"""
                <div class="hist-cell empty">
                    <div class="hist-cell-name muted">{info['name_cn']}</div>
                    <div class="muted small">—</div>
                </div>
                """

        # 共识快照
        consensus_q = compute_consensus(all_history, q)
        strong_count = sum(1 for c in consensus_q if c["n_holders"] >= 3)

        rows_html += f"""
        <div class="hist-row">
            <div class="hist-row-header">
                <div class="hist-row-quarter">{q}</div>
                <div class="hist-row-summary">
                    {len(consensus_q)} 只 ≥2 人共识 · 其中 <b>{strong_count}</b> 只强共识
                </div>
            </div>
            <div class="hist-grid">{cells}</div>
        </div>
        """

    # 13F 公布日历
    today = date.today()
    upcoming_rows = ""
    for q_name, m, d in CONFIG["13f_due_dates"]:
        due = date(today.year, m, d)
        if due < today:
            due = date(today.year + 1, m, d)
        days = (due - today).days
        upcoming_rows += f"""
        <tr>
            <td><b>{due.strftime('%Y-%m-%d')}</b></td>
            <td>{q_name}</td>
            <td class="num"><b>{days}</b> 天后</td>
        </tr>
        """

    content = f"""
    <section class="hero">
        <div class="hero-tag">历史时间线</div>
        <h2 class="hero-title">{len(quarters)} 个季度归档,5 位大师横向对比</h2>
        <div class="hero-sub">最近 {quarters[-1] if quarters else '—'} → {quarters[0] if quarters else '—'}</div>
    </section>

    <section>
        <div class="section-tag">QUARTERLY MATRIX · 季度矩阵</div>
        {rows_html}
    </section>

    <section>
        <div class="section-tag">UPCOMING 13F · 下一次公布</div>
        <table class="data-table">
            <thead><tr><th>预期公布日</th><th>季度</th><th class="num">倒计时</th></tr></thead>
            <tbody>{upcoming_rows}</tbody>
        </table>
    </section>
    """

    return page_shell("历史时间线", content, active_nav="history")


# ============ CSS ============

def build_css():
    return f"""
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: {COLORS['bg']};
    color: {COLORS['text']};
    line-height: 1.5;
    font-size: 14px;
}}

a {{ color: {COLORS['primary']}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

.container {{
    max-width: 1180px;
    margin: 0 auto;
    padding: 0 20px;
}}

.muted {{ color: {COLORS['text_muted']}; }}
.small {{ font-size: 11px; }}
.num {{ font-variant-numeric: tabular-nums; text-align: right; }}

/* ============ Header / Nav ============ */
.site-header {{
    background: white;
    border-bottom: 2px solid {COLORS['border']};
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
}}
.brand {{
    padding: 16px 0 12px;
}}
.brand-tag {{
    font-size: 10px;
    letter-spacing: 3px;
    color: {COLORS['primary']};
    font-weight: 700;
}}
.brand h1 {{
    font-size: 22px;
    font-weight: 700;
    margin-top: 4px;
    color: {COLORS['text']};
}}

.primary-nav, .investor-nav {{
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
}}
.primary-nav {{
    padding: 8px 0;
    border-top: 1px solid {COLORS['border']};
}}
.investor-nav {{
    padding: 8px 0 12px;
    border-top: 1px dashed {COLORS['border']};
}}
.primary-nav a {{
    padding: 6px 14px;
    border-radius: 6px;
    color: {COLORS['text_muted']};
    font-weight: 600;
    font-size: 13px;
}}
.primary-nav a:hover {{ background: {COLORS['bg_hover']}; text-decoration: none; }}
.primary-nav a.active {{ background: {COLORS['primary']}; color: white; }}

.investor-nav a {{
    padding: 4px 10px;
    border-radius: 4px;
    color: var(--accent, {COLORS['neutral']});
    font-weight: 600;
    font-size: 12px;
    border: 1px solid {COLORS['border']};
}}
.investor-nav a:hover {{ background: {COLORS['bg_hover']}; text-decoration: none; }}
.investor-nav a.active {{
    background: var(--accent, {COLORS['primary']});
    color: white;
    border-color: var(--accent, {COLORS['primary']});
}}

/* ============ Main / Section ============ */
main.container {{
    padding-top: 24px;
    padding-bottom: 40px;
}}

section {{
    margin-bottom: 32px;
}}

.section-tag {{
    font-size: 11px;
    letter-spacing: 2px;
    color: {COLORS['primary']};
    font-weight: 700;
    margin-bottom: 8px;
}}
.section-sub {{
    font-size: 12px;
    color: {COLORS['text_muted']};
    margin-bottom: 12px;
}}

/* ============ Hero ============ */
.hero {{
    background: linear-gradient(135deg, {COLORS['primary']} 0%, {COLORS['primary_dark']} 100%);
    color: white;
    padding: 28px 24px;
    border-radius: 12px;
    margin-bottom: 28px;
}}
.hero[style*="--accent"] {{
    background: linear-gradient(135deg, var(--accent) 0%, color-mix(in srgb, var(--accent) 80%, black) 100%);
}}
.hero-tag {{
    font-size: 11px;
    letter-spacing: 2px;
    opacity: 0.85;
    font-weight: 600;
}}
.hero-title {{
    font-size: 26px;
    font-weight: 700;
    margin-top: 6px;
}}
.hero-title .muted {{ color: rgba(255,255,255,0.6); font-weight: 400; font-size: 14px; }}
.hero-sub {{
    font-size: 14px;
    opacity: 0.85;
    margin-top: 6px;
}}
.hero-tags {{
    margin-top: 12px;
    display: flex; gap: 6px; flex-wrap: wrap;
}}
.style-tag {{
    color: white;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
}}

/* ============ Investor Cards Grid ============ */
.investor-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
}}
.investor-card {{
    background: white;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    border-left: 4px solid var(--accent, {COLORS['primary']});
    padding: 16px;
    color: {COLORS['text']};
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: transform 0.1s, box-shadow 0.1s;
}}
.investor-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    text-decoration: none;
}}
.investor-card.placeholder {{ opacity: 0.6; }}

.investor-name {{
    font-size: 16px;
    font-weight: 700;
}}
.investor-fund {{
    font-size: 11px;
    color: {COLORS['text_muted']};
    margin-top: 2px;
}}

.investor-card-stats {{
    display: flex;
    gap: 16px;
    border-top: 1px solid {COLORS['border']};
    padding-top: 12px;
}}
.kpi-mini {{ flex: 1; }}
.kpi-mini-value {{
    font-size: 18px;
    font-weight: 700;
    color: var(--accent, {COLORS['primary']});
}}
.kpi-mini-label {{
    font-size: 10px;
    color: {COLORS['text_muted']};
    letter-spacing: 1px;
}}
.investor-card-top {{ font-size: 12px; }}
.investor-top-name {{ font-weight: 600; }}
.investor-top-weight {{
    font-size: 16px;
    font-weight: 700;
    color: var(--accent);
}}
.investor-card-diff {{
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 4px;
}}

/* ============ Tags / Chips / Badges ============ */
.tag {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
}}
.tag-success {{ background: #dcfce7; color: #15803d; }}
.tag-danger {{ background: #fee2e2; color: #b91c1c; }}
.tag-warning {{ background: #fef3c7; color: #a16207; }}
.tag-pink {{ background: #fce7f3; color: #9f1239; }}

.chip {{
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    margin: 2px;
    font-weight: 500;
}}

.investor-badge {{
    display: inline-block;
    color: white;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    margin: 2px;
    font-weight: 600;
}}

/* ============ KPI Strip ============ */
.kpi-strip {{
    display: flex;
    flex-wrap: wrap;
    background: white;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    border-top: 4px solid var(--accent, {COLORS['primary']});
    padding: 16px 20px;
    margin-bottom: 24px;
    gap: 24px;
}}
.kpi-cell {{
    flex: 1;
    min-width: 120px;
}}
.kpi-label {{
    font-size: 10px;
    letter-spacing: 1.5px;
    color: {COLORS['text_muted']};
    font-weight: 600;
}}
.kpi-value {{
    font-size: 22px;
    font-weight: 700;
    color: var(--accent, {COLORS['primary']});
    margin-top: 4px;
}}
.kpi-sub {{
    font-size: 11px;
    color: {COLORS['text_muted']};
}}

/* ============ Tables ============ */
.data-table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    overflow: hidden;
}}
.data-table thead {{
    background: {COLORS['bg_card']};
}}
.data-table th {{
    padding: 10px 12px;
    text-align: left;
    font-size: 11px;
    letter-spacing: 1px;
    color: {COLORS['text_muted']};
    font-weight: 600;
    border-bottom: 2px solid {COLORS['border']};
}}
.data-table th.num {{ text-align: right; }}
.data-table td {{
    padding: 10px 12px;
    border-bottom: 1px solid {COLORS['border']};
    font-size: 13px;
}}
.data-table tr:last-child td {{ border-bottom: none; }}
.data-table tr:hover {{ background: {COLORS['bg_hover']}; }}
.data-table tr.strong {{ background: #f0fdfa; }}
.data-table tr.strong:hover {{ background: #ccfbf1; }}

.consensus-table td {{ vertical-align: middle; }}

/* ============ Highlights ============ */
.highlight-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
}}
.highlight-card {{
    background: white;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    overflow: hidden;
}}
.highlight-card-header {{
    padding: 12px 16px;
    background: {COLORS['bg_card']};
    font-weight: 600;
    font-size: 13px;
    display: flex; align-items: center; gap: 8px;
}}
.dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
.dot.success {{ background: {COLORS['accent']}; }}
.dot.danger {{ background: {COLORS['danger']}; }}
.dot.warning {{ background: {COLORS['warning']}; }}
.dot.purple {{ background: {COLORS['purple']}; }}

.highlight-card-body {{ padding: 8px 16px; }}
.highlight-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid {COLORS['border']};
    gap: 12px;
}}
.highlight-row:last-child {{ border-bottom: none; }}
.highlight-stock {{ flex: 1; min-width: 0; }}
.highlight-name {{
    font-size: 13px;
    font-weight: 600;
}}
.highlight-badges {{
    margin-top: 4px;
}}
.highlight-label {{
    font-size: 12px;
    color: {COLORS['text_muted']};
    text-align: right;
    white-space: nowrap;
}}

/* ============ Quarter Cards / Timeline ============ */
.quarter-list {{
    display: flex;
    flex-direction: column;
    gap: 16px;
}}
.quarter-card {{
    background: white;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    overflow: hidden;
}}
.quarter-card-header {{
    padding: 14px 18px;
    background: {COLORS['bg_card']};
    border-left: 4px solid {COLORS['primary']};
}}
.quarter-card-title {{
    font-size: 18px;
    font-weight: 700;
}}
.quarter-card-meta {{
    font-size: 11px;
    color: {COLORS['text_muted']};
    margin-top: 2px;
}}
.quarter-card-stats {{
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 12px;
    color: {COLORS['text_muted']};
    flex-wrap: wrap;
}}
.quarter-card-body {{
    padding: 16px 18px;
}}
.qoq-block {{
    margin: 8px 0;
}}
.qoq-block-label {{
    font-size: 11px;
    color: {COLORS['text_muted']};
    margin-bottom: 4px;
    letter-spacing: 1px;
    font-weight: 600;
}}

/* ============ Bars / Charts ============ */
.bar-wrap {{
    display: inline-block;
    height: 8px;
    background: {COLORS['bg_hover']};
    border-radius: 4px;
    overflow: hidden;
    vertical-align: middle;
}}
.bar-fill {{
    height: 100%;
}}

.chart-wrap {{
    background: white;
    padding: 16px;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    overflow-x: auto;
}}
.chart {{ display: block; }}

/* ============ Insights ============ */
.insight-box {{
    background: white;
    padding: 20px 24px;
    border-radius: 8px;
    border-left: 4px solid {COLORS['primary']};
    border: 1px solid {COLORS['border']};
    border-left: 4px solid {COLORS['primary']};
}}
.insight-box p {{
    margin-bottom: 10px;
    line-height: 1.7;
}}
.insight-box p:last-child {{ margin-bottom: 0; }}

/* ============ History Page ============ */
.hist-row {{
    background: white;
    border-radius: 8px;
    border: 1px solid {COLORS['border']};
    margin-bottom: 16px;
    overflow: hidden;
}}
.hist-row-header {{
    padding: 12px 16px;
    background: {COLORS['bg_card']};
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid {COLORS['border']};
}}
.hist-row-quarter {{
    font-size: 18px;
    font-weight: 700;
}}
.hist-row-summary {{
    font-size: 12px;
    color: {COLORS['text_muted']};
}}
.hist-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1px;
    background: {COLORS['border']};
}}
.hist-cell {{
    background: white;
    padding: 12px;
    border-left: 3px solid var(--accent, {COLORS['neutral']});
}}
.hist-cell.empty {{ background: {COLORS['bg_card']}; }}
.hist-cell-name {{
    font-size: 12px;
    font-weight: 700;
}}
.hist-cell-aum {{
    font-size: 15px;
    font-weight: 700;
    color: var(--accent);
    margin-top: 4px;
}}
.hist-top {{ margin-top: 6px; }}
.hist-diff {{ margin-top: 4px; }}

/* ============ Footer ============ */
.site-footer {{
    border-top: 1px solid {COLORS['border']};
    padding: 20px 0;
    margin-top: 32px;
    text-align: center;
    color: {COLORS['text_muted']};
    font-size: 12px;
    background: white;
}}
.site-footer a {{ color: {COLORS['primary']}; }}

/* ============ Mobile ============ */
@media (max-width: 640px) {{
    .hero-title {{ font-size: 20px; }}
    .kpi-cell {{ min-width: 100px; }}
    .kpi-value {{ font-size: 18px; }}
    .investor-card-stats {{ gap: 8px; }}
    .primary-nav a {{ padding: 4px 10px; font-size: 12px; }}
}}
"""


# ============ Main ============

def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"   ✅ {path} ({len(content):,} 字符)")


def main():
    print(f"\n{'='*60}\n🏗️  构建静态站点\n{'='*60}\n")

    # 1. 加载所有历史
    all_history = load_all_history()
    n_quarters_total = sum(len(qs) for qs in all_history.values())
    print(f"📂 加载历史: {n_quarters_total} 份归档跨 {len(all_history)} 位大师")

    if n_quarters_total == 0:
        print("\n⚠️  history/ 目录为空,请先运行 python backfill_history.py")
        return

    # 2. 跨大师统一最新季
    latest_quarter = latest_quarter_across_investors(all_history)
    print(f"🎯 最新季度: {latest_quarter}")

    # 3. 计算每位大师指标
    all_metrics = {key: compute_investor_metrics(quarters)
                   for key, quarters in all_history.items()}

    # 4. 共识与亮点
    consensus = compute_consensus(all_history, latest_quarter)
    highlights = compute_highlights(all_history, latest_quarter)
    print(f"🤝 共识标的: {len(consensus)} 只 (其中 {sum(1 for c in consensus if c['n_holders'] >= 3)} 只 ≥3 位)")
    print(f"💡 本季亮点: 新共识 {len(highlights['new_consensus_buys'])}, "
          f"集体加注 {len(highlights['consensus_adds'])}, "
          f"集体退出 {len(highlights['mass_exits'])}, "
          f"重注 {len(highlights['big_bets'])}")

    # 5. 输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "assets"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "data"), exist_ok=True)

    print(f"\n📝 生成页面:")

    # 6. 写 CSS
    write_file(os.path.join(OUTPUT_DIR, "assets", "style.css"), build_css())

    # 7. 首页
    write_file(
        os.path.join(OUTPUT_DIR, "index.html"),
        render_index(all_history, latest_quarter, consensus, highlights, all_metrics),
    )

    # 8. 共识页
    write_file(
        os.path.join(OUTPUT_DIR, "consensus.html"),
        render_consensus_page(consensus, latest_quarter, all_history),
    )

    # 9. 历史页
    write_file(
        os.path.join(OUTPUT_DIR, "history.html"),
        render_history_page(all_history, all_metrics),
    )

    # 10. 各大师页
    for key, info in INVESTORS.items():
        quarters = all_history.get(key, [])
        metrics = all_metrics.get(key, [])
        write_file(
            os.path.join(OUTPUT_DIR, f"{key}.html"),
            render_investor_page(key, info, quarters, metrics),
        )

    # 11. JSON snapshot (供未来交互式扩展)
    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "latest_quarter": latest_quarter,
        "investors": {key: {"info": INVESTORS[key], "metrics": all_metrics[key]}
                      for key in INVESTORS},
        "consensus_count": len(consensus),
    }
    write_file(
        os.path.join(OUTPUT_DIR, "data", "snapshot.json"),
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
    )

    print(f"\n{'='*60}\n✅ 构建完成. 站点位于 {OUTPUT_DIR}/\n{'='*60}\n")


if __name__ == "__main__":
    main()
