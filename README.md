# etf-monitor

基于 GitHub Actions 的零成本自动化投研系统，聚焦 **AI / 半导体赛道**，把两件最容易拖延的事自动化：

1. **回调择时** —— 盯盘 6 个半导体核心标的，达到预设回调档位时按纪律提醒分批建仓
2. **价投跟踪** —— 季度抓取 5 位价值投资大师的 SEC 13F 持仓，diff 出新建/清仓/加减仓，找出多人共识标的，**同时自动生成可在线访问的看板网站**

无服务器、无数据库，纯 Python 脚本 + 定时 workflow，状态以 JSON 形式回写仓库持久化；静态网站经 GitHub Pages 发布，免运维零成本。

---

## 目录结构

```
.
├── monitor.py                       # 管线 1: ETF 回调监控 (yfinance)
├── superinvestors_monitor.py        # 管线 2: 大师 13F 监控 (SEC EDGAR) + history 归档
├── backfill_history.py              # 工具: 一次性回溯抓取过去 N 季 13F 建底库
├── build_site.py                    # 工具: 读 history/ 生成 docs/ 静态站点
├── alert_state.json                 # 管线 1 状态
├── superinvestor_state.json         # 管线 2 状态
├── history/                         # 13F 季度归档 (每位大师每季一个 JSON)
│   ├── Buffett_2025Q1.json … Q4.json
│   ├── Duan_*.json / LiLu_*.json / Marks_*.json / Pabrai_*.json
├── docs/                            # GitHub Pages 静态站点 (自动生成)
│   ├── index.html                   #   总览看板
│   ├── consensus.html               #   共识标的全表
│   ├── history.html                 #   季度横向矩阵
│   ├── Buffett.html …               #   每位大师的详情页
│   ├── assets/style.css             #   共用样式
│   └── data/snapshot.json           #   完整数据快照
└── .github/workflows/
    ├── monitor.yml                  # 工作日美东收盘后跑 ETF 监控
    └── superinvestors.yml           # 跑 13F 监控 + 构建站点 + 回写归档与 docs/
```

> 仓库根目录另有一份 `monitor.yml`，是早期版本的备份，**实际生效的是 `.github/workflows/` 下的两份**。

---

## 管线 1：ETF 回调监控

### 监控标的与档位

| 标的 | 名称 | 触发档位（相对 60 日高点） |
|------|------|---------------------------|
| SOXX | iShares 半导体 ETF | -8% / -15% / -25% |
| SMH  | VanEck 半导体 ETF  | -8% / -15% / -25% |
| AMD  | 超威半导体         | -8% / -15% / -25% |
| AVGO | 博通               | -8% / -15% / -25% |
| TSM  | 台积电             | -8% / -15% / -25% |
| MU   | 美光科技           | -8% / -15% / -25% |

对应的建仓纪律：第一档买入 30% 仓位、第二档买入 40%、第三档买入 30%（深度回调）。

### 邮件类型

- **回调警报**：任一标的触发档位时立即发送，24h 内同档位不重复
- **每周报表**：周一发送，全景扫描所有标的 + 市场温度 + 未来 14 天事件
- **强制建仓提醒**：连续 180 天无任何警报时触发，避免长牛市踏空

### 附加量化指标

- **市场温度**：VIX 恐慌指数（自动分档：极度平静 / 平静 / 警觉 / 紧张 / 恐慌）、S&P 500、QQQ、SOX
- **个股指标**：RSI(14)、60 日 Unicode sparkline、趋势检测、近 5 日成交量异常度、30 日回报
- **距下一档位**：列出 Top 3 最接近触发档位的标的及差距
- **事件日历**：手动维护在 `CONFIG["events"]` 中的财报 / CPI / FOMC / 利率决议等关键日期

### 运行节奏

`.github/workflows/monitor.yml`：

```yaml
cron: '30 22 * * 1-5'   # 工作日 UTC 22:30 = 美东 17:30（夏令时收盘后）
```

---

## 管线 2：大师 13F 持仓监控

### 跟踪对象

