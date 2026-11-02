"""
Demo mode: a fictional monthly investor, shaped exactly like the broker's API
payloads, so the dashboard can run (and be screenshotted) without an account.

Nothing here is real account data. Tickers are real public instruments; the
quantities, prices paid, deposits and dividends are invented. Generation is
deterministic (fixed seed) apart from a small live price wobble.
"""

import csv
import math
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

FX_TO_GBP = {"GBP": 1.0, "GBX": 0.01, "USD": 0.79, "EUR": 0.86}

# ticker, name, short name, isin, quote ccy, type, quantity, avg price paid,
# current price, quantity held inside the pie
INSTRUMENTS = [
    ("VUAGl_EQ", "Vanguard S&P 500 (Acc)", "VUAG", "IE00BFMXXD54", "GBP", "ETF", 14.20, 88.40, 109.80, 0.0),
    ("VWRPl_EQ", "Vanguard FTSE All-World (Acc)", "VWRP", "IE00BK5BQT80", "GBP", "ETF", 9.10, 112.30, 140.70, 0.0),
    ("VUSAl_EQ", "Vanguard S&P 500 (Dist)", "VUSA", "IE00B3XXRP09", "GBP", "ETF", 2.40, 92.10, 104.20, 0.0),
    ("VHYLl_EQ", "Vanguard FTSE All-World High Dividend Yield (Dist)", "VHYL", "IE00B8GKDB10", "GBP", "ETF", 8.30, 61.20, 69.30, 0.0),
    ("SGLNl_EQ", "iShares Physical Gold", "SGLN", "IE00B4ND3602", "GBX", "ETF", 4.10, 4120.0, 5310.0, 0.0),
    ("EGLNl_EQ", "iShares Physical Gold", "EGLN", "IE00B4ND3602", "GBX", "ETF", 0.90, 4980.0, 5310.0, 0.0),
    ("MSFT_US_EQ", "Microsoft", "MSFT", "US5949181045", "USD", "STOCK", 1.12, 401.50, 522.90, 0.22),
    ("AAPL_US_EQ", "Apple", "AAPL", "US0378331005", "USD", "STOCK", 1.30, 214.80, 345.10, 0.0),
    ("NVDA_US_EQ", "NVIDIA", "NVDA", "US67066G1040", "USD", "STOCK", 2.05, 131.20, 118.40, 0.0),
    ("O_US_EQ", "Realty Income", "O", "US7561091049", "USD", "STOCK", 6.40, 59.10, 56.90, 1.10),
    ("BPl_EQ", "BP", "BP", "GB0007980591", "GBX", "STOCK", 48.0, 455.0, 627.4, 6.0),
    ("LGENl_EQ", "Legal & General", "LGEN", "GB0005603997", "GBX", "STOCK", 30.0, 236.0, 248.0, 30.0),
    ("ULVRl_EQ", "Unilever", "ULVR", "GB00B10RZP78", "GBX", "STOCK", 3.2, 4410.0, 4795.0, 3.2),
    ("KO_US_EQ", "Coca-Cola", "KO", "US1912161007", "USD", "STOCK", 3.9, 61.40, 70.60, 3.9),
    ("PG_US_EQ", "Procter & Gamble", "PG", "US7427181091", "USD", "STOCK", 1.4, 158.20, 171.00, 1.4),
    ("JNJ_US_EQ", "Johnson & Johnson", "JNJ", "US4781601046", "USD", "STOCK", 1.5, 152.90, 161.30, 1.5),
    ("PEP_US_EQ", "PepsiCo", "PEP", "US7134481081", "USD", "STOCK", 1.6, 166.00, 148.20, 1.6),
    ("SAPd_EQ", "SAP", "SAP", "DE0007164600", "EUR", "STOCK", 0.9, 151.0, 211.3, 0.9),
]
PIE_ID = 1001
PIE_NAME = "Dividend Growth Pie"
PIE_CASH = 1.20
FREE_CASH = 42.10
SEED = 212


def _wobble(ticker: str) -> float:
    """Small, smooth live price movement so the session chart has a shape."""
    t = datetime.now().timestamp()
    phase = sum(map(ord, ticker)) % 17
    return 1 + 0.004 * math.sin(t / 240 + phase) + 0.002 * math.sin(t / 37 + phase * 2)


