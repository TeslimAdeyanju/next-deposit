"""
Portfolio insights for a regular (monthly) investor.

Pure functions over broker API payloads and snapshot history: no API
calls and no rendering, so every number here can be checked in isolation.
"""

import json
import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

CONFIG_FILE = Path("portfolio_config.json")

# Transaction types that move your own money in or out of the account
CONTRIBUTION_TYPES = {"DEPOSIT", "TRANSFER", "WITHDRAW", "WITHDRAWAL"}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _last_n_months(n: int, today: date = None) -> list:
    """['2025-12', ..., '2026-11'] — the n calendar months ending this month."""
    today = today or date.today()
    y, m = today.year, today.month
    keys = []
    for _ in range(n):
        keys.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return keys[::-1]


# ── Config ─────────────────────────────────────────────────────────────


def load_config(holdings: list, path: Path = None) -> dict:
    """Read portfolio_config.json, creating it on first run.

    The first version uses your current weights as targets, so the planner
    starts as "keep the current mix" until you edit the file.
    """
    path = path or CONFIG_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            pass
    total = sum(h["_value"] for h in holdings) or 1
    targets = {
        h["_symbol"]: round(h["_value"] / total * 100)
        for h in sorted(holdings, key=lambda h: -h["_value"])
        if h["_value"] / total >= 0.01
    }
    config = {
        "monthly_deposit": 100,
        "is_isa": True,
        "isa_allowance": 20000,
        "targets_pct": targets,
        "_help": (
            "targets_pct: target weight per holding (ticker, or pie name). "
            "Holdings left out get no new money. Weights are rescaled to "
            "100%. Run with --invest AMOUNT to plan a one-off deposit."
        ),
    }
    try:
        path.write_text(json.dumps(config, indent=2) + "\n")
    except OSError:
        pass
    return config


# ── Planner ────────────────────────────────────────────────────────────


def _normalised(targets_pct: dict) -> dict:
    weights = {k: float(v) for k, v in (targets_pct or {}).items() if float(v or 0) > 0}
    total = sum(weights.values()) or 1
    return {k: v / total for k, v in weights.items()}


def auto_buys(holdings: list, targets_pct: dict, amount: float) -> dict:
    """Split a new deposit to move toward target weights without selling.

    Money goes first to the holdings furthest below target; whatever is left
    once everything is at target is split by target weight.
    """
    by_key = {h["_symbol"]: h for h in holdings}
    targets = _normalised(targets_pct)
    new_total = sum(h["_value"] for h in holdings) + amount
    shortfall = {
        k: max(0.0, t * new_total - (by_key[k]["_value"] if k in by_key else 0.0))
        for k, t in targets.items()
    }
    gap = sum(shortfall.values())
    if gap >= amount and gap > 0:
        return {k: amount * s / gap for k, s in shortfall.items()}
    leftover = amount - gap
    return {k: shortfall[k] + leftover * targets[k] for k in targets}


def plan_rows(holdings: list, targets_pct: dict, buys: dict) -> dict:
    """Per-holding now / target / buy / after table for a set of buys."""
    by_key = {h["_symbol"]: h for h in holdings}
    targets = _normalised(targets_pct)
    current_total = sum(h["_value"] for h in holdings)
    amount = sum(v for v in buys.values() if v > 0)
    new_total = current_total + amount

    rows = []
    keys = set(by_key) | set(targets) | {k for k, v in buys.items() if v > 0}
    for key in sorted(keys, key=lambda k: -(by_key[k]["_value"] if k in by_key else 0)):
        h = by_key.get(key)
        value = h["_value"] if h else 0.0
        buy = max(0.0, buys.get(key, 0.0))
        now = value / current_total * 100 if current_total else 0
        rows.append({
            "key": key,
            "name": h["_name"] if h else key,
            "is_pie": bool(h and h["_is_pie"]),
            "missing": h is None,  # target or buy for something you don't hold
            "value": value,
            "current_pct": now,
            "target_raw": float((targets_pct or {}).get(key) or 0),
            "target_pct": targets.get(key, 0) * 100,
            "drift_pct": now - targets.get(key, 0) * 100,
            "buy": buy,
            "after_value": value + buy,
            "after_pct": (value + buy) / new_total * 100 if new_total else 0,
        })
    return {
        "amount": amount,
        "rows": rows,
        "untargeted": [k for k in by_key if k not in targets],
        "targets_sum": sum(float(v or 0) for v in (targets_pct or {}).values()),
    }


def plan_deposit(holdings: list, targets_pct: dict, amount: float) -> dict:
    return plan_rows(holdings, targets_pct, auto_buys(holdings, targets_pct, amount))


