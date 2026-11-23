# etf-monitor

基于 GitHub Actions 的零成本自动化投研系统，做两件事：

1. **回调择时**：盯 6 个半导体核心标的，跌到预设档位时按纪律提醒分批建仓。
2. **大师跟随**：每天检查巴菲特、段永平、李录、Chris Hohn 的 SEC 披露，按回测验证过的规则筛出信号，生成行动备忘录（邮件 + [在线看板](https://ddkaty.github.io/etf-monitor/)）。

无服务器、无数据库。Python 脚本加定时 workflow，数据以 JSON 回写仓库，看板经 GitHub Pages 发布。

---

## 目录结构

```
.
├── monitor.py                 # 管线 1: ETF 回调监控
├── follow.py                  # 管线 2: 大师跟随(披露检查、信号、邮件)
├── follow_site.py             #         看板与邮件的页面生成
├── paper.py                   #         模拟跟随组合(纸面交易)
├── alert_state.json           # 管线 1 状态
├── follow_state.json          # 管线 2 状态(已见过的披露)
├── data/
│   ├── 13f/                   # 2016Q4 以来的 13F,按 CUSIP 合并后的持仓
│   ├── entity_map.json        # CUSIP → 股票代码
│   ├── signals.json           # 当前信号、四位的最新持仓与最近 5 期变动
│   ├── portfolio.json         # 模拟组合的净值、持仓与交易记录
│   └── fast_filings.json      # 近 180 天的 Form 4 / 13D / 13G
├── backtest/                  # 规则的回测脚本与结果
├── docs/                      # 看板(自动生成,勿手改)
└── .github/workflows/
    ├── monitor.yml            # 工作日 UTC 22:30
    └── follow.yml             # 工作日 UTC 23:30
```

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

## 管线 2：大师跟随

### 跟踪对象

| 投资者 | 机构 | CIK |
|---|---|---|
| 巴菲特 | Berkshire Hathaway | 1067983 |
| 段永平 | H&H International | 1759760 |
| 李录 | Himalaya Capital | 1709323 |
| Chris Hohn | TCI Fund | 1647251 |

四位都是集中持仓、低换手、长期持有的风格，13F 对他们有代表性。

### 规则

| 类别 | 条件 | 处理 |
|---|---|---|
| 跟随 | 新建仓且权重 ≥5% | 列入研究清单 |
| 跟随 | 单季加仓 ≥20% 且权重 ≥2% | 列入研究清单 |
| 复查 | 清仓原权重 ≥3% 的仓位 | 提醒持有者重新审视 |
| 忽略 | 小仓位新建、小幅加仓、减仓 | 不提示 |
| 提醒 | 同一位投资者随后减仓 ≥20% 或清仓 | 标注"大师已减仓"，不作废 |
| 分歧 | 另一位跟踪对象在同期或之后减仓 ≥20% | 单独标出 |

信号有效期为披露后 12 个月，参考价取披露日之后第一个交易日的收盘价。

### 规则的依据

规则来自对七位投资者 2016 年底以来 268 份 13F 的回测（[backtest/](backtest/)，结果见看板"规则与依据"页）：

- 照抄前五大持仓没有稳定优势，所以只跟动作，不跟持仓。
- 大仓位新建仓之后 12 个月平均跑赢标普 13.7%（33 个样本），大幅加仓平均跑赢 5.7%（142 个样本）。
- 小仓位新建仓、连续加仓、减仓没有信息量；被清仓的股票约三分之二随后跑输。
- 披露后已跌破大师当季均价的标的后续表现最差，所以不设"等回调再买"的条件。

样本偏小，回测区间又是美股牛市，这些结论只算有迹象。

### 模拟组合

为了检验信号在实际中是否有效，系统按固定规则做纸面交易（[paper.py](paper.py)），并和标普 500 对比：

- **入场**：买入类信号披露后第一个交易日收盘。
- **仓位**：每条信号 10%，同一只股票不超过 20%，最多 10 只，闲置资金放在标普 500。
- **出场**：满 12 个月卖出，或收盘价跌破买入价 25% 时止损。

出场规则来自 `backtest/exit_backtest.py`：155 条信号上，没有哪种出场方式明显好于持有 12 个月；25% 止损几乎不损失平均收益，但把最差一笔的亏损从约 68% 收窄到约 33%；大师减仓即走没有明显帮助。

2026-11-24 之前的部分是用历史数据补算的，之后是逐日累积的实盘记录。

### 每天做什么

1. 检查四位是否有新的 13F；有就拉取、合并同一 CUSIP 的多行、剔除期权行。
2. 检查 Form 4 与 13D/13G，这些在交易后几天内披露，比 13F 快。
3. 更新行情，重算信号状态与披露后的涨跌。
4. 重建看板并提交。

### 邮件

- **行动备忘录**：任一位披露新 13F 时发送。
- **快速披露提醒**：出现新的 Form 4 / 13D / 13G 时发送。
- **周报**：每周一发送当前信号状态。

### 重跑回测

```bash
pip install yfinance pandas numpy
python backtest/fetch_13f.py      # 补齐 13F
python backtest/prices.py         # 下载行情到 backtest/cache/
python backtest/run_backtest.py   # 输出 backtest/results.json 与 events.csv
python backtest/exit_backtest.py  # 出场规则对比,输出 backtest/exit_results.json
```

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

在 `monitor.py` 的 `CONFIG["email"]` 中（大师跟随复用同一份发信配置）：

```python
"sender":    "your_email@gmail.com",   # 你的 Gmail
"recipient": "your_email@gmail.com",   # 收件邮箱（可与发件相同）
```

同时把 `follow.py` 顶部的 `USER_AGENT` 改成你自己的联系方式（SEC 要求）：

```python
USER_AGENT = "SuperinvestorMonitor your_email@gmail.com"
```

### 5. 启用 Actions

Fork 后 Actions 默认是禁用的。进入 `Actions` 标签页点击 `I understand my workflows, go ahead and enable them`。

### 6. 手动跑一次

在 `Actions → ETF 回调监控 v3 / 大师跟随 → Run workflow` 触发一次，确认邮件能正常收到。

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

### 增减跟踪对象（管线 2）

编辑 [`follow.py`](follow.py) 中的 `INVESTORS`，CIK 可在 [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany) 查到。新增对象后先跑一次 `python backtest/fetch_13f.py` 补齐历史，并建议重跑回测确认此人的 13F 值得跟。

信号阈值在同一文件的 `RULES` 中。改阈值前请先用 `backtest/run_backtest.py` 验证。

### 调整冷却时间 / 周报日 / 强制建仓阈值

```python
"cooldown_hours": 24,                 # 同档位警报多久不重发
"weekly_report_day": 0,               # 0=周一, 6=周日
"force_buy_reminder_days": 180,       # 多少天无警报触发强制建仓提醒
```

---

## 状态持久化

workflow 跑完后用 `[skip ci]` commit 把以下内容回写主分支：

- `alert_state.json`：管线 1 的警报冷却与周报时间
- `follow_state.json`：管线 2 已见过的 13F 与快速披露
- `data/`、`docs/`：持仓数据、信号与看板

---

## 本地开发

```bash
pip install yfinance pandas numpy
export SMTP_PASSWORD="your_app_password"   # 留空则只计算不发信
python monitor.py
python follow.py
python -m http.server 8000 --directory docs   # 预览看板
```

---

## 数据来源与限制

- **行情**：[yfinance](https://github.com/ranaroussi/yfinance)，偶有限流或缺数据；下载失败时看板沿用上次价格并注明。
- **持仓与披露**：[SEC EDGAR](https://www.sec.gov/edgar/sec-api-documentation)。13F 只含美股多头，最晚在季度结束 45 天后公布。
- **代码映射**：[OpenFIGI](https://www.openfigi.com/api)。已退市的股票通常匹配不到，没有行情。

---

## 免责声明

本项目仅供个人学习和投研自动化使用。所有邮件中出现的"买入档位""仓位建议"等仅基于预设规则的机械触发，**不构成任何形式的投资建议**。市场有风险，决策请自负。