def _positions(live: bool = True) -> list:
    out = []
    for ticker, name, short, isin, ccy, kind, qty, avg, price, in_pie in INSTRUMENTS:
        cur = price * (_wobble(ticker) if live else 1.0)
        fx = FX_TO_GBP[ccy]
        value = qty * cur * fx
        cost = qty * avg * fx
        out.append({
            "instrument": {"ticker": ticker, "name": name, "isin": isin, "currency": ccy},
            "createdAt": "2025-01-27T10:00:00Z",
            "quantity": qty,
            "quantityAvailableForTrading": qty,
            "quantityInPies": in_pie,
            "currentPrice": round(cur, 4),
            "averagePricePaid": avg,
            "walletImpact": {
                "currency": "GBP",
                "totalCost": round(cost, 2),
                "currentValue": round(value, 2),
                "unrealizedProfitLoss": round(value - cost, 2),
                "fxImpact": 0.0,
            },
        })
    return out


def _summary(positions: list) -> dict:
    value = sum(p["walletImpact"]["currentValue"] for p in positions)
    cost = sum(p["walletImpact"]["totalCost"] for p in positions)
    return {
        "id": 0,
        "currency": "GBP",
        "totalValue": round(value + FREE_CASH + PIE_CASH, 2),
        "cash": {"free": FREE_CASH},
        "investments": {"totalCost": round(cost, 2), "unrealizedProfitLoss": round(value - cost, 2)},
    }


def _pie(positions: list) -> dict:
    value = cost = 0.0
    for p in positions:
        share = p["quantityInPies"] / p["quantity"] if p["quantity"] else 0
        value += p["walletImpact"]["currentValue"] * share
        cost += p["walletImpact"]["totalCost"] * share
    return {
        "id": PIE_ID,
        "cash": PIE_CASH,
        "dividendDetails": {"gained": 6.4, "reinvested": 0, "inCash": 0},
        "result": {
            "priceAvgInvestedValue": round(cost, 2),
            "priceAvgValue": round(value, 2),
            "priceAvgResult": round(value - cost, 2),
            "priceAvgResultCoef": round((value - cost) / cost, 4) if cost else 0,
        },
        "progress": None,
        "status": None,
    }


def _pie_detail(positions: list) -> dict:
    slices = [p for p in positions if p["quantityInPies"] > 0]
    values = {p["instrument"]["ticker"]: p["walletImpact"]["currentValue"] * p["quantityInPies"] / p["quantity"] for p in slices}
    total = sum(values.values()) or 1
    target = 1 / len(slices)
    return {
        "settings": {"id": PIE_ID, "name": PIE_NAME, "icon": "Coins", "dividendCashAction": "TO_ACCOUNT_CASH"},
        "instruments": [
            {"ticker": t, "expectedShare": round(target, 4), "currentShare": round(v / total, 4), "ownedQuantity": 0, "issues": []}
            for t, v in values.items()
        ],
    }


def _schedule_id(ticker: str) -> int:
    """1 = US, 2 = London, 3 = Frankfurt (by ticker suffix)."""
    return 1 if ticker.endswith("_US_EQ") else (3 if ticker.endswith("d_EQ") else 2)


def _instruments() -> list:
    return [
        {"ticker": t, "type": kind, "isin": isin, "currencyCode": ccy, "name": name, "shortName": short,
         "workingScheduleId": _schedule_id(t)}
        for t, name, short, isin, ccy, kind, *_ in INSTRUMENTS
    ]


def _exchanges(today: date) -> list:
    """Weekday trading sessions (UTC) around today, in the API's shape."""
    sessions = {
        1: ("US", [("PRE_MARKET_OPEN", 9, 0), ("OPEN", 14, 30), ("CLOSE", 21, 0), ("AFTER_HOURS_OPEN", 21, 0), ("AFTER_HOURS_CLOSE", 25, 0)]),
        2: ("London", [("OPEN", 8, 0), ("CLOSE", 16, 30)]),
        3: ("Frankfurt", [("OPEN", 7, 0), ("CLOSE", 15, 30)]),
    }
    out = []
    for sid, (name, events) in sessions.items():
        times = []
        for offset in range(-3, 8):
            day = today + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            for kind, hour, minute in events:
                when = datetime(day.year, day.month, day.day) + timedelta(hours=hour, minutes=minute)
                times.append({"date": when.strftime("%Y-%m-%dT%H:%M:00.000Z"), "type": kind})
        out.append({"id": sid, "name": name, "workingSchedules": [{"id": sid, "timeEvents": times}]})
    return out


def _deposit_schedule(today: date) -> list:
    """Monthly deposits from Jan 2025 with a couple of bigger months and one gap."""
    rng = random.Random(SEED)
    flows = []
    y, m = 2025, 1
    while (y, m) <= (today.year, today.month):
        if (y, m) != (2025, 8):  # skipped a month
            amount = 250.0 + (150 if m in (4, 12) else 0)
            day = min(25 + rng.randint(-3, 3), 28)
            d = date(y, m, day)
            if d <= today:
                flows.append((d, amount))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return flows


