"""
大师持仓监控脚本 v1.0
监控 5 位价值投资大师的 SEC 13F 持仓变化

监控对象:
  1. Warren Buffett - Berkshire Hathaway (CIK: 1067983)
  2. 段永平 - H&H International Investment (CIK: 1759760)
  3. 李录 - Himalaya Capital (CIK: 1709323)
  4. Howard Marks - Oaktree Capital (CIK: 949509)
  5. Mohnish Pabrai - Dalal Street (CIK: 1173334)

数据源: SEC EDGAR 官方 API (免费, 权威)
风格: 蓝绿现代清爽风 (与 ETF 监控 v3 一致)
"""

import os
import sys
import json
import time
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from xml.etree import ElementTree as ET

# ============ 配置区 ============
CONFIG = {
    # SEC EDGAR API 要求所有请求带 User-Agent (个人邮箱即可)
    "user_agent": "SuperinvestorMonitor your_email@gmail.com",  # ⚠️ 改成你的邮箱

    # 跟踪的 5 位大师
    "investors": {
        "Buffett": {
            "name": "Warren Buffett",
            "name_cn": "巴菲特",
            "fund": "Berkshire Hathaway",
            "cik": "0001067983",
            "color": "#0891b2",
            "note": "已于 2026年1月1日 卸任 CEO,继任者: Greg Abel",
        },
        "Duan": {
            "name": "Duan Yongping",
            "name_cn": "段永平",
            "fund": "H&H International Investment",
            "cik": "0001759760",
            "color": "#10b981",
            "note": "雪球 ID: 大道无形我有型",
        },
        "LiLu": {
            "name": "Li Lu",
            "name_cn": "李录",
            "fund": "Himalaya Capital Management",
            "cik": "0001709323",
            "color": "#8b5cf6",
            "note": "芒格亲选接班人,极致集中",
        },
        "Marks": {
            "name": "Howard Marks",
            "name_cn": "霍华德·马克斯",
            "fund": "Oaktree Capital Management",
            "cik": "0000949509",
            "color": "#f59e0b",
            "note": "周期意识+市场温度计,信贷为主",
        },
        "Pabrai": {
            "name": "Mohnish Pabrai",
            "name_cn": "莫尼什·帕伯莱",
            "fund": "Dalal Street, LLC",
            "cik": "0001549575",
            "color": "#ec4899",
            "note": "自称克隆者,极致集中,全球视野",
        },
    },

    # 邮件配置
    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender": "qixin202401@gmail.com",       # ⚠️ 改成你的 Gmail
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "recipient": "qixin202401@gmail.com",    # ⚠️ 改成你的 Gmail
    },

    # 状态文件
    "state_file": "superinvestor_state.json",

    # 周报: 每周哪一天 (0=周一)
    "weekly_report_day": 0,

    # 13F 公布期: 这些日期前后会更频繁检查 (但脚本只在工作日运行)
    "13f_due_dates": [
        ("Q1", 5, 15),
        ("Q2", 8, 14),
        ("Q3", 11, 14),
        ("Q4", 2, 14),
    ],
}

# 现代清爽蓝绿风配色
COLORS = {
    "primary": "#0891b2",
    "primary_dark": "#0e7490",
    "accent": "#10b981",
    "danger": "#dc2626",
    "warning": "#f59e0b",
    "success": "#10b981",
    "neutral": "#64748b",
    "bg_card": "#f8fafc",
    "bg_hover": "#f1f5f9",
    "border": "#e2e8f0",
    "text": "#1e293b",
    "text_muted": "#64748b",
}


# ============ SEC EDGAR API 封装 ============

