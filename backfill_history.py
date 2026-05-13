"""
13F 历史回溯抓取脚本

用途: 一次性从 SEC EDGAR 回溯抓取每位大师过去 N 个季度的 13F 持仓,
      并以 history/{Investor}_{YYYY}Q{N}.json 的形式归档.
      之后由 build_site.py 读取这些归档生成静态站点.

幂等: 同一份归档已存在(同 accession)则跳过,可重复运行.

用法:
    python backfill_history.py              # 默认回溯 4 个季度
    python backfill_history.py 8            # 回溯 8 个季度
    python backfill_history.py 4 Buffett    # 只补抓巴菲特
"""

import os
import sys
import json
import time
from datetime import datetime

# 复用主脚本的所有 SEC 抓取逻辑
from superinvestors_monitor import (
    CONFIG,
    sec_request,
    get_recent_filings,
    find_all_13f,
    fetch_13f_holdings,
    normalize_value,
    quarter_label,
    history_path,
    save_quarter_to_history,
)


def backfill_investor(key, info, n_quarters):
    """回溯单位大师过去 n_quarters 个季度"""
    print(f"\n{'='*60}")
    print(f"📊 {info['name_cn']} ({info['fund']}) — 回溯最近 {n_quarters} 季")
    print(f"{'='*60}")

    submissions = get_recent_filings(info["cik"])
    if not submissions:
        print(f"   ❌ 无法获取提交记录")
        return 0

    filings = find_all_13f(submissions, max_count=n_quarters)
    if not filings:
        print(f"   ❌ 未找到任何 13F-HR")
        return 0

    print(f"   🔍 共找到 {len(filings)} 份 13F-HR (按提交时间倒序)")
    saved = 0
    for i, filing in enumerate(filings, 1):
        quarter = quarter_label(filing["report_date"])
        path = history_path(key, quarter)

        # 已有同 accession 则跳过(幂等)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing.get("accession") == filing["accession"]:
                    print(f"   [{i}/{len(filings)}] {quarter} 已归档 (accession {filing['accession'][:20]}...), 跳过")
                    continue
            except Exception:
                pass

        print(f"   [{i}/{len(filings)}] 抓取 {quarter} 报告期 {filing['report_date']} (提交 {filing['filing_date']})...")

        holdings = fetch_13f_holdings(info["cik"], filing)
        if not holdings:
            print(f"      ⚠️ 持仓为空,跳过")
            continue

        # 金额标准化(2022 Q4 前是千美元)
        for h in holdings:
            h["value_usd"] = normalize_value(h["value_usd"], filing["report_date"])

        holdings.sort(key=lambda x: x["value_usd"], reverse=True)
        total = sum(h["value_usd"] for h in holdings)
        for h in holdings:
            h["weight_pct"] = (h["value_usd"] / total * 100) if total else 0

        save_quarter_to_history(key, info, filing, holdings, total)
        print(f"      ✅ 已归档 {len(holdings)} 只持仓,总市值 ${total/1e9:.2f}B")
        saved += 1

        # SEC 礼貌限流
        time.sleep(0.6)

    return saved


def main():
    n_quarters = 4
    only_key = None

    args = sys.argv[1:]
    if args:
        try:
            n_quarters = int(args[0])
        except ValueError:
            print(f"❌ 第一个参数必须是季度数,得到: {args[0]}")
            return
    if len(args) >= 2:
        only_key = args[1]
        if only_key not in CONFIG["investors"]:
            print(f"❌ 未知大师 key: {only_key}")
            print(f"   可选: {', '.join(CONFIG['investors'].keys())}")
            return

    print(f"\n🚀 开始回溯抓取 (回溯 {n_quarters} 季度, "
          f"对象: {only_key if only_key else '全部 5 位'})")
    print(f"   归档目录: {CONFIG['history_dir']}/")

    total_saved = 0
    for key, info in CONFIG["investors"].items():
        if only_key and key != only_key:
            continue
        try:
            total_saved += backfill_investor(key, info, n_quarters)
        except Exception as e:
            print(f"   ❌ {key} 处理异常: {e}")

    print(f"\n{'='*60}")
    print(f"✅ 回溯完成. 本次新增归档文件: {total_saved}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