def _transactions(today: date) -> list:
    """Newest first, like the API. Deposits scaled so money in ≈ cost + cash."""
    positions = _positions(live=False)
    target = sum(p["walletImpact"]["totalCost"] for p in positions) + FREE_CASH + PIE_CASH + 25.0
    deposits = _deposit_schedule(today)
    scale = target / sum(a for _, a in deposits)
    rows = [{"type": "DEPOSIT", "amount": round(a * scale / 5) * 5, "currency": "GBP", "reference": f"demo-dep-{i}", "dateTime": f"{d.isoformat()}T09:30:00Z"} for i, (d, a) in enumerate(deposits)]
    rows.append({"type": "WITHDRAW", "amount": -25.0, "currency": "GBP", "reference": "demo-wd", "dateTime": "2025-11-03T12:00:00Z"})
    d = date(2025, 2, 1)
    i = 0
    while d <= today:
        rows.append({"type": "INTEREST_ON_FREE_CASH", "amount": round(0.08 + 0.01 * (i % 5), 2), "currency": "GBP", "reference": f"demo-int-{i}", "dateTime": f"{d.isoformat()}T01:00:00Z"})
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        i += 1
    return sorted(rows, key=lambda r: r["dateTime"], reverse=True)


def _orders(today: date) -> list:
    """One or two filled buys per deposit, with realistic FX and stamp duty."""
    rng = random.Random(SEED + 1)
    rows = []
    buys = [i for i in INSTRUMENTS if i[0] != "EGLNl_EQ"]
    for n, (d, amount) in enumerate(_deposit_schedule(today)):
        for k in range(2):
            ticker, name, short, isin, ccy, kind, qty, avg, price, _ = buys[(n * 2 + k) % len(buys)]
            value = round(amount * (0.6 if k == 0 else 0.4), 2)
            fill_price = round(avg * (0.9 + 0.2 * rng.random()), 2)
            taxes = []
            if ccy in ("USD", "EUR"):
                taxes.append({"name": "CURRENCY_CONVERSION_FEE", "quantity": round(value * 0.0015, 2), "timeCharged": d.isoformat()})
            elif kind == "STOCK":
                taxes.append({"name": "STAMP_DUTY_RESERVE_TAX", "quantity": round(value * 0.005, 2), "timeCharged": d.isoformat()})
            when = f"{d.isoformat()}T14:{10 + k:02d}:00Z"
            rows.append({
                "order": {
                    "id": 900000 + n * 2 + k, "strategy": "VALUE", "type": "MARKET", "ticker": ticker,
                    "status": "FILLED", "value": value, "filledValue": value, "currency": "GBP",
                    "extendedHours": False, "initiatedFrom": "APP", "side": "BUY", "createdAt": when,
                    "instrument": {"ticker": ticker, "name": name, "isin": isin, "currency": ccy},
                },
                "fill": {
                    "id": 800000 + n * 2 + k,
                    "quantity": round(value / (fill_price * FX_TO_GBP[ccy]), 6),
                    "price": fill_price, "type": "TRADE", "tradingMethod": "TOTV", "filledAt": when,
                    "walletImpact": {"currency": "GBP", "netValue": value, "fxRate": 1 / FX_TO_GBP[ccy], "taxes": taxes},
                },
            })
    return sorted(rows, key=lambda r: r["order"]["createdAt"], reverse=True)


def _dividends(today: date) -> list:
    payers = {  # ticker: (name, months paid, amount per payment)
        "O_US_EQ": ("Realty Income", range(1, 13), 0.95),
        "VHYLl_EQ": ("Vanguard FTSE All-World High Dividend Yield (Dist)", (3, 6, 9, 12), 3.10),
        "BPl_EQ": ("BP", (3, 6, 9, 12), 2.85),
        "LGENl_EQ": ("Legal & General", (6, 9), 3.40),
        "ULVRl_EQ": ("Unilever", (3, 6, 9, 12), 1.60),
        "KO_US_EQ": ("Coca-Cola", (4, 7, 10, 12), 1.20),
        "PG_US_EQ": ("Procter & Gamble", (2, 5, 8, 11), 1.05),
        "JNJ_US_EQ": ("Johnson & Johnson", (3, 6, 9, 12), 1.10),
        "PEP_US_EQ": ("PepsiCo", (1, 3, 6, 9), 1.15),
        "MSFT_US_EQ": ("Microsoft", (3, 6, 9, 12), 0.70),
        "VUSAl_EQ": ("Vanguard S&P 500 (Dist)", (3, 6, 9, 12), 0.45),
    }
    rows = []
    d = date(2025, 3, 1)
    while d <= today:
        for ticker, (name, months, amount) in payers.items():
            if d.month in months:
                paid = d.replace(day=10 + (sum(map(ord, ticker)) % 15))
                if paid <= today:
                    growth = 1 + 0.15 * (paid - date(2025, 3, 1)).days / 365  # holdings grew over time
                    rows.append({
                        "ticker": ticker, "instrument": {"ticker": ticker, "name": name}, "reference": f"demo-div-{ticker}-{paid}",
                        "quantity": 1.0, "amount": round(amount * growth, 2), "currency": "GBP",
                        "grossAmountPerShare": 0.0, "amountInEuro": 0.0, "paidOn": f"{paid.isoformat()}T12:00:00Z", "type": "DIVIDEND",
                    })
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return sorted(rows, key=lambda r: r["paidOn"], reverse=True)