def pie_slices(pie_detail: dict) -> list:
    """Pie slices with target vs current share, most underweight first."""
    rows = []
    for inst in (pie_detail or {}).get("instruments", []):
        target = (inst.get("expectedShare") or 0) * 100
        current = (inst.get("currentShare") or 0) * 100
        rows.append({"ticker": inst.get("ticker", ""), "target_pct": target, "current_pct": current, "drift_pct": current - target})
    return sorted(rows, key=lambda r: r["drift_pct"])


# ── Contributions & performance ────────────────────────────────────────


def contributions(transactions: list) -> list:
    """[(date, amount)] of your own money in (+) and out (-), oldest first."""
    flows = []
    for t in transactions or []:
        kind = (t.get("type") or "").upper()
        if kind not in CONTRIBUTION_TYPES:
            continue
        amount = float(t.get("amount") or 0)
        if kind.startswith("WITHDRAW"):
            amount = -abs(amount)
        flows.append((_parse_dt(t["dateTime"]).date(), amount))
    return sorted(flows)


def monthly_contributions(flows: list, months: int = 12, today: date = None) -> list:
    totals = defaultdict(float)
    for d, amount in flows:
        totals[_month_key(d)] += amount
    return [(k, totals.get(k, 0.0)) for k in _last_n_months(months, today)]


def contribution_streak(flows: list, today: date = None) -> int:
    """Consecutive calendar months with a net deposit, counting back from
    this month (or last month, if nothing has gone in yet this month)."""
    months = {_month_key(d) for d, a in flows if a > 0}
    keys = _last_n_months(240, today)[::-1]
    if keys and keys[0] not in months:
        keys = keys[1:]
    streak = 0
    for k in keys:
        if k not in months:
            break
        streak += 1
    return streak


def tax_year_start(today: date = None) -> date:
    """UK tax years run 6 April – 5 April."""
    today = today or date.today()
    start = date(today.year, 4, 6)
    return start if today >= start else date(today.year - 1, 4, 6)


def isa_used(transactions: list, today: date = None) -> float:
    """Deposits this tax year. Transfers are excluded: an ISA transfer-in
    does not use the allowance (check your own transfers if unsure)."""
    start = tax_year_start(today)
    return sum(
        float(t.get("amount") or 0)
        for t in transactions or []
        if (t.get("type") or "").upper() == "DEPOSIT" and _parse_dt(t["dateTime"]).date() >= start
    )


def xirr(flows: list):
    """Annualised money-weighted return. `flows` = [(date, amount)] from the
    investor's view: deposits negative, final value positive. None if it
    can't be solved (e.g. under ~30 days of history)."""
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)
    if (max(d for d, _ in flows) - t0).days < 30:
        return None

    def npv(rate):
        return sum(a / (1 + rate) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):  # bisection: slow but cannot diverge
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def performance(transactions: list, total_value: float, today: date = None) -> dict:
    flows = contributions(transactions)
    net_in = sum(a for _, a in flows)
    deposited = sum(a for _, a in flows if a > 0)
    withdrawn = -sum(a for _, a in flows if a < 0)
    today = today or date.today()
    rate = xirr([(d, -a) for d, a in flows] + [(today, total_value)])
    return {
        "net_contributed": net_in,
        "deposited": deposited,
        "withdrawn": withdrawn,
        "growth": total_value - net_in,
        "growth_pct": (total_value - net_in) / net_in * 100 if net_in else 0,
        "xirr": rate,
        "first_deposit": flows[0][0] if flows else None,
        "interest": sum(
            float(t.get("amount") or 0)
            for t in transactions or []
            if (t.get("type") or "").upper().startswith("INTEREST")
        ),
    }


# ── Costs ──────────────────────────────────────────────────────────────


def fees(orders: list) -> dict:
    """Fees paid on fills, by type, in the account currency."""
    by_type = defaultdict(float)
    traded = 0.0
    for item in orders or []:
        fill = item.get("fill") or {}
        impact = fill.get("walletImpact") or {}
        traded += abs(float(impact.get("netValue") or 0))
        for tax in impact.get("taxes") or []:
            by_type[tax.get("name") or "OTHER"] += abs(float(tax.get("quantity") or 0))
    total = sum(by_type.values())
    return {"by_type": dict(by_type), "total": total, "traded": traded, "pct_of_traded": total / traded * 100 if traded else 0}


FEE_LABELS = {
    "CURRENCY_CONVERSION_FEE": "FX conversion (0.15%)",
    "STAMP_DUTY_RESERVE_TAX": "UK stamp duty (0.5%)",
}


# ── Dividends ──────────────────────────────────────────────────────────


