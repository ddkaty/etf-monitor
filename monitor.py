"""
ETF 回调监控脚本 v3.0 - 量化数据丰富版
风格：现代清爽蓝绿商务风
特色：VIX/Sparkline/RSI/距下一档/事件日历/资金流向

监控标的：SOXX, SMH, AMD, AVGO, TSM, MU
触发阈值：-8% / -15% / -25%
"""

import os
import smtplib
import json
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import yfinance as yf
import pandas as pd
import numpy as np

# ============ 配置区 ============
CONFIG = {
    "tickers": {
        "SOXX": {"name": "iShares 半导体 ETF", "type": "ETF", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
        "SMH":  {"name": "VanEck 半导体 ETF", "type": "ETF", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
        "AMD":  {"name": "超威半导体", "type": "STOCK", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
        "AVGO": {"name": "博通", "type": "STOCK", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
        "TSM":  {"name": "台积电", "type": "STOCK", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
        "MU":   {"name": "美光科技", "type": "STOCK", "lookback_days": 60, "alert_levels": [0.08, 0.15, 0.25]},
    },

    # 市场基准指标
    "benchmarks": {
        "^VIX":  {"name": "VIX 恐慌指数", "label": "VIX"},
        "^GSPC": {"name": "S&P 500", "label": "SP500"},
        "QQQ":   {"name": "纳指 100 ETF", "label": "QQQ"},
        "^SOX":  {"name": "费城半导体指数", "label": "SOX"},
    },

    # 关键事件日历（手动维护，每月可更新）
    # 格式: 日期, 类型, 描述
    "events": [
        ("2026-05-13", "数据", "美国 4月CPI 数据"),
        ("2026-05-15", "数据", "美国 4月零售销售"),
        ("2026-05-21", "财报", "NVDA 英伟达 Q1 财报（盘后）"),
        ("2026-05-22", "会议", "FOMC 会议纪要发布"),
        ("2026-05-28", "财报", "MRVL Marvell 财报（盘后）"),
        ("2026-06-04", "财报", "AVGO 博通 Q2 财报"),
        ("2026-06-11", "数据", "美国 5月CPI 数据"),
        ("2026-06-12", "会议", "FOMC 利率决议"),
        ("2026-06-19", "财报", "MU 美光 财报（盘后）"),
        ("2026-07-17", "财报", "TSM 台积电 Q2 财报"),
    ],

    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender": "qixin202401@gmail.com",       # ⚠️ 改成你的 Gmail
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "recipient": "qixin202401@gmail.com",    # ⚠️ 改成你的 Gmail
    },

    "cooldown_hours": 24,
    "weekly_report_day": 0,  # 周一
    "force_buy_reminder_days": 180,
    "state_file": "alert_state.json",
}

# ============ 数据工具函数 ============

def get_history(ticker, days=90):
    """获取历史数据"""
    try:
        end = datetime.now()
        start = end - timedelta(days=days + 30)
        stock = yf.Ticker(ticker)
        hist = stock.history(start=start, end=end, interval="1d")
        return hist if not hist.empty else None
    except Exception as e:
        print(f"  ⚠️ {ticker} 数据获取失败: {e}")
        return None


def calc_rsi(prices, period=14):
    """计算 RSI"""
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def make_sparkline(prices, width=20):
    """生成 sparkline 字符串（Unicode 块字符）"""
    if prices is None or len(prices) < 2:
        return "─" * width
    blocks = "▁▂▃▄▅▆▇█"
    prices = np.array(prices)
    # 重采样到 width 个点
    if len(prices) > width:
        idx = np.linspace(0, len(prices) - 1, width).astype(int)
        prices = prices[idx]
    lo, hi = prices.min(), prices.max()
    if hi == lo:
        return blocks[3] * len(prices)
    norm = (prices - lo) / (hi - lo)
    chars = [blocks[min(int(v * 8), 7)] for v in norm]
    return "".join(chars)


def detect_trend(prices):
    """简单趋势检测"""
    if prices is None or len(prices) < 10:
        return "─", "未知"
    recent = prices[-10:]
    older = prices[-30:-10] if len(prices) >= 30 else prices[:-10]
    if older.mean() == 0:
        return "─", "未知"
    change = (recent.mean() - older.mean()) / older.mean()
    if change > 0.03:
        return "↗", "上升"
    elif change < -0.03:
        return "↘", "下降"
    else:
        return "→", "盘整"


def calc_volume_anomaly(hist):
    """计算成交量异常度"""
    if hist is None or len(hist) < 30 or "Volume" not in hist.columns:
        return None
    recent_5 = hist["Volume"].tail(5).mean()
    avg_30 = hist["Volume"].tail(30).mean()
    if avg_30 == 0:
        return None
    return (recent_5 - avg_30) / avg_30 * 100


# ============ 主要分析函数 ============

def analyze_ticker(ticker, config):
    """分析单个标的，返回完整指标"""
    hist = get_history(ticker, days=90)
    if hist is None or hist.empty:
        return None

    prices = hist["Close"].values
    current = prices[-1]
    prev = prices[-2] if len(prices) > 1 else current
    day_change_pct = (current - prev) / prev * 100

    # 60日回调
    recent = hist.tail(config["lookback_days"])
    peak = recent["High"].max()
    peak_date = recent["High"].idxmax().strftime("%Y-%m-%d")
    pullback_pct = (peak - current) / peak

    # 触发档位
    triggered_level = None
    for level in sorted(config["alert_levels"], reverse=True):
        if pullback_pct >= level:
            triggered_level = level
            break

    # 计算到各档位的距离
    distance_to_levels = []
    for level in config["alert_levels"]:
        if pullback_pct >= level:
            distance_to_levels.append({"level": level, "triggered": True, "distance_pct": 0, "trigger_price": peak * (1 - level)})
        else:
            target_price = peak * (1 - level)
            distance_pct = (current - target_price) / current
            distance_to_levels.append({
                "level": level, "triggered": False,
                "distance_pct": distance_pct,
                "trigger_price": target_price,
            })

    # 技术指标
    rsi = calc_rsi(prices, period=14)
    sparkline = make_sparkline(prices[-60:], width=24)
    trend_arrow, trend_label = detect_trend(prices)
    volume_anomaly = calc_volume_anomaly(hist)

    # 30日累计涨幅
    if len(prices) >= 30:
        return_30d = (current - prices[-30]) / prices[-30] * 100
    else:
        return_30d = None

    return {
        "ticker": ticker,
        "name": config["name"],
        "type": config["type"],
        "current": current,
        "day_change_pct": day_change_pct,
        "peak": peak,
        "peak_date": peak_date,
        "pullback_pct": pullback_pct,
        "triggered_level": triggered_level,
        "distance_to_levels": distance_to_levels,
        "alert_levels": config["alert_levels"],
        "rsi": rsi,
        "sparkline": sparkline,
        "trend_arrow": trend_arrow,
        "trend_label": trend_label,
        "volume_anomaly": volume_anomaly,
        "return_30d": return_30d,
    }


def analyze_benchmarks():
    """分析市场基准指标"""
    results = {}
    for symbol, info in CONFIG["benchmarks"].items():
        hist = get_history(symbol, days=30)
        if hist is None or hist.empty:
            results[info["label"]] = None
            continue
        prices = hist["Close"].values
        current = prices[-1]
        prev = prices[-2] if len(prices) > 1 else current
        day_chg = (current - prev) / prev * 100
        return_5d = (current - prices[-5]) / prices[-5] * 100 if len(prices) >= 5 else None
        results[info["label"]] = {
            "name": info["name"],
            "current": current,
            "day_change_pct": day_chg,
            "return_5d": return_5d,
            "sparkline": make_sparkline(prices[-20:], width=15),
        }
    return results


def get_upcoming_events(days_ahead=14):
    """获取未来 N 天内的事件"""
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    upcoming = []
    for event_str, event_type, desc in CONFIG["events"]:
        try:
            event_date = datetime.strptime(event_str, "%Y-%m-%d").date()
            if today <= event_date <= end_date:
                weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][event_date.weekday()]
                upcoming.append({
                    "date": event_date,
                    "date_str": event_date.strftime("%m-%d"),
                    "weekday": weekday,
                    "type": event_type,
                    "desc": desc,
                })
        except ValueError:
            continue
    return sorted(upcoming, key=lambda x: x["date"])


# ============ 状态管理 ============

def load_state():
    if os.path.exists(CONFIG["state_file"]):
        with open(CONFIG["state_file"], "r") as f:
            return json.load(f)
    return {"last_alerts": {}, "last_weekly_report": None, "first_run_date": datetime.now().isoformat()}


def save_state(state):
    with open(CONFIG["state_file"], "w") as f:
        json.dump(state, f, indent=2)


def should_send_alert(state, ticker, triggered_level):
    if triggered_level is None:
        return False
    key = f"{ticker}_{triggered_level}"
    last_sent = state["last_alerts"].get(key)
    if not last_sent:
        return True
    last_time = datetime.fromisoformat(last_sent)
    return datetime.now() - last_time > timedelta(hours=CONFIG["cooldown_hours"])


def is_weekly_report_day(state):
    today = datetime.now()
    if today.weekday() != CONFIG["weekly_report_day"]:
        return False
    last_report = state.get("last_weekly_report")
    if not last_report:
        return True
    last_time = datetime.fromisoformat(last_report)
    return (today - last_time).days >= 6


def check_force_buy_reminder(state):
    last_alerts = state.get("last_alerts", {})
    if not last_alerts:
        first_run = state.get("first_run_date")
        if not first_run:
            return False
        last_time = datetime.fromisoformat(first_run)
    else:
        last_times = [datetime.fromisoformat(t) for t in last_alerts.values()]
        last_time = max(last_times)
    return (datetime.now() - last_time).days >= CONFIG["force_buy_reminder_days"]


# ============ 邮件渲染 ============

# 配色 - 现代清爽蓝绿风
COLORS = {
    "primary": "#0891b2",      # 主色 - 青蓝
    "primary_dark": "#0e7490",
    "accent": "#10b981",       # 强调 - 翠绿
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


def render_market_pulse(benchmarks):
    """市场温度卡片"""
    if not benchmarks:
        return ""

    vix = benchmarks.get("VIX")
    sp500 = benchmarks.get("SP500")
    qqq = benchmarks.get("QQQ")
    sox = benchmarks.get("SOX")

    # VIX 情绪判断
    vix_status = "—"
    vix_color = COLORS["neutral"]
    vix_value_text = "—"
    if vix:
        v = vix["current"]
        vix_value_text = f"{v:.1f}"
        if v < 15:
            vix_status, vix_color = "极度平静", "#3b82f6"
        elif v < 20:
            vix_status, vix_color = "平静", COLORS["success"]
        elif v < 25:
            vix_status, vix_color = "警觉", COLORS["warning"]
        elif v < 35:
            vix_status, vix_color = "紧张", "#f97316"
        else:
            vix_status, vix_color = "恐慌", COLORS["danger"]

    def fmt_bench(b, show_value=True):
        if not b:
            return "—", COLORS["neutral"]
        chg = b["day_change_pct"]
        color = COLORS["success"] if chg >= 0 else COLORS["danger"]
        sign = "+" if chg >= 0 else ""
        text = f"{sign}{chg:.2f}%"
        return text, color

    sp_text, sp_color = fmt_bench(sp500)
    qqq_text, qqq_color = fmt_bench(qqq)
    sox_text, sox_color = fmt_bench(sox)

    return f"""
    <div style="background: linear-gradient(135deg, #0891b2 0%, #0e7490 100%); padding: 24px; border-radius: 12px; margin-bottom: 24px; color: white;">
        <div style="font-size: 11px; letter-spacing: 2px; opacity: 0.8; margin-bottom: 4px;">MARKET PULSE · 市场温度</div>
        <div style="display: table; width: 100%; margin-top: 16px;">
            <div style="display: table-row;">
                <div style="display: table-cell; padding-right: 16px;">
                    <div style="font-size: 11px; opacity: 0.7; letter-spacing: 1px;">VIX 恐慌指数</div>
                    <div style="font-size: 26px; font-weight: 700; margin-top: 4px;">{vix_value_text}</div>
                    <div style="font-size: 12px; background: {vix_color}; padding: 2px 8px; border-radius: 4px; display: inline-block; margin-top: 4px;">{vix_status}</div>
                </div>
                <div style="display: table-cell; padding-right: 16px;">
                    <div style="font-size: 11px; opacity: 0.7; letter-spacing: 1px;">S&P 500</div>
                    <div style="font-size: 22px; font-weight: 700; margin-top: 4px; color: {sp_color};">{sp_text}</div>
                    <div style="font-size: 11px; opacity: 0.7; margin-top: 4px;">{sp500["sparkline"] if sp500 else ""}</div>
                </div>
                <div style="display: table-cell; padding-right: 16px;">
                    <div style="font-size: 11px; opacity: 0.7; letter-spacing: 1px;">QQQ 纳指</div>
                    <div style="font-size: 22px; font-weight: 700; margin-top: 4px; color: {qqq_color};">{qqq_text}</div>
                    <div style="font-size: 11px; opacity: 0.7; margin-top: 4px;">{qqq["sparkline"] if qqq else ""}</div>
                </div>
                <div style="display: table-cell;">
                    <div style="font-size: 11px; opacity: 0.7; letter-spacing: 1px;">SOX 半导体</div>
                    <div style="font-size: 22px; font-weight: 700; margin-top: 4px; color: {sox_color};">{sox_text}</div>
                    <div style="font-size: 11px; opacity: 0.7; margin-top: 4px;">{sox["sparkline"] if sox else ""}</div>
                </div>
            </div>
        </div>
    </div>
    """


def render_watchlist_table(results):
    """主标的清单表格"""
    rows = ""
    for r in results:
        if r is None:
            continue

        # 回调颜色
        pct = r["pullback_pct"] * 100
        if pct >= 25:
            pb_color = COLORS["success"]
            status = '<span style="background:#dcfce7; color:#15803d; padding:2px 8px; border-radius:4px; font-size:11px;">深度回调</span>'
        elif pct >= 15:
            pb_color = COLORS["warning"]
            status = '<span style="background:#fef3c7; color:#a16207; padding:2px 8px; border-radius:4px; font-size:11px;">明显回调</span>'
        elif pct >= 8:
            pb_color = "#f97316"
            status = '<span style="background:#ffedd5; color:#c2410c; padding:2px 8px; border-radius:4px; font-size:11px;">健康回调</span>'
        else:
            pb_color = COLORS["text_muted"]
            status = '<span style="background:#f1f5f9; color:#475569; padding:2px 8px; border-radius:4px; font-size:11px;">未达档位</span>'

        # 日变化
        day_chg = r["day_change_pct"]
        day_color = COLORS["success"] if day_chg >= 0 else COLORS["danger"]
        day_sign = "+" if day_chg >= 0 else ""

        # RSI
        rsi = r["rsi"]
        if rsi:
            if rsi > 70:
                rsi_color = COLORS["danger"]
                rsi_label = "🔥超买"
            elif rsi < 30:
                rsi_color = COLORS["success"]
                rsi_label = "❄️超卖"
            else:
                rsi_color = COLORS["neutral"]
                rsi_label = "中性"
            rsi_text = f'<span style="color:{rsi_color}; font-weight:600;">{rsi:.0f}</span> <span style="color:{COLORS["text_muted"]}; font-size:11px;">{rsi_label}</span>'
        else:
            rsi_text = "—"

        # 30日回报
        ret30 = r["return_30d"]
        if ret30 is not None:
            ret30_color = COLORS["success"] if ret30 >= 0 else COLORS["danger"]
            ret30_sign = "+" if ret30 >= 0 else ""
            ret30_text = f'<span style="color:{ret30_color};">{ret30_sign}{ret30:.1f}%</span>'
        else:
            ret30_text = "—"

        rows += f"""
        <tr style="border-bottom: 1px solid {COLORS['border']};">
            <td style="padding: 12px 8px;">
                <div style="font-weight: 700; color: {COLORS['text']}; font-size: 14px;">{r['ticker']}</div>
                <div style="font-size: 11px; color: {COLORS['text_muted']};">{r['name']}</div>
            </td>
            <td style="padding: 12px 8px; text-align: right;">
                <div style="font-weight: 600; font-size: 14px;">${r['current']:.2f}</div>
                <div style="font-size: 11px; color: {day_color};">{day_sign}{day_chg:.2f}%</div>
            </td>
            <td style="padding: 12px 8px; font-family: monospace; font-size: 13px; color: {COLORS['primary']}; letter-spacing: -1px;">
                {r['sparkline']}
                <div style="font-size: 10px; color: {COLORS['text_muted']}; margin-top: 2px;">{r['trend_arrow']} {r['trend_label']}</div>
            </td>
            <td style="padding: 12px 8px; text-align: right;">
                <span style="color: {pb_color}; font-weight: 700; font-size: 15px;">-{pct:.1f}%</span>
                <div style="margin-top: 4px;">{status}</div>
            </td>
            <td style="padding: 12px 8px; text-align: center;">{rsi_text}</td>
            <td style="padding: 12px 8px; text-align: right;">{ret30_text}</td>
        </tr>
        """

    return f"""
    <div style="margin: 24px 0;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {COLORS['primary']}; margin-bottom: 8px; font-weight: 600;">WATCHLIST · 观察清单</div>
        <table style="width: 100%; border-collapse: collapse; background: white; border: 1px solid {COLORS['border']}; border-radius: 8px; overflow: hidden;">
            <thead>
                <tr style="background: {COLORS['bg_card']}; border-bottom: 2px solid {COLORS['border']};">
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">标的</th>
                    <th style="padding: 10px 8px; text-align: right; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">现价/日变</th>
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">60日走势</th>
                    <th style="padding: 10px 8px; text-align: right; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">回调幅度</th>
                    <th style="padding: 10px 8px; text-align: center; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">RSI</th>
                    <th style="padding: 10px 8px; text-align: right; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">30日回报</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
    """


def render_next_targets(results):
    """距下一档位差多少"""
    # 找出最接近触发的（按距第一档的距离排序）
    candidates = []
    for r in results:
        if r is None or r["triggered_level"]:
            continue
        for d in r["distance_to_levels"]:
            if not d["triggered"]:
                candidates.append({
                    "ticker": r["ticker"],
                    "name": r["name"],
                    "current": r["current"],
                    "level": d["level"],
                    "trigger_price": d["trigger_price"],
                    "distance_pct": d["distance_pct"],
                    "pullback_pct": r["pullback_pct"],
                })
                break

    if not candidates:
        return ""

    # 按距离排序
    candidates.sort(key=lambda x: x["distance_pct"])
    top3 = candidates[:3]

    rows = ""
    for c in top3:
        level_pct = c["level"] * 100
        dist_pct = c["distance_pct"] * 100
        price_drop = c["current"] - c["trigger_price"]

        # 进度条
        progress = (c["pullback_pct"] / c["level"]) * 100
        progress = min(progress, 100)

        rows += f"""
        <div style="background: white; padding: 16px; margin: 8px 0; border-radius: 8px; border: 1px solid {COLORS['border']};">
            <div style="display: table; width: 100%;">
                <div style="display: table-cell; vertical-align: middle;">
                    <div style="font-weight: 700; font-size: 14px; color: {COLORS['text']};">{c['ticker']}</div>
                    <div style="font-size: 11px; color: {COLORS['text_muted']};">{c['name']}</div>
                </div>
                <div style="display: table-cell; vertical-align: middle; text-align: right;">
                    <div style="font-size: 12px; color: {COLORS['text_muted']};">距第一档 -{level_pct:.0f}%</div>
                    <div style="font-size: 16px; font-weight: 700; color: {COLORS['primary']};">还需跌 {dist_pct:.1f}%</div>
                    <div style="font-size: 11px; color: {COLORS['text_muted']};">即 ${price_drop:.2f}（触发价 ${c['trigger_price']:.2f}）</div>
                </div>
            </div>
            <div style="margin-top: 12px; background: {COLORS['bg_hover']}; height: 6px; border-radius: 3px; overflow: hidden;">
                <div style="width: {progress:.1f}%; height: 100%; background: linear-gradient(90deg, {COLORS['accent']}, {COLORS['warning']});"></div>
            </div>
            <div style="margin-top: 4px; font-size: 10px; color: {COLORS['text_muted']};">当前回调 {c['pullback_pct']*100:.1f}% / 触发档 {level_pct:.0f}%</div>
        </div>
        """

    return f"""
    <div style="margin: 24px 0;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {COLORS['primary']}; margin-bottom: 8px; font-weight: 600;">NEXT TARGETS · 最接近触发</div>
        {rows}
    </div>
    """


def render_events(events):
    """关键事件日历"""
    if not events:
        return ""

    rows = ""
    for e in events:
        type_colors = {
            "财报": ("#ddd6fe", "#6d28d9"),
            "数据": ("#fef3c7", "#a16207"),
            "会议": ("#fee2e2", "#b91c1c"),
        }
        bg, fg = type_colors.get(e["type"], ("#f1f5f9", "#475569"))

        rows += f"""
        <tr style="border-bottom: 1px solid {COLORS['border']};">
            <td style="padding: 10px 8px; font-weight: 600; color: {COLORS['text']};">{e['date_str']}</td>
            <td style="padding: 10px 8px; color: {COLORS['text_muted']}; font-size: 12px;">{e['weekday']}</td>
            <td style="padding: 10px 8px;">
                <span style="background: {bg}; color: {fg}; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;">{e['type']}</span>
            </td>
            <td style="padding: 10px 8px; font-size: 13px; color: {COLORS['text']};">{e['desc']}</td>
        </tr>
        """

    return f"""
    <div style="margin: 24px 0;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {COLORS['primary']}; margin-bottom: 8px; font-weight: 600;">UPCOMING CATALYSTS · 未来 14 天事件</div>
        <table style="width: 100%; border-collapse: collapse; background: white; border: 1px solid {COLORS['border']}; border-radius: 8px; overflow: hidden;">
            <thead>
                <tr style="background: {COLORS['bg_card']};">
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">日期</th>
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">星期</th>
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">类型</th>
                    <th style="padding: 10px 8px; text-align: left; font-size: 11px; color: {COLORS['text_muted']}; letter-spacing: 1px;">事件</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """


def render_volume_anomaly(results):
    """资金流向异常"""
    anomalies = []
    for r in results:
        if r is None or r["volume_anomaly"] is None:
            continue
        if abs(r["volume_anomaly"]) > 30:  # 超过 30% 算异常
            anomalies.append(r)

    if not anomalies:
        return ""

    anomalies.sort(key=lambda x: abs(x["volume_anomaly"]), reverse=True)

    rows = ""
    for r in anomalies[:5]:
        vol = r["volume_anomaly"]
        if vol > 0:
            color = COLORS["success"]
            arrow = "↑"
            label = "资金流入"
        else:
            color = COLORS["danger"]
            arrow = "↓"
            label = "资金流出"

        rows += f"""
        <div style="display: inline-block; background: white; padding: 12px 16px; margin: 4px; border-radius: 8px; border: 1px solid {COLORS['border']};">
            <div style="font-weight: 700; color: {COLORS['text']};">{r['ticker']}</div>
            <div style="font-size: 18px; font-weight: 700; color: {color};">{arrow} {abs(vol):.0f}%</div>
            <div style="font-size: 11px; color: {COLORS['text_muted']};">{label}</div>
        </div>
        """

    return f"""
    <div style="margin: 24px 0;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {COLORS['primary']}; margin-bottom: 8px; font-weight: 600;">VOLUME ANOMALY · 资金流向异常</div>
        <div style="font-size: 11px; color: {COLORS['text_muted']}; margin-bottom: 8px;">近5日成交量 vs 近30日均量偏离 > 30%</div>
        <div>{rows}</div>
    </div>
    """


def render_summary_banner(results, alerts_to_send=None):
    """顶部今日指令横幅"""
    if alerts_to_send:
        count = len(alerts_to_send)
        max_level = max(a["triggered_level"] for a in alerts_to_send)
        if max_level >= 0.25:
            color, bg = COLORS["danger"], "#fee2e2"
            action = "AGGRESSIVE BUY · 进攻建仓"
            sub = f"{count} 个标的触发深度回调，执行第三档买入"
        elif max_level >= 0.15:
            color, bg = "#ea580c", "#ffedd5"
            action = "PARTIAL BUY · 部分建仓"
            sub = f"{count} 个标的触发明显回调，执行第二档买入"
        else:
            color, bg = COLORS["warning"], "#fef3c7"
            action = "FIRST ENTRY · 首档建仓"
            sub = f"{count} 个标的触发健康回调，执行第一档买入"
    else:
        # 看接近度
        min_dist = float('inf')
        for r in results:
            if r and not r["triggered_level"]:
                for d in r["distance_to_levels"]:
                    if not d["triggered"]:
                        min_dist = min(min_dist, d["distance_pct"])
                        break

        if min_dist < 0.02:
            color, bg = "#0891b2", "#cffafe"
            action = "WATCH CLOSELY · 密切关注"
            sub = "有标的接近触发档位，建议手动盯盘"
        else:
            color, bg = COLORS["success"], "#d1fae5"
            action = "HOLD · 继续等待"
            sub = "市场强势，所有标的未触发，保持观望"

    return f"""
    <div style="background: {bg}; border-left: 4px solid {color}; padding: 20px; border-radius: 8px; margin-bottom: 24px;">
        <div style="font-size: 11px; letter-spacing: 2px; color: {color}; font-weight: 600;">TODAY'S ACTION · 今日指令</div>
        <div style="font-size: 20px; font-weight: 700; color: {color}; margin-top: 4px;">{action}</div>
        <div style="font-size: 13px; color: {COLORS['text']}; margin-top: 4px;">{sub}</div>
    </div>
    """


def render_email_shell(title, subtitle, content):
    """邮件外壳"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif; background: #f1f5f9; color: {COLORS['text']};">
        <div style="max-width: 720px; margin: 0 auto; padding: 24px 16px;">
            <!-- Header -->
            <div style="padding: 16px 0 24px 0; border-bottom: 2px solid {COLORS['primary']};">
                <div style="font-size: 11px; letter-spacing: 3px; color: {COLORS['primary']}; font-weight: 700;">ETF MONITOR</div>
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

            <!-- Content -->
            {content}

            <!-- Footer -->
            <div style="margin-top: 32px; padding: 16px 0; border-top: 1px solid {COLORS['border']}; text-align: center;">
                <div style="font-size: 11px; color: {COLORS['text_muted']};">
                    自动监控系统 · 每个工作日运行 · 基于 yfinance 数据
                </div>
                <div style="font-size: 10px; color: {COLORS['text_muted']}; margin-top: 4px;">
                    本报表仅供参考，不构成投资建议
                </div>
            </div>
        </div>
    </body>
    </html>
    """


def format_alert_email(alerts, all_results, benchmarks, events):
    """触发警报邮件"""
    tickers = [a["ticker"] for a in alerts]
    subject = f"🔔 回调触发 · {', '.join(tickers)}"

    # 触发详情卡片
    alert_cards = ""
    for a in alerts:
        level_pct = a["triggered_level"] * 100
        pullback_pct = a["pullback_pct"] * 100
        if level_pct >= 25:
            tier, advice = "第三档", "买入 30% 仓位（深度回调机会）"
            color = COLORS["success"]
        elif level_pct >= 15:
            tier, advice = "第二档", "买入 40% 仓位（明显回调）"
            color = COLORS["warning"]
        else:
            tier, advice = "第一档", "买入 30% 仓位（健康回调）"
            color = "#ea580c"

        alert_cards += f"""
        <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid {color}; margin: 12px 0;">
            <div style="display: table; width: 100%;">
                <div style="display: table-cell;">
                    <div style="font-size: 18px; font-weight: 700;">{a['ticker']} <span style="font-size: 13px; color: {COLORS['text_muted']}; font-weight: 400;">{a['name']}</span></div>
                    <div style="margin-top: 8px; font-size: 13px;">
                        <span style="color: {COLORS['text_muted']};">当前价</span> <b>${a['current']:.2f}</b> &nbsp;|&nbsp;
                        <span style="color: {COLORS['text_muted']};">60日高</span> ${a['peak']:.2f}
                    </div>
                </div>
                <div style="display: table-cell; text-align: right; vertical-align: top;">
                    <div style="font-size: 28px; font-weight: 700; color: {color};">-{pullback_pct:.1f}%</div>
                    <div style="font-size: 12px; color: {COLORS['text_muted']};">触发 -{level_pct:.0f}% 档位</div>
                </div>
            </div>
            <div style="margin-top: 12px; padding: 12px; background: {COLORS['bg_card']}; border-radius: 6px;">
                <span style="font-weight: 600; color: {color};">{tier} 建议</span> <span style="color: {COLORS['text']};">· {advice}</span>
            </div>
        </div>
        """

    content = (
        render_summary_banner(all_results, alerts) +
        render_market_pulse(benchmarks) +
        f'<div style="font-size: 11px; letter-spacing: 2px; color: {COLORS["primary"]}; margin-bottom: 8px; font-weight: 600;">ALERTS · 触发详情</div>' +
        alert_cards +
        render_watchlist_table(all_results) +
        render_volume_anomaly(all_results) +
        render_events(events)
    )

    return subject, render_email_shell(
        title=f"⚡ 回调警报",
        subtitle=f"{len(alerts)} 个标的触发买入信号",
        content=content,
    )


def format_weekly_report(all_results, benchmarks, events):
    """周报邮件"""
    today = datetime.now()
    subject = f"📊 周报 · {today.strftime('%Y-%m-%d')}"

    triggered = sum(1 for r in all_results if r and r["triggered_level"])

    content = (
        render_summary_banner(all_results, None) +
        render_market_pulse(benchmarks) +
        render_watchlist_table(all_results) +
        render_next_targets(all_results) +
        render_volume_anomaly(all_results) +
        render_events(events)
    )

    return subject, render_email_shell(
        title="📊 每周量化报表",
        subtitle=f"AI 硬件赛道全景扫描 · 第 {today.isocalendar()[1]} 周",
        content=content,
    )


def format_force_buy_reminder(all_results, benchmarks, events, days_since):
    """6个月强制建仓提醒"""
    subject = f"⏰ 强制建仓提醒 · 已等待 {days_since} 天"

    banner = f"""
    <div style="background: linear-gradient(135deg, #7c3aed, #6d28d9); padding: 24px; border-radius: 12px; margin-bottom: 24px; color: white;">
        <div style="font-size: 11px; letter-spacing: 2px; opacity: 0.9; font-weight: 600;">FORCE BUY REMINDER · 强制建仓提醒</div>
        <div style="font-size: 24px; font-weight: 700; margin-top: 8px;">已 {days_since} 天无警报</div>
        <div style="font-size: 14px; opacity: 0.9; margin-top: 8px;">按方案 A 纪律，建议建仓 15% 仓位防止踏空</div>
    </div>
    """

    content = (
        banner +
        render_market_pulse(benchmarks) +
        render_watchlist_table(all_results) +
        render_next_targets(all_results) +
        render_events(events)
    )

    return subject, render_email_shell(
        title="⏰ 防踏空保险",
        subtitle="6 个月未触发，启动强制建仓机制",
        content=content,
    )


# ============ 邮件发送 ============

def send_email(subject, html_content):
    cfg = CONFIG["email"]
    if not cfg["password"]:
        print("❌ 未设置 SMTP_PASSWORD")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html", "utf-8"))

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

def main():
    print(f"\n{'='*60}")
    print(f"🚀 ETF 量化监控运行: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    state = load_state()

    # 1. 拉取所有数据
    print("📊 分析市场基准...")
    benchmarks = analyze_benchmarks()
    for label, b in benchmarks.items():
        if b:
            print(f"   {label}: {b['current']:.2f} ({b['day_change_pct']:+.2f}%)")

    print("\n📈 分析持仓标的...")
    all_results = []
    alerts_to_send = []
    for ticker, config in CONFIG["tickers"].items():
        print(f"   {ticker} ({config['name']})...")
        r = analyze_ticker(ticker, config)
        all_results.append(r)
        if r is None:
            continue
        print(f"      ${r['current']:.2f}  回调:{r['pullback_pct']*100:.2f}%  RSI:{r['rsi']:.0f}" if r['rsi'] else "")
        if r["triggered_level"] and should_send_alert(state, ticker, r["triggered_level"]):
            alerts_to_send.append(r)
            state["last_alerts"][f"{ticker}_{r['triggered_level']}"] = datetime.now().isoformat()

    events = get_upcoming_events(days_ahead=14)
    print(f"\n📅 未来 14 天事件: {len(events)} 个")

    # 2. 优先发警报
    sent = False
    if alerts_to_send:
        subject, html = format_alert_email(alerts_to_send, all_results, benchmarks, events)
        if send_email(subject, html):
            sent = True

    # 3. 强制建仓提醒
    if not sent and check_force_buy_reminder(state):
        last_alerts = state.get("last_alerts", {})
        if last_alerts:
            last_time = max(datetime.fromisoformat(t) for t in last_alerts.values())
        else:
            last_time = datetime.fromisoformat(state["first_run_date"])
        days_since = (datetime.now() - last_time).days
        subject, html = format_force_buy_reminder(all_results, benchmarks, events, days_since)
        if send_email(subject, html):
            state["last_alerts"]["__force_reminder__"] = datetime.now().isoformat()
            sent = True

    # 4. 周报
    if not sent and is_weekly_report_day(state):
        subject, html = format_weekly_report(all_results, benchmarks, events)
        if send_email(subject, html):
            state["last_weekly_report"] = datetime.now().isoformat()
    elif not sent:
        print("\n📭 本次无邮件需要发送")

    save_state(state)
    print(f"\n{'='*60}\n✅ 运行结束\n{'='*60}\n")


if __name__ == "__main__":
    main()