class DemoClient:
    """Stands in for BrokerClient with the same method signatures."""

    base_url = "demo"

    def __init__(self):
        self.last_error = None
        self.today = date.today()

    def account_summary(self):
        return _summary(_positions())

    def positions(self):
        return _positions()

    def instruments(self):
        return _instruments()

    def exchanges(self):
        return _exchanges(self.today)

    def pies(self):
        return [_pie(_positions())]

    def pie_details(self, pies):
        return {PIE_ID: _pie_detail(_positions())} if pies else {}

    def history_orders(self, limit=None, retries=0):
        return _orders(self.today)[:limit]

    def history_dividends(self, limit=None, retries=0):
        return _dividends(self.today)[:limit]

    def history_transactions(self, limit=None, retries=0):
        return _transactions(self.today)[:limit]


def write_demo_snapshots(directory: Path, days: int = 120) -> None:
    """Daily snapshot history ending yesterday: deposits plus a market path."""
    rng = random.Random(SEED + 2)
    today = date.today()
    end_value = _summary(_positions(live=False))["totalValue"]
    flows = {d: a for d, a in ((datetime.fromisoformat(t["dateTime"].replace("Z", "")).date(), t["amount"]) for t in _transactions(today) if t["type"] in ("DEPOSIT", "WITHDRAW"))}
    start = today - timedelta(days=days)

    # Market index with drift, one drawdown and recovery; values = contributions so far × index
    index, path = 1.0, []
    for i in range(days):
        shock = -0.012 if 55 <= i < 63 else 0.0
        index *= 1 + 0.0007 + shock + rng.gauss(0, 0.006)
        path.append(index)
    contributed = sum(a for d, a in flows.items() if d < start)
    raw = []
    for i in range(days):
        d = start + timedelta(days=i)
        contributed += flows.get(d, 0.0)
        raw.append((d, contributed * path[i]))
    scale = end_value / raw[-1][1]

    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "portfolio_history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "timestamp", "total_value", "invested", "pnl", "pnl_pct", "cash", "positions_count", "win_rate"])
        for d, v in raw:
            v *= scale
            w.writerow([d.isoformat(), f"{d.isoformat()}T09:00:00", f"{v:.2f}", "", "", "", f"{FREE_CASH:.2f}", len(INSTRUMENTS), ""])
    with open(directory / "positions_history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "ticker", "name", "value", "cost", "pnl", "pnl_pct", "weight", "currency"])


def demo_environment():
    """(client, snapshot dir, config path) in a throwaway temp folder, so demo
    runs never touch your real snapshots or portfolio_config.json."""
    import json

    root = Path(tempfile.mkdtemp(prefix="next-deposit-demo-"))
    write_demo_snapshots(root / "snapshots")
    # Yesterday's per-holding close, so the Daily change panel can show movers
    rng = random.Random(SEED + 3)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    closes = {
        p["instrument"]["ticker"]: {
            "value": round(p["walletImpact"]["currentValue"] * (1 - rng.uniform(-0.015, 0.015)), 4),
            "cost": p["walletImpact"]["totalCost"],
        }
        for p in _positions(live=False)
    }
    (root / "snapshots" / "positions_eod.json").write_text(json.dumps({yesterday: closes}))
    config = root / "portfolio_config.json"
    config.write_text(json.dumps({
        "monthly_deposit": 250,
        "is_isa": True,
        "isa_allowance": 20000,
        "targets_pct": {PIE_NAME: 25, "VWRP": 35, "VUAG": 25, "VHYL": 10, "SGLN": 5},
    }, indent=2))
    return DemoClient(), root / "snapshots", config