def dividend_summary(dividends: list, invested: float, today: date = None) -> dict:
    today = today or date.today()
    year_ago = today - timedelta(days=365)
    by_month = defaultdict(float)
    by_ticker = defaultdict(float)
    ttm = 0.0
    total = 0.0
    for d in dividends or []:
        amount = float(d.get("amount") or 0)
        paid = _parse_dt(d.get("paidOn") or d.get("date")).date()
        total += amount
        by_month[_month_key(paid)] += amount
        if paid >= year_ago:
            ttm += amount
            by_ticker[d.get("ticker", "")] += amount
    return {
        "ttm": ttm,
        "all_time": total,
        "count": len(dividends or []),
        "yield_on_cost": ttm / invested * 100 if invested else 0,
        "monthly": [(k, by_month.get(k, 0.0)) for k in _last_n_months(12, today)],
        "top_payers": sorted(by_ticker.items(), key=lambda kv: -kv[1]),
    }


# ── Structure: overlap & fragmentation ─────────────────────────────────


def _index_family(name: str):
    n = name.lower()
    if "s&p 500" in n:
        return "S&P 500 (sector slice)" if re.search(r"sector|information technology|financials|health", n) else "S&P 500"
    if "all-world" in n or "all world" in n:
        return "FTSE All-World (high dividend)" if "dividend" in n else "FTSE All-World"
    if "msci world" in n or "msci acwi" in n:
        return "MSCI World / ACWI"
    if "gold" in n:
        return "Gold"
    return None


def overlap_groups(positions: list) -> list:
    """Funds that hold the same thing: identical ISIN, or the same index."""
    groups = defaultdict(list)
    for p in positions:
        inst = p.get("instrument", {})
        family = _index_family(inst.get("name", ""))
        if family:
            groups[family].append(p)
    by_isin = defaultdict(list)
    for p in positions:
        isin = p.get("instrument", {}).get("isin")
        if isin:
            by_isin[isin].append(p)

    out = []
    same_fund = {frozenset(id(m) for m in ms) for ms in by_isin.values() if len(ms) > 1}
    for family, members in groups.items():
        if len(members) > 1 and frozenset(id(m) for m in members) not in same_fund:
            out.append({"label": family, "members": members, "value": sum(m["_value"] for m in members), "same_isin": False})
    for isin, members in by_isin.items():
        if len(members) > 1:
            out.append({"label": f"Same fund, two listings ({isin})", "members": members, "value": sum(m["_value"] for m in members), "same_isin": True})
    return sorted(out, key=lambda g: -g["value"])


def fragmentation(positions: list, threshold_pct: float = 1.0) -> dict:
    total = sum(p["_value"] for p in positions) or 1
    small = [p for p in positions if p["_value"] / total * 100 < threshold_pct]
    top5 = sorted((p["_value"] for p in positions), reverse=True)[:5]
    # Effective number of holdings = 1 / sum(w^2) (inverse Herfindahl)
    hhi = sum((p["_value"] / total) ** 2 for p in positions)
    return {
        "count": len(positions),
        "small_count": len(small),
        "small_value": sum(p["_value"] for p in small),
        "small_pct": sum(p["_value"] for p in small) / total * 100,
        "top5_pct": sum(top5) / total * 100,
        "effective_n": 1 / hhi if hhi else 0,
    }


# ── Risk (from daily snapshots) ────────────────────────────────────────


def risk_metrics(daily_values: list, flows: list) -> dict:
    """Drawdown and volatility from daily snapshots, with deposits removed
    so that adding money doesn't count as a gain."""
    if len(daily_values) < 2:
        return {"days": len(daily_values)}
    flow_by_day = defaultdict(float)
    for d, a in flows:
        flow_by_day[d.isoformat()] += a

    returns = []
    index = [1.0]  # growth of £1, deposit-adjusted
    for (d_prev, v_prev), (d, v) in zip(daily_values, daily_values[1:]):
        if v_prev <= 0:
            continue
        r = (v - v_prev - flow_by_day.get(d, 0.0)) / v_prev
        returns.append((d, r))
        index.append(index[-1] * (1 + r))

    peak, max_dd = index[0], 0.0
    for x in index:
        peak = max(peak, x)
        max_dd = min(max_dd, x / peak - 1)

    out = {
        "days": len(daily_values),
        "max_drawdown_pct": max_dd * 100,
        "current_drawdown_pct": (index[-1] / max(index) - 1) * 100,
        "period_return_pct": (index[-1] - 1) * 100,
        "best_day": max(returns, key=lambda x: x[1]) if returns else None,
        "worst_day": min(returns, key=lambda x: x[1]) if returns else None,
    }
    if len(returns) >= 10:
        out["volatility_pct"] = statistics.stdev(r for _, r in returns) * math.sqrt(252) * 100
    return out


# ── Value schedule ──────────────────────────────────────────────────────


