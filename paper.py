"""模拟跟随组合(纸面交易)

规则(出场方式的依据见 backtest/exit_backtest.py):
  入场  买入类信号披露后第一个交易日收盘
  仓位  每条信号占组合 10%;同一只股票累计不超过 20%;最多同时持有 10 只
  出场  满 12 个月(252 个交易日)卖出;收盘价跌破入场价 25% 止损
  闲置资金放在标普 500(SPY),所以组合与标普的差距完全来自选股
每次运行都从头重算,不依赖上一次的结果。收益含分红。
"""

PARAMS = {"start": "2025-11-01", "live_from": "2026-11-24", "per_signal": 0.10, "per_stock_cap": 0.20,
          "max_names": 10, "hold_days": 252, "stop_loss": 0.25, "min_add": 0.01}


def simulate(signals, close, raw=None, params=PARAMS):
    """signals: follow.compute_signals 的全部信号;close: 含分红复权收盘价(含 SPY)"""
    import pandas as pd
    days = close.index[close.index >= pd.Timestamp(params["start"])]
    if len(days) < 2:
        return None
    by_day = {}
    for s in signals:
        if s["kind"] in ("NEW", "ADD") and s.get("entry_date") and s["entry_date"] >= params["start"]:
            by_day.setdefault(s["entry_date"], []).append(s)

    spy_val, lots, closed, skipped = 1.0, [], [], []
    nav_series, spy_series = [], []
    spy0 = float(close["SPY"][days[0]])
    prev = days[0]
    for d in days:
        if d != prev:      # 按当日涨跌更新市值
            spy_val *= float(close["SPY"][d] / close["SPY"][prev])
            for lot in lots:
                lot["value"] *= float(close[lot["entity"]][d] / close[lot["entity"]][prev])
                lot["age"] += 1
        # 出场
        for lot in list(lots):
            px = float(close[lot["entity"]][d])
            reason = ("止损" if px <= lot["entry_adj"] * (1 - params["stop_loss"])
                      else "满 12 个月" if lot["age"] >= params["hold_days"] else None)
            if reason:
                lots.remove(lot)
                spy_val += lot["value"]
                lot.update({"exit_date": str(d.date()), "exit_reason": reason,
                            "exit_px": _px(raw, close, lot["entity"], d),
                            "ret": px / lot["entry_adj"] - 1,
                            "spy_ret": float(close["SPY"][d]) / lot["spy_entry"] - 1})
                closed.append(lot)
        # 入场
        nav = spy_val + sum(l["value"] for l in lots)
        for s in sorted(by_day.get(str(d.date()), []), key=lambda s: -s["weight"]):
            ent = s["entity"]
            base = {"investor": s["investor"], "kind": s["kind"], "entity": ent, "key": s["key"],
                    "issuer": s["issuer"], "quarter": s["quarter"], "date": str(d.date())}
            if not ent or ent not in close.columns or close[ent][d] != close[ent][d]:
                skipped.append({**base, "why": "无行情数据"}); continue
            held = sum(l["value"] for l in lots if l["entity"] == ent)
            names = {l["entity"] for l in lots}
            if ent not in names and len(names) >= params["max_names"]:
                skipped.append({**base, "why": f"已持有 {params['max_names']} 只"}); continue
            room = params["per_stock_cap"] * nav - held
            add = min(params["per_signal"] * nav, room, spy_val)
            if add < params["min_add"] * nav:
                skipped.append({**base, "why": "该股已达 20% 上限" if room < params["min_add"] * nav else "没有可用资金"}); continue
            spy_val -= add
            lots.append({**base, "entry_date": str(d.date()), "entry_adj": float(close[ent][d]),
                         "entry_px": _px(raw, close, ent, d), "spy_entry": float(close["SPY"][d]),
                         "size": round(add / nav, 4), "value": add, "age": 0,
                         "signal_weight": s["weight"], "chg": s.get("chg"),
                         "trimmed": s.get("status") == "invalid", "trim_note": s.get("status_note")})
        nav_series.append(spy_val + sum(l["value"] for l in lots))
        spy_series.append(float(close["SPY"][d]) / spy0)
        prev = d

    last = days[-1]
    nav = nav_series[-1]
    for lot in lots:
        px = float(close[lot["entity"]][last])
        lot.update({"last_px": _px(raw, close, lot["entity"], last), "ret": px / lot["entry_adj"] - 1,
                    "spy_ret": float(close["SPY"][last]) / lot["spy_entry"] - 1,
                    "weight_now": lot["value"] / nav,
                    "stop_px": round(lot["entry_px"] * (1 - params["stop_loss"]), 2),
                    "days_left": params["hold_days"] - lot["age"]})
    peak, mdd, spy_peak, spy_mdd = 0.0, 0.0, 0.0, 0.0
    for a, b in zip(nav_series, spy_series):
        peak, spy_peak = max(peak, a), max(spy_peak, b)
        mdd, spy_mdd = min(mdd, a / peak - 1), min(spy_mdd, b / spy_peak - 1)
    done = closed + lots
    live_i = next((i for i, d in enumerate(days) if str(d.date()) >= params["live_from"]), None)
    clean = lambda lot: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in lot.items()
                         if k not in ("entry_adj", "spy_entry", "value", "age")}
    return {
        "params": params, "start": str(days[0].date()), "as_of": str(last.date()),
        "dates": [str(d.date()) for d in days], "nav": [round(v, 4) for v in nav_series],
        "spy": [round(v, 4) for v in spy_series],
        "stats": {"ret": nav - 1, "spy_ret": spy_series[-1] - 1, "excess": nav - spy_series[-1],
                  "mdd": mdd, "spy_mdd": spy_mdd, "invested": sum(l["value"] for l in lots) / nav,
                  "n_open": len(lots), "n_names": len({l["entity"] for l in lots}), "n_closed": len(closed),
                  "n_stopped": sum(1 for l in closed if l["exit_reason"] == "止损"),
                  "n_beat": sum(1 for l in done if l["ret"] > l["spy_ret"]), "n_total": len(done),
                  "live_ret": (nav / nav_series[live_i] - 1) if live_i else None,
                  "live_spy_ret": (spy_series[-1] / spy_series[live_i] - 1) if live_i else None},
        "open": [clean(l) for l in sorted(lots, key=lambda l: -l["value"])],
        "closed": [clean(l) for l in sorted(closed, key=lambda l: l["exit_date"], reverse=True)],
        "skipped": skipped,
    }


def _px(raw, close, ent, d):
    src = raw if raw is not None and ent in raw.columns else close
    return round(float(src[ent][d]), 2)