def sec_request(url, max_retries=3):
    """带 User-Agent 的 SEC 请求"""
    headers = {
        "User-Agent": CONFIG["user_agent"],
        "Accept": "application/json, text/xml, */*",
    }
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:  # 限流
                wait = 2 ** attempt
                print(f"   ⏸️ 限流,等待 {wait}s 重试...")
                time.sleep(wait)
                continue
            elif e.code == 404:
                return None
            else:
                print(f"   ⚠️ HTTP {e.code}: {url}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None
        except Exception as e:
            print(f"   ⚠️ 请求失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return None
    return None


def get_recent_filings(cik):
    """获取某 CIK 的最新提交记录"""
    cik_no_zero = cik.lstrip("0")
    cik_padded = cik_no_zero.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    data = sec_request(url)
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception as e:
        print(f"   ⚠️ JSON 解析失败: {e}")
        return None


def find_latest_13f(submissions_data):
    """从 submissions 数据中找到最新的 13F-HR"""
    if not submissions_data:
        return None
    recent = submissions_data.get("filings", {}).get("recent", {})
    if not recent:
        return None

    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form == "13F-HR":
            return {
                "accession": accession_numbers[i],
                "filing_date": filing_dates[i],
                "report_date": report_dates[i],
                "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
            }
    return None


def fetch_13f_holdings(cik, filing):
    """获取 13F 持仓数据"""
    cik_no_zero = cik.lstrip("0")
    accession_clean = filing["accession"].replace("-", "")
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_no_zero}/{accession_clean}"

    # 先获取该 filing 的文件目录
    index_json_url = f"{base_url}/index.json"
    index_data = sec_request(index_json_url)

    info_table_url = None
    candidate_xmls = []

    if index_data:
        try:
            idx = json.loads(index_data)
            items = idx.get("directory", {}).get("item", [])
            # 先找明显是 infotable 的
            for item in items:
                name = item.get("name", "")
                name_lower = name.lower()
                if name_lower.endswith(".xml"):
                    # 排除 primary_doc.xml (那是封面文件,不含持仓)
                    if "primary_doc" in name_lower:
                        continue
                    candidate_xmls.append(name)
                    if "infotable" in name_lower or "informationtable" in name_lower:
                        info_table_url = f"{base_url}/{name}"
                        break
            # 如果没找到明显的,尝试所有 xml(排除 primary_doc)
            if not info_table_url and candidate_xmls:
                # 探测每个 xml,看哪个含 <infoTable>
                for name in candidate_xmls:
                    test_url = f"{base_url}/{name}"
                    test_data = sec_request(test_url)
                    if test_data and b"infoTable" in test_data:
                        info_table_url = test_url
                        print(f"   🔍 通过探测找到信息表: {name}")
                        break
                    time.sleep(0.3)
        except Exception as e:
            print(f"   ⚠️ 解析索引失败: {e}")

    if not info_table_url:
        # 最后兜底:列出目录内容辅助调试
        if candidate_xmls:
            print(f"   ⚠️ 找不到 13F 信息表,目录中的 XML: {candidate_xmls[:5]}")
        else:
            print(f"   ⚠️ 找不到 13F 信息表 (目录为空)")
        return []

    xml_data = sec_request(info_table_url)
    if not xml_data:
        return []

    return parse_13f_xml(xml_data)


def parse_13f_xml(xml_bytes):
    """解析 13F XML 信息表"""
    holdings = []
    try:
        import re
        text = xml_bytes.decode("utf-8", errors="ignore")

        # 关键清理步骤(按顺序):
        # 1. 移除所有 xmlns 声明 (xmlns="..." 和 xmlns:xxx="...")
        text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', text)
        # 2. 移除带命名空间前缀的属性 (xsi:schemaLocation="..." 等)
        text = re.sub(r'\s\w+:\w+="[^"]*"', '', text)
        # 3. 移除标签上的命名空间前缀 (<ns1:infoTable> -> <infoTable>)
        text = re.sub(r'<(/?)\w+:', r'<\1', text)

        root = ET.fromstring(text)

        for entry in root.findall(".//infoTable"):
            issuer = (entry.findtext("nameOfIssuer") or "").strip()
            cusip = (entry.findtext("cusip") or "").strip()
            value_str = (entry.findtext("value") or "0").strip()
            sh_prn_amount = (entry.findtext(".//sshPrnamt") or "0").strip()
            sh_prn_type = (entry.findtext(".//sshPrnamtType") or "SH").strip()
            put_call = (entry.findtext("putCall") or "").strip()
            title_class = (entry.findtext("titleOfClass") or "").strip()

            try:
                value = int(float(value_str))
                shares = int(float(sh_prn_amount))
            except (ValueError, TypeError):
                continue

            if not issuer or not cusip:
                continue

            holdings.append({
                "issuer": issuer,
                "cusip": cusip,
                "value_usd": value,
                "shares": shares,
                "share_type": sh_prn_type,
                "put_call": put_call,
                "title_class": title_class,
            })
    except Exception as e:
        print(f"   ⚠️ XML 解析失败: {e}")
        return []

    return holdings


def normalize_value(value, report_date):
    """SEC 在 2022 Q4 前用千美元,之后用美元.统一为美元"""
    try:
        rdate = datetime.strptime(report_date, "%Y-%m-%d").date()
    except Exception:
        return value
    # 2022 Q4 及之后用美元 (report_date >= 2022-09-30 的 13F 改用美元)
    if rdate >= date(2022, 9, 30):
        return value
    else:
        return value * 1000


# ============ 数据获取主流程 ============

def fetch_investor_data(key, info):
    """获取单个大师的完整数据"""
    print(f"\n📊 抓取 {info['name_cn']} ({info['fund']})...")

    submissions = get_recent_filings(info["cik"])
    if not submissions:
        print(f"   ❌ 无法获取提交记录")
        return None

    latest = find_latest_13f(submissions)
    if not latest:
        print(f"   ❌ 未找到 13F-HR")
        return None

    print(f"   📅 最新 13F: 报告期 {latest['report_date']}, 提交 {latest['filing_date']}")

    holdings = fetch_13f_holdings(info["cik"], latest)
    if not holdings:
        print(f"   ❌ 持仓数据为空")
        return None

    # 标准化金额 (美元)
    for h in holdings:
        h["value_usd"] = normalize_value(h["value_usd"], latest["report_date"])

    # 按持仓金额排序
    holdings.sort(key=lambda x: x["value_usd"], reverse=True)

    # 计算占比
    total_value = sum(h["value_usd"] for h in holdings)
    for h in holdings:
        h["weight_pct"] = (h["value_usd"] / total_value * 100) if total_value else 0

    print(f"   ✅ {len(holdings)} 只持仓,总市值 ${total_value/1e9:.2f}B")
    if holdings[:3]:
        for h in holdings[:3]:
            print(f"      {h['issuer'][:40]:<40} {h['weight_pct']:.2f}%")

    # SEC API 礼貌限流: 每秒不超过 10 次请求
    time.sleep(0.5)

    return {
        "filing": latest,
        "holdings": holdings,
        "total_value": total_value,
        "info": info,
    }


# ============ 数据对比与变化检测 ============

def compare_holdings(old, new):
    """对比两个季度的持仓变化"""
    if not old or not new:
        return {"new_buys": [], "sold_out": [], "added": [], "reduced": [], "unchanged": []}

    old_map = {h["cusip"]: h for h in old if h.get("cusip")}
    new_map = {h["cusip"]: h for h in new if h.get("cusip")}

    new_buys = []      # 新建仓
    sold_out = []      # 清仓
    added = []         # 加仓
    reduced = []       # 减仓
    unchanged = []     # 不变

    for cusip, h in new_map.items():
        if cusip not in old_map:
            new_buys.append(h)
        else:
            old_h = old_map[cusip]
            change = h["shares"] - old_h["shares"]
            if change > 0:
                pct = (change / old_h["shares"] * 100) if old_h["shares"] else 100
                added.append({**h, "old_shares": old_h["shares"], "change": change, "change_pct": pct})
            elif change < 0:
                pct = (change / old_h["shares"] * 100) if old_h["shares"] else 0
                reduced.append({**h, "old_shares": old_h["shares"], "change": change, "change_pct": pct})
            else:
                unchanged.append(h)

    for cusip, old_h in old_map.items():
        if cusip not in new_map:
            sold_out.append(old_h)

    return {
        "new_buys": new_buys,
        "sold_out": sold_out,
        "added": sorted(added, key=lambda x: x["change_pct"], reverse=True),
        "reduced": sorted(reduced, key=lambda x: x["change_pct"]),
        "unchanged": unchanged,
    }


def find_consensus(all_data):
    """找出多人共识的标的"""
    issuer_holders = {}  # {cusip: [investor_key, ...]}

    for key, data in all_data.items():
        if not data:
            continue
        for h in data["holdings"]:
            cusip = h.get("cusip")
            if not cusip:
                continue
            if cusip not in issuer_holders:
                issuer_holders[cusip] = {
                    "issuer": h["issuer"],
                    "cusip": cusip,
                    "holders": [],
                }
            issuer_holders[cusip]["holders"].append({
                "investor": key,
                "weight_pct": h.get("weight_pct", 0),
                "value_usd": h["value_usd"],
            })

    # 按持有人数排序
    consensus = sorted(
        issuer_holders.values(),
        key=lambda x: (len(x["holders"]), sum(h["value_usd"] for h in x["holders"])),
        reverse=True,
    )

    return consensus


# ============ 状态管理 ============

def load_state():
    if os.path.exists(CONFIG["state_file"]):
        try:
            with open(CONFIG["state_file"], "r") as f:
                return json.load(f)
        except Exception:
            return default_state()
    return default_state()


def default_state():
    return {
        "first_run": datetime.now().isoformat(),
        "last_seen_filings": {},   # {investor_key: accession_number}
        "previous_holdings": {},   # {investor_key: [holdings...]}
        "last_weekly_report": None,
    }


def save_state(state):
    with open(CONFIG["state_file"], "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ============ 邮件渲染 ============

def format_money(value):
    """格式化金额"""
    if value >= 1e9:
        return f"${value/1e9:.2f}B"
    elif value >= 1e6:
        return f"${value/1e6:.1f}M"
    elif value >= 1e3:
        return f"${value/1e3:.0f}K"
    else:
        return f"${value:.0f}"


def render_investor_card(key, data, changes=None):
    """单个大师的卡片"""
    if not data:
        return ""

    info = data["info"]
    holdings = data["holdings"]
    filing = data["filing"]

    # 顶部信息条
    top_html = f"""
    <div style="background: linear-gradient(135deg, {info['color']} 0%, {info['color']}dd 100%); padding: 16px 20px; border-radius: 8px 8px 0 0; color: white;">
        <div style="display: table; width: 100%;">
            <div style="display: table-cell;">
                <div style="font-size: 18px; font-weight: 700;">{info['name_cn']} <span style="font-size: 12px; opacity: 0.9; font-weight: 400;">{info['name']}</span></div>
                <div style="font-size: 11px; opacity: 0.85; margin-top: 4px;">{info['fund']}</div>
            </div>
            <div style="display: table-cell; text-align: right; vertical-align: top;">
                <div style="font-size: 20px; font-weight: 700;">{format_money(data['total_value'])}</div>
                <div style="font-size: 11px; opacity: 0.85;">{len(holdings)} 只持仓</div>
            </div>
        </div>
        <div style="font-size: 10px; opacity: 0.75; margin-top: 6px;">报告期 {filing['report_date']} · 提交 {filing['filing_date']} · {info['note']}</div>
    </div>
    """

    # 持仓 Top 10 表格
    top10_rows = ""
    for i, h in enumerate(holdings[:10], 1):
        # 简化股票名
        name = h["issuer"][:40] + ("..." if len(h["issuer"]) > 40 else "")
        weight = h.get("weight_pct", 0)
        # 进度条
        bar_width = min(weight * 2, 100)
        top10_rows += f"""
        <tr style="border-bottom: 1px solid {COLORS['border']};">
            <td style="padding: 8px 4px; color: {COLORS['text_muted']}; font-size: 11px;">{i}</td>
            <td style="padding: 8px 4px; font-weight: 600; font-size: 13px; color: {COLORS['text']};">{name}</td>
            <td style="padding: 8px 4px; text-align: right; font-size: 12px; color: {COLORS['text_muted']};">{format_money(h['value_usd'])}</td>
            <td style="padding: 8px 4px; text-align: right;">
                <span style="font-weight: 700; color: {info['color']}; font-size: 13px;">{weight:.1f}%</span>
            </td>
            <td style="padding: 4px;">
                <div style="background: {COLORS['bg_hover']}; height: 6px; width: 80px; border-radius: 3px; overflow: hidden;">
                    <div style="width: {bar_width:.1f}%; height: 100%; background: {info['color']};"></div>
                </div>
            </td>
        </tr>
        """

    # 变化部分
    changes_html = ""
    if changes:
        if changes["new_buys"]:
            new_buy_items = "".join(
                f'<span style="display: inline-block; background: #dcfce7; color: #15803d; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin: 2px;">{h["issuer"][:30]}</span>'
                for h in changes["new_buys"][:5]
            )
            changes_html += f"""
            <div style="margin: 8px 0;">
                <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-bottom: 4px;">🟢 新建仓 ({len(changes['new_buys'])})</div>
                <div>{new_buy_items}</div>
            </div>
            """

        if changes["sold_out"]:
            sold_items = "".join(
                f'<span style="display: inline-block; background: #fee2e2; color: #b91c1c; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin: 2px;">{h["issuer"][:30]}</span>'
                for h in changes["sold_out"][:5]
            )
            changes_html += f"""
            <div style="margin: 8px 0;">
                <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-bottom: 4px;">❌ 清仓 ({len(changes['sold_out'])})</div>
                <div>{sold_items}</div>
            </div>
            """

        if changes["added"]:
            top_adds = changes["added"][:3]
            add_items = "".join(
                f'<span style="display: inline-block; background: #fef3c7; color: #a16207; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin: 2px;">{h["issuer"][:25]} +{h["change_pct"]:.0f}%</span>'
                for h in top_adds
            )
            changes_html += f"""
            <div style="margin: 8px 0;">
                <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-bottom: 4px;">📈 加仓 Top ({len(changes['added'])})</div>
                <div>{add_items}</div>
            </div>
            """

        if changes["reduced"]:
            top_reds = changes["reduced"][:3]
            red_items = "".join(
                f'<span style="display: inline-block; background: #fce7f3; color: #9f1239; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin: 2px;">{h["issuer"][:25]} {h["change_pct"]:.0f}%</span>'
                for h in top_reds
            )
            changes_html += f"""
            <div style="margin: 8px 0;">
                <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-bottom: 4px;">📉 减仓 Top ({len(changes['reduced'])})</div>
                <div>{red_items}</div>
            </div>
            """

    if changes_html:
        changes_html = f"""
        <div style="padding: 12px 20px; background: {COLORS['bg_card']}; border-top: 1px solid {COLORS['border']};">
            <div style="font-size: 11px; letter-spacing: 1px; color: {COLORS['text_muted']}; margin-bottom: 6px; font-weight: 600;">本季变化</div>
            {changes_html}
        </div>
        """

    return f"""
    <div style="border: 1px solid {COLORS['border']}; border-radius: 8px; overflow: hidden; margin-bottom: 20px; background: white;">
        {top_html}
        <div style="padding: 0 20px 16px;">
            <table style="width: 100%; border-collapse: collapse; margin-top: 8px;">
                <thead>
                    <tr>
                        <th style="padding: 8px 4px; text-align: left; font-size: 10px; color: {COLORS['text_muted']}; letter-spacing: 1px;">#</th>
                        <th style="padding: 8px 4px; text-align: left; font-size: 10px; color: {COLORS['text_muted']}; letter-spacing: 1px;">标的</th>
                        <th style="padding: 8px 4px; text-align: right; font-size: 10px; color: {COLORS['text_muted']}; letter-spacing: 1px;">市值</th>
                        <th style="padding: 8px 4px; text-align: right; font-size: 10px; color: {COLORS['text_muted']}; letter-spacing: 1px;">占比</th>
                        <th style="padding: 8px 4px;"></th>
                    </tr>
                </thead>
                <tbody>{top10_rows}</tbody>
            </table>
        </div>
        {changes_html}
    </div>
    """


def render_consensus_section(consensus_list, total_investors):
    """共识标的部分"""
    multi_holder = [c for c in consensus_list if len(c["holders"]) >= 2]
    if not multi_holder:
        return ""

    rows = ""
    investor_names = {k: v["name_cn"] for k, v in CONFIG["investors"].items()}
    investor_colors = {k: v["color"] for k, v in CONFIG["investors"].items()}

    for c in multi_holder[:15]:
        n = len(c["holders"])
        # 持有人徽章
        badges = "".join(
            f'<span style="display: inline-block; background: {investor_colors.get(h["investor"], "#888")}; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px; margin: 1px;">{investor_names.get(h["investor"], h["investor"])} {h["weight_pct"]:.1f}%</span>'
            for h in sorted(c["holders"], key=lambda x: x["weight_pct"], reverse=True)
        )

        total_value = sum(h["value_usd"] for h in c["holders"])
        # 共识强度星星
        stars = "★" * min(n, 5) + "☆" * (5 - min(n, 5))

        rows += f"""
        <tr style="border-bottom: 1px solid {COLORS['border']};">
            <td style="padding: 12px 8px;">
                <div style="font-weight: 700; color: {COLORS['text']}; font-size: 14px;">{c['issuer'][:40]}</div>
                <div style="margin-top: 4px;">{badges}</div>
            </td>
            <td style="padding: 12px 8px; text-align: center;">
                <div style="font-size: 18px; font-weight: 700; color: {COLORS['primary']};">{n}/{total_investors}</div>
                <div style="font-size: 11px; color: {COLORS['warning']};">{stars}</div>
            </td>
            <td style="padding: 12px 8px; text-align: right;">
                <div style="font-size: 13px; font-weight: 600;">{format_money(total_value)}</div>
                <div style="font-size: 10px; color: {COLORS['text_muted']};">大师合计持仓</div>
            </td>
        </tr>
        """

    return f"""
    <div style="margin: 24px 0;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {COLORS['primary']}; margin-bottom: 8px; font-weight: 600;">CONSENSUS · 大师共识标的</div>
        <div style="font-size: 12px; color: {COLORS['text_muted']}; margin-bottom: 12px;">≥ 2 位大师共同持有的标的,按持有人数排序</div>
        <table style="width: 100%; border-collapse: collapse; background: white; border: 1px solid {COLORS['border']}; border-radius: 8px; overflow: hidden;">
            <thead>
                <tr style="background: {COLORS['bg_card']};">
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">标的 & 持有人</th>
                    <th style="padding: 10px 8px; text-align: center; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">共识度</th>
                    <th style="padding: 10px 8px; text-align: right; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">合计市值</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """


def render_summary_banner(new_filings_count, all_data, consensus):
    """顶部摘要横幅"""
    if new_filings_count > 0:
        color = COLORS["danger"]
        bg = "#fee2e2"
        title = f"🔔 NEW FILINGS · {new_filings_count} 位大师有新 13F"
        sub = "下方查看最新持仓变化"
    else:
        # 看共识情况
        top_consensus = [c for c in consensus if len(c["holders"]) >= 3]
        if top_consensus:
            color = COLORS["primary"]
            bg = "#cffafe"
            title = f"📊 MASTERS BRIEFING · 大师持仓周报"
            sub = f"{len(top_consensus)} 只标的获 ≥3 位大师共识"
        else:
            color = COLORS["accent"]
            bg = "#d1fae5"
            title = "📊 MASTERS BRIEFING · 大师持仓周报"
            sub = "市场观察期,无新 13F 提交"

    # 大师总市值
    total_aum = sum(d["total_value"] for d in all_data.values() if d)

    return f"""
    <div style="background: {bg}; border-left: 4px solid {color}; padding: 20px; border-radius: 8px; margin-bottom: 24px;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {color}; font-weight: 600;">{title}</div>
        <div style="font-size: 14px; color: {COLORS['text']}; margin-top: 4px;">{sub}</div>
        <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-top: 6px;">5 位大师合计 13F 持仓 {format_money(total_aum)}</div>
    </div>
    """


def render_email_shell(title, subtitle, content):
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    return f"""<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif; background: #f1f5f9; color: {COLORS['text']};">
<div style="max-width: 760px; margin: 0 auto; padding: 24px 16px;">
    <div style="padding: 16px 0 24px 0; border-bottom: 2px solid {COLORS['primary']};">
        <div style="font-size: 11px; letter-spacing: 3px; color: {COLORS['primary']}; font-weight: 700;">SUPERINVESTOR MONITOR</div>
        <div style="display: table; width: 100%; margin-top: 8px;">
            <div style="display: table-cell; vertical-align: bottom;">
                <div style="font-size: 24px; font-weight: 700; color: {COLORS['text']};">{title}</div>
                <div style="font-size: 13px; color: {COLORS['text_muted']}; margin-top: 4px;">{subtitle}</div>
            </div>
            <div style="display: table-cell; vertical-align: bottom; text-align: right;">
                <div style="font-size: 11px; color: {COLORS['text_muted']};">{now}</div>
            </div>
        </div>
    </div>
    {content}
    <div style="margin-top: 32px; padding: 16px 0; border-top: 1px solid {COLORS['border']}; text-align: center;">
        <div style="font-size: 11px; color: {COLORS['text_muted']};">基于 SEC EDGAR 13F 数据 · 巴菲特/段永平/李录/Howard Marks/Mohnish Pabrai</div>
        <div style="font-size: 10px; color: {COLORS['text_muted']}; margin-top: 4px;">13F 数据有 45 天滞后 · 仅供学习,不构成投资建议</div>
    </div>
</div></body></html>"""


def render_next_due_date():
    """下一次 13F 公布日期"""
    today = date.today()
    upcoming = []
    for q, m, d in CONFIG["13f_due_dates"]:
        due = date(today.year, m, d)
        if due < today:
            due = date(today.year + 1, m, d)
        days_until = (due - today).days
        upcoming.append((q, due, days_until))
    upcoming.sort(key=lambda x: x[2])

    if upcoming:
        q, due, days = upcoming[0]
        return f"""
        <div style="background: {COLORS['bg_card']}; padding: 12px 16px; border-radius: 6px; margin: 16px 0; font-size: 12px; color: {COLORS['text_muted']};">
            📅 下次 13F 公布: <b style="color: {COLORS['primary']};">{due.strftime('%Y-%m-%d')}</b> ({q}) · 还有 <b>{days}</b> 天
        </div>
        """
    return ""


# ============ 邮件构建 ============

def build_full_report_email(all_data, state, new_filings):
    """主报告邮件"""
    consensus = find_consensus(all_data)

    # 标题
    today = datetime.now()
    if new_filings:
        names = [CONFIG["investors"][k]["name_cn"] for k in new_filings]
        subject = f"🔔 大师 13F 更新 · {', '.join(names)}"
        title = "⚡ 新 13F 提交"
        subtitle = f"{len(new_filings)} 位大师发布最新持仓"
    else:
        subject = f"📊 大师持仓周报 · {today.strftime('%Y-%m-%d')}"
        title = "📊 大师持仓周报"
        subtitle = f"5 位价值投资大师全景扫描 · 第 {today.isocalendar()[1]} 周"

    # 内容拼接
    content = render_summary_banner(len(new_filings), all_data, consensus)
    content += render_next_due_date()
    content += render_consensus_section(consensus, len(all_data))

    # 每位大师的卡片
    content += f'<div style="font-size: 11px; letter-spacing: 2px; color: {COLORS["primary"]}; margin: 24px 0 12px; font-weight: 600;">PORTFOLIOS · 各大师持仓详情</div>'

    for key, data in all_data.items():
        if not data:
            continue
        # 取上次持仓
        prev = state["previous_holdings"].get(key)
        changes = compare_holdings(prev, data["holdings"]) if prev else None
        # 只有当本季是新 13F 时才显示变化
        if key not in new_filings:
            changes = None
        content += render_investor_card(key, data, changes)

    html = render_email_shell(title, subtitle, content)
    return subject, html


# ============ 邮件发送 ============

def send_email(subject, html):
    cfg = CONFIG["email"]
    if not cfg["password"]:
        print("❌ 未设置 SMTP_PASSWORD")
        return False
    msg = MIMEMultipart("alternative")
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if cfg["smtp_port"] == 465:
            server = smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"])
        else:
            server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
            server.starttls()
        server.login(cfg["sender"], cfg["password"])
        server.send_message(msg)
        server.quit()
        print(f"✅ 邮件已发送: {subject}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


# ============ 主流程 ============

def is_weekly_report_day(state):
    today = datetime.now()
    if today.weekday() != CONFIG["weekly_report_day"]:
        return False
    last = state.get("last_weekly_report")
    if not last:
        return True
    last_time = datetime.fromisoformat(last)
    return (today - last_time).days >= 6


def main():
    print(f"\n{'='*60}")
    print(f"🚀 大师持仓监控运行: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    state = load_state()

    # 1. 获取每位大师数据
    all_data = {}
    new_filings = []  # 本次新提交的 13F

    for key, info in CONFIG["investors"].items():
        data = fetch_investor_data(key, info)
        if data:
            all_data[key] = data
            # 检查是否是新的 13F
            last_acc = state["last_seen_filings"].get(key)
            current_acc = data["filing"]["accession"]
            if last_acc != current_acc:
                new_filings.append(key)
                state["last_seen_filings"][key] = current_acc

    if not all_data:
        print("\n❌ 所有大师数据获取失败,退出")
        return

    # 2. 决定是否发邮件
    should_send = False
    reason = ""

    if new_filings:
        should_send = True
        reason = f"{len(new_filings)} 位大师有新 13F"
    elif is_weekly_report_day(state):
        should_send = True
        reason = "周一周报"
    else:
        print("\n📭 无新 13F,非周一,跳过邮件")

    # 3. 发送邮件
    if should_send:
        print(f"\n📧 准备发送邮件 ({reason})...")
        subject, html = build_full_report_email(all_data, state, new_filings)
        if send_email(subject, html):
            if not new_filings:
                state["last_weekly_report"] = datetime.now().isoformat()

    # 4. 更新前次持仓 (用于下次对比)
    for key, data in all_data.items():
        state["previous_holdings"][key] = data["holdings"]

    save_state(state)
    print(f"\n{'='*60}\n✅ 运行结束\n{'='*60}\n")


if __name__ == "__main__":
    main()