| 大师 | 机构 | CIK | 风格备注 |
|------|------|------|----------|
| Warren Buffett | Berkshire Hathaway | 0001067983 | 已卸任 CEO，继任 Greg Abel |
| 段永平 | H&H International Investment | 0001759760 | 雪球: 大道无形我有型 |
| 李录 | Himalaya Capital | 0001709323 | 芒格亲选接班人，极致集中 |
| Howard Marks | Oaktree Capital | 0000949509 | 周期意识，信贷为主 |
| Mohnish Pabrai | Dalal Street | 0001549575 | 克隆者，极致集中 |

### 核心能力

- 直连 SEC EDGAR 官方 API（带 User-Agent，429 自动指数退避重试）
- 解析 13F-HR XML 信息表（自动剥离 xmlns 命名空间）
- **季度持仓 diff**：新建仓 / 清仓 / 加仓 / 减仓，按变动幅度排序
- **共识标的**：自动找出 ≥2 位大师同时持有的标的，按共识强度打星
- 金额自动标准化：兼容 SEC 在 2022 Q4 前的"千美元"口径切换

### 邮件类型

- **新 13F 提醒**：任一大师提交新季度报告时立即发送，并附带与上季持仓的对比
- **周一周报**：全景扫描 5 位大师持仓 + 共识标的榜单 + 下次 13F 公布倒计时

### 运行节奏

`.github/workflows/superinvestors.yml`：

```yaml
cron: '0 23 10-20 2,5,8,11 *'   # 13F 公布期(2/5/8/11月10-20日)每日检查
cron: '0 23 * * 1'              # 其余时间仅周一
```

每次运行除了发邮件,还会自动归档新季度到 `history/` 并重建 `docs/` 静态站点。

---

## 在线看板网站

每次 13F 监控运行后,会自动生成静态网站发布到 GitHub Pages,**无需登录邮箱也能随时查看**最新持仓、季度变化与跨大师共识。

启用后访问地址形如:

```
https://<你的GitHub用户名>.github.io/etf-monitor/
```

### 站点页面

| 页面 | 内容 |
|------|------|
| **总览** (`index.html`) | 5 位大师 KPI 卡片、本季亮点(新共识/集体加注/集体退出/重注信号)、共识 Top 10、整体解读 |
| **共识榜** (`consensus.html`) | 所有 ≥2 位大师同时持有的标的全表,强共识 (≥3 位) 单独高亮 |
| **历史时间线** (`history.html`) | 跨季度 × 跨大师矩阵,每格显示当季 AUM、持仓数、顶仓、QoQ 变化 |
| **大师单页** (`Buffett.html` / `Duan.html` / `LiLu.html` / `Marks.html` / `Pabrai.html`) | 风格标签、KPI(集中度/换手率)、Top 20 持仓、AUM 时间线柱状图、逐季演化卡片、风格解读 |

### 启用 GitHub Pages

1. 仓库 `Settings → Pages`
2. **Source** 选 `Deploy from a branch`
3. **Branch** 选 `main` + `/docs` 目录
4. 保存后等 1-2 分钟,刷新即可访问

### 首次建底库 (本地一次)

新仓库 fork 后 `history/` 还是空的,需要先回溯抓取建底库:

```bash
python backfill_history.py 4      # 回溯 4 个季度 (默认)
python backfill_history.py 8      # 回溯 8 个季度
python build_site.py              # 生成 docs/
git add history/ docs/ && git commit -m "Initial history backfill" && git push
```

之后每周 / 每个 13F 公布日的自动 workflow 会持续追加新季度。

---

## 快速开始

### 1. Fork 仓库

点击右上角 Fork 到自己的账户。

### 2. 准备 Gmail 应用专用密码

