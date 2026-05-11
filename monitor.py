"""
ETF 回调监控脚本 v2.0
监控目标：SOXX, SMH, AMD, AVGO, TSM, MU
功能：
  1. 每个交易日检测回调，触发时发警报
  2. 每周一发送周报（全部状态总览）
  3. 6个月没触发任何警报时，提醒强制建仓

使用方法:
  1. 修改 CONFIG["email"] 中的邮箱
  2. 在 GitHub Secret 中设置 SMTP_PASSWORD
  3. 自动通过 GitHub Actions 运行
"""

import os
import smtplib
import json
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import yfinance as yf

# ============ 配置区 ============
CONFIG = {
    # 监控的标的
    "tickers": {
        # ETF
        "SOXX": {
            "name": "iShares 半导体 ETF",
            "type": "ETF",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
        "SMH": {
            "name": "VanEck 半导体 ETF",
            "type": "ETF",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
        # 个股
        "AMD": {
            "name": "超威半导体",
            "type": "STOCK",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
        "AVGO": {
            "name": "博通",
            "type": "STOCK",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
        "TSM": {
            "name": "台积电",
            "type": "STOCK",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
        "MU": {
            "name": "美光科技",
            "type": "STOCK",
            "lookback_days": 60,
            "alert_levels": [0.08, 0.15, 0.25],
        },
    },

    # 邮件配置
    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender": "your_email@gmail.com",      # ⚠️ 改成你的 Gmail
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "recipient": "your_email@gmail.com",   # ⚠️ 改成你的 Gmail
    },

    # 防止重复发邮件：同一档位多少小时内不重发
    "cooldown_hours": 24,

    # 周报：每周哪一天发送（0=周一, 6=周日）
    # 注意：GitHub Actions 用 UTC，美东周一对应 UTC 周一上午
    "weekly_report_day": 0,  # 周一

    # 6个月强制建仓提醒
    "force_buy_reminder_days": 180,

    # 状态文件路径
    "state_file": "alert_state.json",
}


def load_state():
    """读取状态文件"""
    if os.path.exists(CONFIG["state_file"]):
        with open(CONFIG["state_file"], "r") as f:
            return json.load(f)
    return {
        "last_alerts": {},          # 最近一次警报时间（按 ticker_level）
        "last_weekly_report": None, # 最近一次周报时间
        "first_run_date": datetime.now().isoformat(),  # 首次运行时间
    }


def save_state(state):
    """保存状态"""
    with open(CONFIG["state_file"], "w") as f:
        json.dump(state, f, indent=2)


def get_ticker_data(ticker, lookback_days):
    """获取标的数据"""
    try:
        period_days = lookback_days + 30
        end = datetime.now()
        start = end - timedelta(days=period_days)

        stock = yf.Ticker(ticker)
        hist = stock.history(start=start, end=end, interval="1d")

        if hist.empty:
            return None

        current_price = hist["Close"].iloc[-1]
        prev_price = hist["Close"].iloc[-2] if len(hist) > 1 else current_price
        day_change_pct = (current_price - prev_price) / prev_price * 100

        recent = hist.tail(lookback_days)
        peak_price = recent["High"].max()
        peak_date = recent["High"].idxmax().strftime("%Y-%m-%d")
        low_price = recent["Low"].min()

        return {
            "current": current_price,
            "prev": prev_price,
            "day_change_pct": day_change_pct,
            "peak": peak_price,
            "peak_date": peak_date,
            "low": low_price,
        }
    except Exception as e:
        print(f"❌ 获取 {ticker} 数据失败: {e}")
        return None


def check_pullback(ticker, config):
    """检测回调"""
    data = get_ticker_data(ticker, config["lookback_days"])
    if data is None:
        return None

    pullback_pct = (data["peak"] - data["current"]) / data["peak"]

    triggered_level = None
    for level in sorted(config["alert_levels"], reverse=True):
        if pullback_pct >= level:
            triggered_level = level
            break

    return {
        "ticker": ticker,
        "name": config["name"],
        "type": config["type"],
        "current": data["current"],
        "day_change_pct": data["day_change_pct"],
        "peak": data["peak"],
        "peak_date": data["peak_date"],
        "low": data["low"],
        "pullback_pct": pullback_pct,
        "triggered_level": triggered_level,
        "alert_levels": config["alert_levels"],
    }


def should_send_alert(state, ticker, triggered_level):
    """判断是否应发警报（24小时冷却）"""
    if triggered_level is None:
        return False

    key = f"{ticker}_{triggered_level}"
    last_sent = state["last_alerts"].get(key)

    if not last_sent:
        return True

    last_time = datetime.fromisoformat(last_sent)
    return datetime.now() - last_time > timedelta(hours=CONFIG["cooldown_hours"])


def is_weekly_report_day(state):
    """判断今天是否该发周报"""
    today = datetime.now()
    if today.weekday() != CONFIG["weekly_report_day"]:
        return False

    last_report = state.get("last_weekly_report")
    if not last_report:
        return True

    last_time = datetime.fromisoformat(last_report)
    # 距上次周报超过 6 天才发（防止同一天重复）
    return (today - last_time).days >= 6


def check_force_buy_reminder(state):
    """检查是否需要发 6 个月强制建仓提醒"""
    last_alerts = state.get("last_alerts", {})

    if not last_alerts:
        # 从首次运行算起
        first_run = state.get("first_run_date")
        if not first_run:
            return False
        last_time = datetime.fromisoformat(first_run)
    else:
        # 取最近一次任何警报的时间
        last_times = [datetime.fromisoformat(t) for t in last_alerts.values()]
        last_time = max(last_times)

    days_since = (datetime.now() - last_time).days
    return days_since >= CONFIG["force_buy_reminder_days"]


def render_status_table(results):
    """生成状态表 HTML"""
    html = """
    <table style="width: 100%; border-collapse: collapse; margin: 12px 0;">
        <tr style="background: #f3f4f6;">
            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">标的</th>
            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">名称</th>
            <th style="padding: 10px; text-align: right; border: 1px solid #ddd;">当前价</th>
            <th style="padding: 10px; text-align: right; border: 1px solid #ddd;">日变化</th>
            <th style="padding: 10px; text-align: right; border: 1px solid #ddd;">60日高点</th>
            <th style="padding: 10px; text-align: right; border: 1px solid #ddd;">回调幅度</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">状态</th>
        </tr>
    """

    for r in results:
        if r is None:
            continue

        pct = r["pullback_pct"] * 100
        day_pct = r["day_change_pct"]

        # 颜色逻辑
        if pct >= 25:
            pct_color = "#16a34a"  # 绿（深度回调=机会）
            status = "🟢 深度回调"
        elif pct >= 15:
            pct_color = "#eab308"
            status = "🟡 明显回调"
        elif pct >= 8:
            pct_color = "#f97316"
            status = "🟠 健康回调"
        else:
            pct_color = "#6b7280"
            status = "⚪ 未达档位"

        day_color = "#16a34a" if day_pct >= 0 else "#dc2626"
        day_sign = "+" if day_pct >= 0 else ""

        html += f"""
        <tr>
            <td style="padding: 10px; border: 1px solid #ddd;"><b>{r['ticker']}</b></td>
            <td style="padding: 10px; border: 1px solid #ddd; color: #6b7280;">{r['name']}</td>
            <td style="padding: 10px; text-align: right; border: 1px solid #ddd;">${r['current']:.2f}</td>
            <td style="padding: 10px; text-align: right; border: 1px solid #ddd; color: {day_color};">
                {day_sign}{day_pct:.2f}%
            </td>
            <td style="padding: 10px; text-align: right; border: 1px solid #ddd;">${r['peak']:.2f}</td>
            <td style="padding: 10px; text-align: right; border: 1px solid #ddd; color: {pct_color}; font-weight: bold;">
                -{pct:.2f}%
            </td>
            <td style="padding: 10px; text-align: center; border: 1px solid #ddd;">{status}</td>
        </tr>
        """

    html += "</table>"
    return html


def format_alert_email(alerts_to_send, all_results):
    """触发警报邮件"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    tickers = [a["ticker"] for a in alerts_to_send]
    subject = f"🔔 ETF/个股 回调警报: {', '.join(tickers)}"

    html = f"""
    <html>
    <body style="font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #d97706; border-bottom: 2px solid #fbbf24; padding-bottom: 8px;">
            🔔 回调触发警报
        </h2>
        <p style="color: #6b7280; font-size: 13px;">检测时间: {now}</p>

        <h3 style="color: #dc2626;">⚡ 触发的警报</h3>
    """

    for alert in alerts_to_send:
        level_pct = alert["triggered_level"] * 100
        pullback_pct = alert["pullback_pct"] * 100

        if level_pct >= 25:
            action = "🟢 第三档买入（30% 仓位）— 深度回调，罕见机会"
            color = "#16a34a"
            bg = "#dcfce7"
        elif level_pct >= 15:
            action = "🟡 第二档买入（40% 仓位）— 明显回调"
            color = "#ca8a04"
            bg = "#fef9c3"
        else:
            action = "🟠 第一档买入（30% 仓位）— 健康回调"
            color = "#ea580c"
            bg = "#ffedd5"

        type_emoji = "📊" if alert["type"] == "ETF" else "📈"

        html += f"""
        <div style="border-left: 4px solid {color}; padding: 14px; margin: 14px 0; background: {bg}; border-radius: 4px;">
            <h4 style="margin: 0 0 10px 0; font-size: 16px;">
                {type_emoji} {alert['ticker']} - {alert['name']}
            </h4>
            <table style="width: 100%; font-size: 14px;">
                <tr>
                    <td style="padding: 3px 0; color: #4b5563;">当前价:</td>
                    <td style="padding: 3px 0; text-align: right;"><b>${alert['current']:.2f}</b></td>
                </tr>
                <tr>
                    <td style="padding: 3px 0; color: #4b5563;">60 日高点:</td>
                    <td style="padding: 3px 0; text-align: right;">${alert['peak']:.2f} ({alert['peak_date']})</td>
                </tr>
                <tr>
                    <td style="padding: 3px 0; color: #4b5563;">回调幅度:</td>
                    <td style="padding: 3px 0; text-align: right; color: #dc2626;">
                        <b>-{pullback_pct:.2f}%</b>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 3px 0; color: #4b5563;">触发档位:</td>
                    <td style="padding: 3px 0; text-align: right;"><b>-{level_pct:.0f}%</b></td>
                </tr>
            </table>
            <div style="margin-top: 10px; padding: 10px; background: white; border-radius: 4px;">
                <b>📋 建仓建议:</b> {action}
            </div>
        </div>
        """

    html += "<h3>📊 全部标的当前状态</h3>"
    html += render_status_table(all_results)

    html += """
        <div style="margin-top: 30px; padding: 15px; background: #fffbeb; border-left: 4px solid #f59e0b;">
            <p style="margin: 0 0 8px 0;"><b>💡 提醒：</b></p>
            <ul style="margin: 0; padding-left: 20px; color: #78350f;">
                <li>建仓建议基于"方案A 保守派"分批策略</li>
                <li>触发警报不等于必须买入，仍需自己判断</li>
                <li>同一档位 24 小时内不重复发送警报</li>
            </ul>
        </div>

        <p style="margin-top: 24px; color: #9ca3af; font-size: 11px; text-align: center;">
            本邮件由 ETF 自动监控脚本发送 | 不构成投资建议
        </p>
    </body>
    </html>
    """

    return subject, html


def format_weekly_report(all_results):
    """周报邮件"""
    now = datetime.now().strftime("%Y-%m-%d")
    subject = f"📊 ETF/个股 周报 - {now}"

    # 统计触发情况
    triggered_count = sum(1 for r in all_results if r and r["triggered_level"])
    total_count = sum(1 for r in all_results if r)

    if triggered_count == 0:
        summary = "✅ 本周所有标的均未触发回调警报，整体处于强势状态。"
        summary_color = "#16a34a"
    elif triggered_count <= 2:
        summary = f"⚠️ 本周有 {triggered_count}/{total_count} 个标的触发回调警报，建议关注。"
        summary_color = "#f97316"
    else:
        summary = f"🚨 本周有 {triggered_count}/{total_count} 个标的触发回调警报，板块普遍调整中。"
        summary_color = "#dc2626"

    html = f"""
    <html>
    <body style="font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2563eb; border-bottom: 2px solid #93c5fd; padding-bottom: 8px;">
            📊 ETF/个股 周报
        </h2>
        <p style="color: #6b7280; font-size: 13px;">报告日期: {now}</p>

        <div style="padding: 14px; background: #eff6ff; border-left: 4px solid {summary_color}; border-radius: 4px; margin: 16px 0;">
            <p style="margin: 0; font-size: 15px; color: {summary_color};"><b>{summary}</b></p>
        </div>

        <h3>📈 全部标的状态总览</h3>
    """
    html += render_status_table(all_results)

    # 找出最接近触发的标的
    near_trigger = []
    for r in all_results:
        if r and not r["triggered_level"]:
            min_level = min(r["alert_levels"])
            gap = min_level - r["pullback_pct"]
            if gap < 0.05:  # 距第一档不到 5%
                near_trigger.append((r, gap))

    if near_trigger:
        near_trigger.sort(key=lambda x: x[1])
        html += "<h3>🎯 接近触发的标的（距第一档警报 &lt; 5%）</h3><ul>"
        for r, gap in near_trigger:
            html += f"<li><b>{r['ticker']}</b>: 当前回调 {r['pullback_pct']*100:.2f}%，再跌 {gap*100:.2f}% 即触发</li>"
        html += "</ul>"

    html += """
        <div style="margin-top: 30px; padding: 15px; background: #f0fdf4; border-left: 4px solid #16a34a;">
            <p style="margin: 0 0 8px 0;"><b>📋 操作提醒（方案A 保守派）：</b></p>
            <ul style="margin: 0; padding-left: 20px; color: #166534;">
                <li><b>未触发档位</b>: 继续等待，资金放短债赚利息</li>
                <li><b>触发 -8%</b>: 买入第一档 30%</li>
                <li><b>触发 -15%</b>: 买入第二档 40%</li>
                <li><b>触发 -25%</b>: 买入第三档 30%</li>
                <li><b>6 个月未触发</b>: 强制建仓 15% 防止踏空</li>
            </ul>
        </div>

        <p style="margin-top: 24px; color: #9ca3af; font-size: 11px; text-align: center;">
            周报每周一发送 | 触发警报时会即时单独发送 | 不构成投资建议
        </p>
    </body>
    </html>
    """

    return subject, html


def format_force_buy_reminder(all_results, days_since):
    """6 个月强制建仓提醒邮件"""
    subject = f"⏰ 6个月强制建仓提醒 - 已等待 {days_since} 天"

    html = f"""
    <html>
    <body style="font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #7c3aed; border-bottom: 2px solid #c4b5fd; padding-bottom: 8px;">
            ⏰ 强制建仓纪律提醒
        </h2>

        <div style="padding: 16px; background: #faf5ff; border-left: 4px solid #7c3aed; border-radius: 4px; margin: 16px 0;">
            <p style="margin: 0 0 8px 0; font-size: 15px;">
                <b>已经 {days_since} 天没有触发任何回调警报。</b>
            </p>
            <p style="margin: 0; color: #6b21a8;">
                按照方案A 的纪律，当 6 个月（180天）一直没等到回调时，应该<b>强制建仓 15% 仓位</b>，以避免完全踏空 AI 硬件赛道。
            </p>
        </div>

        <h3>🎯 当前建议执行的操作</h3>
        <div style="padding: 14px; background: #fef3c7; border-radius: 6px;">
            <p style="margin: 0 0 10px 0;"><b>立即买入第一档的一半（总仓位 15%）</b></p>
            <p style="margin: 0; color: #78350f; font-size: 14px;">
                理由：
                <br>① 牛市中等不到回调是常态
                <br>② 6 个月没动说明趋势比预期更强
                <br>③ 部分建仓总比完全错过好
                <br>④ 剩余 85% 资金继续按原计划等回调
            </p>
        </div>

        <h3>📊 当前所有标的状态</h3>
    """
    html += render_status_table(all_results)

    html += """
        <div style="margin-top: 24px; padding: 12px; background: #e0e7ff; border-radius: 6px;">
            <p style="margin: 0; color: #3730a3; font-size: 14px;">
                💭 <b>心态提醒</b>: 不要因为价格已经很高而拒绝建仓。
                如果你这 6 个月里的纪律是"等回调才买"，现在的强制建仓就是这个纪律的一部分。
                这不是追高，是<b>反踏空保险</b>。
            </p>
        </div>

        <p style="margin-top: 24px; color: #9ca3af; font-size: 11px; text-align: center;">
            6 个月强制建仓提醒 | 触发后会重置计时 | 不构成投资建议
        </p>
    </body>
    </html>
    """

    return subject, html


def send_email(subject, html_content):
    """发送邮件"""
    cfg = CONFIG["email"]

    if not cfg["password"]:
        print("❌ 错误: 未设置 SMTP_PASSWORD 环境变量")
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


def main():
    print(f"\n{'='*60}")
    print(f"🚀 ETF/个股 回调监控运行: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    state = load_state()
    all_results = []
    alerts_to_send = []

    # 1. 检测所有标的
    for ticker, config in CONFIG["tickers"].items():
        print(f"📈 检测 {ticker} ({config['name']})...")
        result = check_pullback(ticker, config)
        all_results.append(result)

        if result is None:
            print(f"   ⚠️  数据获取失败\n")
            continue

        pullback_pct = result["pullback_pct"] * 100
        print(f"   当前价: ${result['current']:.2f} (日变化 {result['day_change_pct']:+.2f}%)")
        print(f"   60日高点: ${result['peak']:.2f} | 回调: -{pullback_pct:.2f}%")

        if result["triggered_level"]:
            level_pct = result["triggered_level"] * 100
            print(f"   ⚡ 触发档位: -{level_pct:.0f}%")

            if should_send_alert(state, ticker, result["triggered_level"]):
                alerts_to_send.append(result)
                state["last_alerts"][f"{ticker}_{result['triggered_level']}"] = datetime.now().isoformat()
            else:
                print(f"   ⏸️  24小时冷却期内，跳过")
        else:
            print(f"   ✅ 未触发")
        print()

    # 2. 优先级最高：发送触发警报
    sent_alert = False
    if alerts_to_send:
        subject, html = format_alert_email(alerts_to_send, all_results)
        if send_email(subject, html):
            sent_alert = True

    # 3. 检查 6 个月强制建仓提醒（仅在没有触发警报时发）
    if not sent_alert and check_force_buy_reminder(state):
        # 算实际天数
        last_alerts = state.get("last_alerts", {})
        if last_alerts:
            last_times = [datetime.fromisoformat(t) for t in last_alerts.values()]
            last_time = max(last_times)
        else:
            last_time = datetime.fromisoformat(state["first_run_date"])
        days_since = (datetime.now() - last_time).days

        print(f"⏰ 已 {days_since} 天无警报，发送强制建仓提醒")
        subject, html = format_force_buy_reminder(all_results, days_since)
        if send_email(subject, html):
            # 重置计时（避免每天都发）
            state["last_alerts"]["__force_reminder__"] = datetime.now().isoformat()
            sent_alert = True

    # 4. 周报（仅在没发其他邮件时发，避免轰炸）
    if not sent_alert and is_weekly_report_day(state):
        print("📅 周一，发送周报")
        subject, html = format_weekly_report(all_results)
        if send_email(subject, html):
            state["last_weekly_report"] = datetime.now().isoformat()
    elif not sent_alert:
        print("📭 本次无邮件需要发送")

    # 5. 保存状态
    save_state(state)
    print(f"\n{'='*60}")
    print(f"✅ 运行结束")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