def value_schedule(daily_rows: list, flows: list) -> dict:
    """Day-by-day movement of total value, newest first.

    Each change is split into money in (deposits/withdrawals since the
    previous row, up to and including this date) and market move (the rest).
    """
    rows = []
    for prev, cur in zip(daily_rows, daily_rows[1:]):
        money_in = sum(a for d, a in flows if prev["date"] < d.isoformat() <= cur["date"])
        change = cur["total_value"] - prev["total_value"]
        rows.append({
            **cur,
            "change": change,
            "change_pct": change / prev["total_value"] * 100 if prev["total_value"] else 0,
            "money_in": money_in,
            "market_move": change - money_in,
            "gap_days": (date.fromisoformat(cur["date"]) - date.fromisoformat(prev["date"])).days,
        })
    if daily_rows:  # the first day has nothing to compare with
        rows.insert(0, {**daily_rows[0], "change": None, "change_pct": None, "money_in": None, "market_move": None, "gap_days": 0})
    rows.reverse()  # newest first; the first day ends up last

    summary = None
    # Best/worst only among consecutive-ish days, not multi-day gaps
    daily_moves = [r for r in rows[:-1] if r["gap_days"] <= 3]
    comparable = len(daily_moves) >= 2  # best vs worst needs two days to compare
    if len(daily_rows) >= 2:
        first, last = daily_rows[0], daily_rows[-1]
        money_in = sum(a for d, a in flows if first["date"] < d.isoformat() <= last["date"])
        change = last["total_value"] - first["total_value"]
        summary = {
            "from": first["date"], "to": last["date"], "days": len(daily_rows),
            "change": change, "money_in": money_in, "market_move": change - money_in,
            "market_pct": (change - money_in) / first["total_value"] * 100 if first["total_value"] else 0,
            "best": max(daily_moves, key=lambda r: r["market_move"]) if comparable else None,
            "worst": min(daily_moves, key=lambda r: r["market_move"]) if comparable else None,
            "daily_moves": len(daily_moves),
        }
    return {"rows": rows, "summary": summary}


# ── Scenario impact ────────────────────────────────────────────────────

FX_FEE_RATE = 0.0015  # broker FX conversion fee on non-GBP instruments
STAMP_DUTY_RATE = 0.005  # UK shares (not ETFs)


def save_targets(targets_pct: dict, path: Path = None) -> None:
    """Write targets back to the config, keeping every other setting."""
    path = path or CONFIG_FILE
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
    except (ValueError, OSError):
        config = {}
    config["targets_pct"] = {k: v for k, v in targets_pct.items() if v}
    path.write_text(json.dumps(config, indent=2) + "\n")


def _composition(positions: list, holdings: list) -> dict:
    """{holding key: [(position, share of that holding's money)]}.

    A direct holding maps to its own position; a pie maps to the positions
    held inside pies, weighted by their in-pie value (look-through).
    """
    comp = {}
    in_pie = []
    for p in positions:
        qty = p.get("quantity") or 0
        share = (p.get("quantityInPies") or 0) / qty if qty else 0
        if share > 0:
            in_pie.append((p, p["_value"] * share))
    pie_total = sum(v for _, v in in_pie) or 1
    for h in holdings:
        if h["_is_pie"]:
            comp[h["_symbol"]] = [(p, v / pie_total) for p, v in in_pie]
        else:
            comp[h["_symbol"]] = [(p, 1.0) for p in positions if p["_symbol"] == h["_symbol"]][:1]
    return comp


def _mix(values: dict, positions_by_id: dict, field: str) -> dict:
    out = defaultdict(float)
    for pid, v in values.items():
        out[positions_by_id[pid][field]] += v
    total = sum(out.values()) or 1
    return {k: v / total * 100 for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def scenario_impact(positions: list, holdings: list, buys: dict) -> dict:
    """Before/after look-through metrics and estimated costs for `buys`
    ({holding key: £ amount})."""
    by_id = {id(p): p for p in positions}
    before = {id(p): p["_value"] for p in positions}
    after = dict(before)
    comp = _composition(positions, holdings)
    fx = stamp = 0.0
    unmapped = []
    for key, amount in buys.items():
        if amount <= 0:
            continue
        parts = comp.get(key)
        if not parts:
            unmapped.append(key)
            continue
        for p, share in parts:
            slice_ = amount * share
            uk = p["_price_ccy"] in ("GBP", "GBX")
            slice_fx = 0.0 if uk else slice_ * FX_FEE_RATE
            slice_stamp = slice_ * STAMP_DUTY_RATE if uk and p["_type"] == "Stock" else 0.0
            fx += slice_fx
            stamp += slice_stamp
            after[id(p)] += slice_ - slice_fx - slice_stamp

    def summary(values):
        return {
            "total": sum(values.values()),
            "currency": _mix(values, by_id, "_currency"),
            "type": _mix(values, by_id, "_type"),
        }

    return {"before": summary(before), "after": summary(after), "fx_fee": fx, "stamp_duty": stamp, "unmapped": unmapped}