由于使用 SMTP 发邮件，需要在 [Google 账户安全设置](https://myaccount.google.com/apppasswords) 里生成一个 **App Password**（不是 Gmail 登录密码）。前提是 Gmail 已开启两步验证。

### 3. 添加 GitHub Secret

进入仓库 `Settings → Secrets and variables → Actions → New repository secret`：

| Name | Value |
|------|-------|
| `SMTP_PASSWORD` | 上一步生成的 16 位 App Password |

### 4. 修改收发件邮箱

在 `monitor.py` 和 `superinvestors_monitor.py` 的 `CONFIG["email"]` 中：

```python
"sender":    "your_email@gmail.com",   # 你的 Gmail
"recipient": "your_email@gmail.com",   # 收件邮箱（可与发件相同）
```

同时把 `superinvestors_monitor.py` 顶部的 `user_agent` 改成你自己的邮箱（SEC 要求）：

```python
"user_agent": "SuperinvestorMonitor your_email@gmail.com",
```

### 5. 启用 Actions

Fork 后 Actions 默认是禁用的。进入 `Actions` 标签页点击 `I understand my workflows, go ahead and enable them`。

### 6. 手动跑一次

在 `Actions → ETF 回调监控 v3 / 大师持仓监控 → Run workflow` 触发一次，确认邮件能正常收到。

---

## 自定义配置

### 修改监控标的（管线 1）

编辑 [`monitor.py`](monitor.py) 中的 `CONFIG["tickers"]`：

```python
"tickers": {
    "NVDA": {
        "name": "英伟达",
        "type": "STOCK",
        "lookback_days": 60,
        "alert_levels": [0.08, 0.15, 0.25],
    },
    ...
}
```

### 维护事件日历（管线 1）

`CONFIG["events"]` 是一个手动列表，按 `(日期, 类型, 描述)` 三元组：

```python
"events": [
    ("2026-05-21", "财报", "NVDA 英伟达 Q1 财报（盘后）"),
    ("2026-06-12", "会议", "FOMC 利率决议"),
    ...
]
```

`类型` 字段控制邮件中徽章颜色，可选 `财报` / `数据` / `会议`。

### 增减大师（管线 2）

编辑 [`superinvestors_monitor.py`](superinvestors_monitor.py) 中的 `CONFIG["investors"]`。CIK 号可在 [SEC EDGAR 搜索](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany) 中查到，必须填写包含前导零的 10 位完整 CIK。

### 调整冷却时间 / 周报日 / 强制建仓阈值

```python
"cooldown_hours": 24,                 # 同档位警报多久不重发
"weekly_report_day": 0,               # 0=周一, 6=周日
"force_buy_reminder_days": 180,       # 多少天无警报触发强制建仓提醒
```

---

## 状态持久化

GitHub Actions 跑完后，workflow 会自动用 `[skip ci]` commit 把以下文件回写主分支：

- `alert_state.json` —— 管线 1 状态：每个 ticker_档位 的最后警报时间、上次周报时间、首次运行日期
- `superinvestor_state.json` —— 管线 2 状态：每位大师的最近 accession 号、上次周报时间
- `history/*.json` —— 13F 季度归档，每位大师每季一份完整持仓快照
- `docs/**` —— 重新构建的静态站点

**不要手动编辑 `*_state.json`**，除非要重置状态。`history/` 是季度对比与网站的数据底库，按需可手动添加/修复历史 JSON。

---

## 本地开发

```bash
# 管线 1 (ETF 监控) 需要 yfinance/pandas/numpy
pip install yfinance pandas numpy

# 管线 2 (大师监控 + 站点构建) 纯标准库,无需额外依赖

export SMTP_PASSWORD="your_app_password"
python monitor.py                        # 跑 ETF 监控
python superinvestors_monitor.py         # 跑大师监控 (含 history 归档)
python backfill_history.py 4             # 回溯 4 季度建底库 (首次部署时)
python build_site.py                     # 重新生成 docs/

# 本地预览站点
python -m http.server 8000 --directory docs
# 浏览器打开 http://localhost:8000
```

只想看输出不发邮件？把 `SMTP_PASSWORD` 留空，脚本会跑完所有分析并打印结果，邮件发送步骤会自动跳过。

---

## 数据来源与限制

- **管线 1**：[yfinance](https://github.com/ranaroussi/yfinance)，免费的 Yahoo Finance 非官方包装。偶有 rate limit / 数据缺失，脚本对单 ticker 失败有容错
- **管线 2**：[SEC EDGAR REST API](https://www.sec.gov/edgar/sec-api-documentation)，官方权威。**13F 数据有 45 天滞后**（季末后第 45 天才公布），无法用作短线择时

---

## 免责声明

本项目仅供个人学习和投研自动化使用。所有邮件中出现的"买入档位""仓位建议"等仅基于预设规则的机械触发，**不构成任何形式的投资建议**。市场有风险，决策请自负。
