"""
Next Deposit
============
A live terminal planner and monitor for a Stocks ISA / Invest account, using
the broker's public API: where the next deposit should go, and whether the portfolio is
performing, diversified and cost-efficient.

Requirements:
    pip install requests python-dotenv rich

Usage:
    1. Add your API credentials to a .env file (see .env.example)
    2. Run: python next_deposit.py   (or: python next_deposit.py --demo)

Author: Teslim Adeyanju
"""

import requests
import base64
import time
import os
import argparse
import io
import json
import select
import sys
import threading
import re
import csv
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import insights

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.live import Live
    from rich.columns import Columns
    from rich.align import Align
    from rich.console import Group
    from rich import box
    from rich.markup import escape

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from dotenv import load_dotenv

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────
API_KEY = os.getenv("BROKER_API_KEY", "")
API_SECRET = os.getenv("BROKER_API_SECRET", "")
BASE_URL = os.getenv("BROKER_BASE_URL", "https://live.trading212.com/api/v0")  # broker's public API endpoint
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "30"))
HISTORY_REFRESH_SECONDS = 1800  # full history (deposits, orders, dividends) changes rarely
SNAPSHOT_DIR = Path("snapshots")
TOP_N_POSITIONS = 15  # rows shown before the rest are rolled into "+N others"
CONCENTRATION_THRESHOLD = 25  # % weight that triggers a concentration alert
SNAPSHOT_DIR.mkdir(exist_ok=True)

# ── API Client ─────────────────────────────────────────────────────────


class BrokerClient:
    """Lightweight read-only client for the broker's public API (v0)."""

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.base_url = base_url
        creds = f"{api_key}:{api_secret}"
        encoded = base64.b64encode(creds.encode()).decode()
        self.headers = {"Authorization": f"Basic {encoded}"}
        self.last_error = None  # human-readable reason for the last failed call

    def _get(self, endpoint: str, params: dict = None):
        try:
            r = requests.get(
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            self.last_error = None
            return r.json()
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (401, 403):
                self.last_error = f"HTTP {code}: check BROKER_API_KEY / BROKER_API_SECRET in .env"
            elif code == 429:
                self.last_error = (
                    "Rate limited by the broker API — is another dashboard running "
                    "on this account?"
                )
            else:
                self.last_error = f"HTTP {code} from {endpoint}"
            return None
        except Exception as e:
            self.last_error = f"Network error: {e}"
            return None

    def account_summary(self):
        return self._get("/equity/account/summary")

    def positions(self):
        return self._get("/equity/positions")

    def instruments(self):
        return self._get("/equity/metadata/instruments")

    def pies(self):
        """Pie summaries (value, result, cash). Rate limit: 1 request / 30s."""
        return self._get("/equity/pies")

    def pie_details(self, pies: list):
        """{pie id: detail} for a pies() result. Detail holds the name and
        each slice's target vs current share. Rate limit: 1 request / 5s,
        so call this rarely; failed lookups are left out for a later retry."""
        details = {}
        for i, pie in enumerate(pies or []):
            if i:
                time.sleep(5)
            detail = self._get(f"/equity/pies/{pie['id']}")
            if detail and (detail.get("settings") or {}).get("name"):
                details[pie["id"]] = detail
        return details

    def history_orders(self, limit=None, retries=0):
        return self._paginate("/equity/history/orders", limit, retries)

    def history_dividends(self, limit=None, retries=0):
        return self._paginate("/equity/history/dividends", limit, retries)

    def history_transactions(self, limit=None, retries=0):
        return self._paginate("/equity/history/transactions", limit, retries)

    def _paginate(self, endpoint: str, limit: int = None, retries: int = 0):
        """Fetch pages until `limit` items (None = everything).

        `retries` > 0 waits and retries rate-limited pages (history endpoints
        allow ~6 requests/min); only use it off the UI thread. Returns None
        if the first page fails, else whatever was collected.
        """
        items = []
        path = f"{endpoint}?limit=50"
        while path:
            data = self._get(path)
            for _ in range(retries):
                if data is not None:
                    break
                time.sleep(11)
                data = self._get(path)
            if data is None:
                return items or None
            items.extend(data.get("items", []))
            if limit is not None and len(items) >= limit:
                return items[:limit]
            path = data.get("nextPagePath")
            # nextPagePath already includes the /api/v0 prefix of base_url
            prefix = "/" + self.base_url.split("://", 1)[-1].split("/", 1)[-1]
            if path and path.startswith(prefix):
                path = path[len(prefix):]
        return items


# ── Analytics Engine ───────────────────────────────────────────────────


def clean_ticker(ticker: str) -> str:
    """Strip broker ticker suffixes: AAPL_US_EQ -> AAPL, VUAGl_EQ -> VUAG.

    The trailing lowercase letter is the exchange (l = London, d = Xetra, ...).
    """
    return re.sub(r"[a-z]+$", "", ticker.replace("_US_EQ", "").replace("_EQ", ""))


class PortfolioAnalytics:
    """Computes analytics from raw position and account data.

    `instruments` is the /equity/metadata/instruments list (optional); it
    supplies each position's type (ETF/STOCK) and a clean short name.

    `pies` is the /equity/pies list (optional), with each pie's name under
    "name". When given, `holdings` shows each pie as one line and positions
    only by the quantity held outside pies — the same view as the app.
    Breakdowns, win rate and concentration still use the underlying positions.
    """

    def __init__(self, summary: dict, positions: list, instruments: list = None, pies: list = None):
        self.summary = summary
        self.positions = positions
        self.instruments = {i["ticker"]: i for i in (instruments or [])}
        self.pies = pies
        self._enrich_positions()
        self.holdings = self._build_holdings()

    def symbol(self, ticker: str) -> str:
        """Display name for a raw ticker, e.g. VUAGl_EQ -> VUAG."""
        meta = self.instruments.get(ticker, {})
        return meta.get("shortName") or clean_ticker(ticker)

    def _enrich_positions(self):
        """Add computed fields to each position."""
        total_value = sum(
            p.get("walletImpact", {}).get("currentValue", 0) for p in self.positions
        )
        for p in self.positions:
            wallet = p.get("walletImpact", {})
            cost = wallet.get("totalCost", 0)
            value = wallet.get("currentValue", 0)
            pnl = wallet.get("unrealizedProfitLoss", 0)
            p["_pnl"] = pnl
            p["_pnl_pct"] = ((value - cost) / cost * 100) if cost else 0
            p["_value"] = value
            p["_cost"] = cost
            p["_weight"] = (value / total_value * 100) if total_value else 0
            inst = p.get("instrument", {})
            p["_ticker"] = inst.get("ticker", "???")
            p["_symbol"] = self.symbol(p["_ticker"])
            p["_name"] = inst.get("name", p["_ticker"])
            meta = self.instruments.get(p["_ticker"], {})
            # GBX is pence sterling, so it counts as GBP exposure
            cur = inst.get("currency") or meta.get("currencyCode") or "GBP"
            p["_currency"] = "GBP" if cur == "GBX" else cur
            # Share prices are quoted in the instrument's own currency (GBX = pence)
            p["_price_ccy"] = cur
            p["_price"] = p.get("currentPrice")
            p["_avg_price"] = p.get("averagePricePaid")
            p["_type"] = (meta.get("type") or "Unknown").title().replace("Etf", "ETF")

    def _build_holdings(self):
        """Direct (non-pie) slice of each position plus one row per pie."""
        rows = []
        for p in self.positions:
            qty = p.get("quantity") or 0
            in_pies = (p.get("quantityInPies") or 0) if self.pies is not None else 0
            share = (qty - in_pies) / qty if qty else 1
            if share <= 1e-9:
                continue  # held entirely inside pies
            rows.append({
                "_symbol": p["_symbol"],
                "_name": p["_name"],
                "_value": p["_value"] * share,
                "_cost": p["_cost"] * share,
                "_pnl": p["_pnl"] * share,
                "_pnl_pct": p["_pnl_pct"],
                "_currency": p["_currency"],
                "_price_ccy": p["_price_ccy"],
                "_price": p["_price"],
                "_avg_price": p["_avg_price"],
                "_type": p["_type"],
                "_is_pie": False,
            })
        for pie in self.pies or []:
            result = pie.get("result") or {}
            rows.append({
                "_symbol": pie.get("name") or f"Pie {pie.get('id')}",
                "_name": pie.get("name") or f"Pie {pie.get('id')}",
                # The app shows a pie's value including its uninvested cash
                "_value": (result.get("priceAvgValue") or 0) + (pie.get("cash") or 0),
                "_cost": result.get("priceAvgInvestedValue") or 0,
                "_pnl": result.get("priceAvgResult") or 0,
                "_pnl_pct": (result.get("priceAvgResultCoef") or 0) * 100,
                "_currency": "",
                "_price_ccy": "",
                "_price": None,
                "_avg_price": None,
                "_type": "Pie",
                "_is_pie": True,
            })
        total = sum(r["_value"] for r in rows)
        for r in rows:
            r["_weight"] = (r["_value"] / total * 100) if total else 0
        return rows

    @property
    def total_value(self):
        return self.summary.get("totalValue", 0)

    @property
    def positions_value(self):
        """Sum of position values; the base for sector/currency percentages."""
        return sum(p["_value"] for p in self.positions)

    @property
    def invested(self):
        return self.summary.get("investments", {}).get("totalCost", 0)

    @property
    def total_pnl(self):
        return self.summary.get("investments", {}).get("unrealizedProfitLoss", 0)

    @property
    def total_pnl_pct(self):
        inv = self.invested
        return (self.total_pnl / inv * 100) if inv else 0

    @property
    def cash(self):
        return self.summary.get("cash", {}).get("free", 0)

    @property
    def currency(self):
        return self.summary.get("currency", "GBP")

    @property
    def winners(self):
        return sorted(
            [p for p in self.positions if p["_pnl"] > 0],
            key=lambda x: x["_pnl_pct"],
            reverse=True,
        )

    @property
    def losers(self):
        return sorted(
            [p for p in self.positions if p["_pnl"] < 0],
            key=lambda x: x["_pnl_pct"],
        )

    @property
    def win_rate(self):
        total = len(self.positions)
        wins = len(self.winners)
        return (wins / total * 100) if total else 0

    @property
    def sector_breakdown(self):
        """Group positions by instrument type (ETF/Stock), largest first."""
        sectors = {}
        for p in self.positions:
            sector = p["_type"]
            if sector not in sectors:
                sectors[sector] = {"value": 0, "cost": 0, "count": 0}
            sectors[sector]["value"] += p["_value"]
            sectors[sector]["cost"] += p["_cost"]
            sectors[sector]["count"] += 1
        return dict(sorted(sectors.items(), key=lambda kv: -kv[1]["value"]))

    @property
    def currency_exposure(self):
        """Group value by currency."""
        exposure = {}
        for p in self.positions:
            cur = p["_currency"]
            if cur not in exposure:
                exposure[cur] = 0
            exposure[cur] += p["_value"]
        return dict(sorted(exposure.items(), key=lambda kv: -kv[1]))

    @property
    def top_holding_weight(self):
        return max((h["_weight"] for h in self.holdings), default=0)

    def concentration_warning(self, threshold=CONCENTRATION_THRESHOLD):
        """Return positions exceeding weight threshold."""
        return [p for p in self.positions if p["_weight"] > threshold]


# ── Snapshot Manager ───────────────────────────────────────────────────


class SnapshotManager:
    """Saves daily portfolio snapshots to CSV for historical tracking."""

    def __init__(self, directory: Path = SNAPSHOT_DIR):
        self.directory = directory
        self.portfolio_file = directory / "portfolio_history.csv"
        self.positions_file = directory / "positions_history.csv"

    def save_snapshot(self, analytics: PortfolioAnalytics):
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()

        # Portfolio-level snapshot
        exists = self.portfolio_file.exists()
        with open(self.portfolio_file, "a", newline="") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(
                    [
                        "date",
                        "timestamp",
                        "total_value",
                        "invested",
                        "pnl",
                        "pnl_pct",
                        "cash",
                        "positions_count",
                        "win_rate",
                    ]
                )
            writer.writerow(
                [
                    today,
                    now,
                    f"{analytics.total_value:.2f}",
                    f"{analytics.invested:.2f}",
                    f"{analytics.total_pnl:.2f}",
                    f"{analytics.total_pnl_pct:.2f}",
                    f"{analytics.cash:.2f}",
                    len(analytics.positions),
                    f"{analytics.win_rate:.1f}",
                ]
            )

        # Position-level snapshot
        exists = self.positions_file.exists()
        with open(self.positions_file, "a", newline="") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(
                    [
                        "date",
                        "ticker",
                        "name",
                        "value",
                        "cost",
                        "pnl",
                        "pnl_pct",
                        "weight",
                        "currency",
                    ]
                )
            for p in analytics.positions:
                writer.writerow(
                    [
                        today,
                        p["_ticker"],
                        p["_name"],
                        f"{p['_value']:.2f}",
                        f"{p['_cost']:.2f}",
                        f"{p['_pnl']:.2f}",
                        f"{p['_pnl_pct']:.2f}",
                        f"{p['_weight']:.1f}",
                        p["_currency"],
                    ]
                )

    def load_history(self, days: int = 30):
        """Load recent portfolio history from CSV."""
        if not self.portfolio_file.exists():
            return []
        rows = []
        with open(self.portfolio_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [r for r in rows if r["date"] >= cutoff]

    def daily_values(self, days: int = 90, live_value: float = None):
        """One (date, total_value) per day, last row wins; today uses live_value."""
        by_date = {}
        for row in self.load_history(days):
            try:
                by_date[row["date"]] = float(row["total_value"])
            except (ValueError, KeyError):
                pass
        if live_value is not None:
            by_date[datetime.now().strftime("%Y-%m-%d")] = live_value
        return sorted(by_date.items())


# ── Terminal Renderers ─────────────────────────────────────────────────


def _colour(value: float, text: str = None) -> str:
    """ANSI colour: green for positive, red for negative."""
    display = text or str(value)
    if value > 0:
        return f"\033[92m{display}\033[0m"
    elif value < 0:
        return f"\033[91m{display}\033[0m"
    return display


def _bar(pct: float, max_pct: float, width: int = 10) -> str:
    """Weight bar scaled so the largest holding fills the bar."""
    filled = round(pct / max_pct * width) if max_pct else 0
    return "█" * min(filled, width) + "░" * (width - min(filled, width))


def _fit(text: str, width: int) -> str:
    """Truncate with an ellipsis so long fund names keep columns aligned."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _split_positions(analytics: "PortfolioAnalytics", top_n: int = TOP_N_POSITIONS):
    """Largest holdings first; the tail is rolled into one summary dict."""
    ranked = sorted(analytics.holdings, key=lambda x: x["_value"], reverse=True)
    shown, rest = ranked[:top_n], ranked[top_n:]
    if not rest:
        return shown, None
    others = {
        "count": len(rest),
        "value": sum(p["_value"] for p in rest),
        "cost": sum(p["_cost"] for p in rest),
        "pnl": sum(p["_pnl"] for p in rest),
        "weight": sum(p["_weight"] for p in rest),
    }
    others["pnl_pct"] = (others["pnl"] / others["cost"] * 100) if others["cost"] else 0
    return shown, others


def _chart_series(daily_history, value_history):
    """Prefer the daily snapshot series; fall back to this session's points."""
    if daily_history and len(daily_history) >= 2:
        return [v for _, v in daily_history], f"Daily — last {len(daily_history)} days", "Period"
    if value_history and len(value_history) >= 2:
        return [v for _, v in value_history], "Session", "Session"
    return None, None, None


def _area_chart(points: list, width: int, height: int, cur: str) -> Text:
    """Multi-row area chart with a value axis and first/last x labels.

    `points` is [(x_label, value), ...]. Each column is drawn with eighth-block
    characters, so a 6-row chart has 48 levels of vertical resolution.
    Columns are green when at/above the first value, red when below it.
    """
    axis_w = 10
    plot_w = max(10, width - axis_w - 1)
    values = [v for _, v in points]

    # Resample to the plot width: average when there are more points than
    # columns, interpolate linearly when there are fewer
    n = len(values)
    cols = []
    for c in range(plot_w):
        if n >= plot_w:
            lo_i = int(c * n / plot_w)
            bucket = values[lo_i:max(lo_i + 1, int((c + 1) * n / plot_w))]
            cols.append(sum(bucket) / len(bucket))
        else:
            pos = c * (n - 1) / (plot_w - 1)
            i = min(int(pos), n - 2)
            cols.append(values[i] + (values[i + 1] - values[i]) * (pos - i))

    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.1 or max(abs(hi) * 0.001, 0.01)
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    base = values[0]
    eighths = " ▁▂▃▄▅▆▇█"

    out = Text()
    for row in range(height - 1, -1, -1):
        # Axis label on the top, middle and bottom rows
        if row in (height - 1, height // 2, 0):
            row_val = lo + span * (row + 0.5) / height
            out.append(f"{_money(row_val, cur):>{axis_w - 2}} ┤", style="dim")
        else:
            out.append(" " * (axis_w - 1) + "│", style="dim")
        for v in cols:
            level = (v - lo) / span * height * 8
            fill = int(min(max(level - row * 8, 0), 8))
            style = "green" if v >= base else "red"
            out.append(eighths[fill] if fill else " ", style=style)
        out.append("\n")

    # X axis: first label left, last label right
    first, last = str(points[0][0]), str(points[-1][0])
    out.append(" " * (axis_w - 1) + "└" + "─" * plot_w + "\n", style="dim")
    gap = max(1, plot_w - len(first) - len(last))
    out.append(" " * axis_w + first + " " * gap + last, style="dim")
    return out


def _parse_order(item: dict) -> dict:
    """Flatten a history/orders item ({order: {...}, fill: {...}})."""
    order = item.get("order", item)
    fill = item.get("fill") or {}
    qty = fill.get("quantity", order.get("filledQuantity", order.get("quantity", 0))) or 0
    side = order.get("side", "")
    return {
        "date": (fill.get("filledAt") or order.get("createdAt") or "")[:10],
        "ticker": order.get("ticker", ""),
        "side": side.title(),
        "qty": abs(qty),
        # fill price is quoted in the instrument's currency, not the account's
        "price": fill.get("price", 0) or 0,
        "currency": order.get("instrument", {}).get("currency") or order.get("currency", ""),
        "status": order.get("status", "").title(),
        "sell": side.upper() == "SELL",
    }


def _sparkline_rich(values: list, width: int = 68):
    """Return a coloured Rich Text sparkline from a list of floats."""
    from rich.text import Text as RText

    blocks = "▁▂▃▄▅▆▇█"
    if len(values) < 2:
        return RText("Accumulating data…", style="dim")

    mn, mx = min(values), max(values)
    spread = mx - mn

    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = list(values)

    line = RText()
    for i, v in enumerate(sampled):
        idx = int((v - mn) / spread * 7) if spread else 3
        char = blocks[idx]
        if i == 0:
            style = "dim"
        elif sampled[i] > sampled[i - 1]:
            style = "bright_green"
        elif sampled[i] < sampled[i - 1]:
            style = "bright_red"
        else:
            style = "yellow"
        line.append(char, style=style)
    return line


def _sparkline_ansi(values: list, width: int = 60) -> str:
    """Return an ANSI-coloured sparkline string for the plain renderer."""
    blocks = "▁▂▃▄▅▆▇█"
    if len(values) < 2:
        return "[accumulating data…]"

    mn, mx = min(values), max(values)
    spread = mx - mn

    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = list(values)

    result = ""
    for i, v in enumerate(sampled):
        idx = int((v - mn) / spread * 7) if spread else 3
        char = blocks[idx]
        if i > 0 and sampled[i] > sampled[i - 1]:
            result += f"\033[92m{char}\033[0m"
        elif i > 0 and sampled[i] < sampled[i - 1]:
            result += f"\033[91m{char}\033[0m"
        else:
            result += char
    return result


def render_simple(analytics: PortfolioAnalytics, value_history=None, daily_history=None):
    """Fallback renderer using plain ANSI escape codes."""
    os.system("cls" if os.name == "nt" else "clear")
    now = datetime.now().strftime("%d %b %Y  %H:%M:%S")
    cur = analytics.currency
    base = analytics.positions_value

    print()
    print("=" * 76)
    print(f"  NEXT DEPOSIT        {now}")
    print("=" * 76)
    print(f"  {'Total Value:':<22} {cur} {analytics.total_value:>10.2f}")
    print(f"  {'Invested:':<22} {cur} {analytics.invested:>10.2f}")
    print(f"  {'Cash:':<22} {cur} {analytics.cash:>10.2f}")
    pnl_str = f"{analytics.total_pnl:+.2f} ({analytics.total_pnl_pct:+.1f}%)"
    print(f"  {'Total P&L:':<22} {_colour(analytics.total_pnl, pnl_str)}")
    print(
        f"  {'Win Rate:':<22} {analytics.win_rate:.0f}%  "
        f"({len(analytics.winners)}W / {len(analytics.losers)}L)"
    )

    print()
    print("-" * 104)
    print(
        f"  {'INVESTMENT':<28} {'TICKER':<6} {'VALUE':>9} {'COST':>9} {'P&L':>9} "
        f"{'RETURN':>8} {'WT%':>6}  WEIGHT     CCY"
    )
    print("-" * 104)

    shown, others = _split_positions(analytics)
    max_w = analytics.top_holding_weight
    for p in shown:
        pnl_str = f"{p['_pnl']:>+9.2f}"
        pnl_pct_str = f"{p['_pnl_pct']:>+7.1f}%"
        print(
            f"  {_fit(p['_name'], 28):<28} {'Pie' if p['_is_pie'] else p['_symbol'][:6]:<6} "
            f"{p['_value']:>9.2f} {p['_cost']:>9.2f} "
            f"{_colour(p['_pnl'], pnl_str)} {_colour(p['_pnl_pct'], pnl_pct_str)} "
            f"{p['_weight']:>5.1f}%  {_bar(p['_weight'], max_w)} {'—' if p['_is_pie'] else p['_currency']}"
        )
    if others:
        pnl_str = f"{others['pnl']:>+9.2f}"
        pnl_pct_str = f"{others['pnl_pct']:>+7.1f}%"
        label = f"+{others['count']} more"
        print(
            f"  {label:<35} {others['value']:>9.2f} {others['cost']:>9.2f} "
            f"{_colour(others['pnl'], pnl_str)} {_colour(others['pnl_pct'], pnl_pct_str)} "
            f"{others['weight']:>5.1f}%"
        )

    # Sector breakdown
    print()
    print("-" * 76)
    print("  SECTOR BREAKDOWN")
    print("-" * 76)
    for sector, data in analytics.sector_breakdown.items():
        pct = (data["value"] / base * 100) if base else 0
        print(f"  {sector:<20} {cur}{data['value']:>8.2f}  ({pct:>5.1f}%)  {data['count']} positions")

    # Currency exposure
    print()
    print("-" * 76)
    print("  CURRENCY EXPOSURE")
    print("-" * 76)
    for cur_code, val in analytics.currency_exposure.items():
        pct = (val / base * 100) if base else 0
        print(f"  {cur_code:<8} {cur}{val:>8.2f}  ({pct:>5.1f}%)")

    # ── Value Chart ──
    vals, title, label = _chart_series(daily_history, value_history)
    if vals:
        change = vals[-1] - vals[0]
        change_pct = (change / vals[0] * 100) if vals[0] else 0
        arrow = "▲" if change >= 0 else "▼"
        col = "\033[92m" if change >= 0 else "\033[91m"
        print()
        print("-" * 76)
        print(f"  PORTFOLIO VALUE ({title})")
        print("-" * 76)
        print(f"  Lo:{cur}{min(vals):,.2f}  {_sparkline_ansi(vals)}  Hi:{cur}{max(vals):,.2f}")
        print(
            f"  {label}: {col}{arrow} {cur}{abs(change):,.2f} ({change_pct:+.1f}%)\033[0m"
            f"  |  {len(vals)} data points"
        )

    # Concentration warnings
    warnings = analytics.concentration_warning()
    if warnings:
        print()
        print(
            f"  \033[93m⚠  Concentration alert: "
            + ", ".join(f"{w['_symbol']} ({w['_weight']:.1f}%)" for w in warnings)
            + f" exceed {CONCENTRATION_THRESHOLD}% weight\033[0m"
        )

    print()
    print(
        f"  {len(analytics.positions)} positions | "
        f"Refreshes every {REFRESH_SECONDS}s | Ctrl+C to stop"
    )
    print("=" * 76)
    print()


CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _money(value: float, cur: str, signed: bool = False) -> str:
    sym = CURRENCY_SYMBOLS.get(cur, cur + " ")
    if signed:
        return f"{'+' if value >= 0 else '-'}{sym}{abs(value):,.2f}"
    return f"{sym}{value:,.2f}"


def _share_price(value, cur: str) -> str:
    """Share price in its quote currency; pence for GBX, '—' when unknown."""
    if value is None:
        return "[dim]—[/]"
    if cur == "GBX":
        return f"{value:,.1f}p"
    return _money(value, cur)


def _kpi(value: str, label: str, style: str = "bright_white", border: str = "blue"):
    return Panel(
        Text.from_markup(f"[bold {style}]{value}[/]\n[dim]{label}[/]"),
        border_style=border,
        padding=(0, 1),
    )


def _holdings_table(analytics: "PortfolioAnalytics", name_width: int) -> Table:
    cur = analytics.currency
    shown, others = _split_positions(analytics)
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("Investment", style="bold", max_width=name_width, no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Entry", justify="right", style="dim", no_wrap=True)
    table.add_column("Price", justify="right", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    table.add_column("Return", justify="right", no_wrap=True)
    table.add_column("%", justify="right", no_wrap=True)
    table.add_column("Weight", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)

    max_w = analytics.top_holding_weight
    for p in shown:
        pstyle = "green" if p["_pnl"] >= 0 else "red"
        # Price vs entry in the quote currency, so FX moves don't flip the colour
        price_style = "green" if (p["_price"] or 0) >= (p["_avg_price"] or 0) else "red"
        table.add_row(
            f"[magenta]◔ {escape(p['_name'])}[/]" if p["_is_pie"] else escape(p["_name"]),
            "[magenta]Pie[/]" if p["_is_pie"] else p["_symbol"],
            _share_price(p["_avg_price"], p["_price_ccy"]),
            f"[{price_style}]{_share_price(p['_price'], p['_price_ccy'])}[/]",
            _money(p["_value"], cur),
            f"[{pstyle}]{_money(p['_pnl'], cur, signed=True)}[/]",
            f"[{pstyle}]{p['_pnl_pct']:+.1f}%[/]",
            f"{p['_weight']:.1f}%",
            f"[blue]{_bar(p['_weight'], max_w)}[/]",
        )
    if others:
        ostyle = "green" if others["pnl"] >= 0 else "red"
        table.add_row(
            f"[dim]+{others['count']} others[/]",
            "",
            "",
            "",
            _money(others["value"], cur),
            f"[{ostyle}]{_money(others['pnl'], cur, signed=True)}[/]",
            f"[{ostyle}]{others['pnl_pct']:+.1f}%[/]",
            f"{others['weight']:.1f}%",
            "",
        )
    return table


def _allocation_table(analytics: "PortfolioAnalytics") -> Table:
    """Asset type and currency split of the underlying positions."""
    cur = analytics.currency
    base = analytics.positions_value
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_column(no_wrap=True)

    def section(title, items):
        table.add_row(f"[bold]{title}[/]", "", "", "")
        for label, value in items:
            pct = (value / base * 100) if base else 0
            table.add_row(f"  {label}", _money(value, cur), f"{pct:.0f}%", f"[cyan]{_bar(pct, 100, 8)}[/]")

    section("Type", [(k, v["value"]) for k, v in analytics.sector_breakdown.items()])
    section("Currency", list(analytics.currency_exposure.items()))
    return table


def _dividends_table(analytics: "PortfolioAnalytics", dividends: list) -> Table:
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("Date", style="dim", no_wrap=True)
    table.add_column("Ticker", no_wrap=True)
    table.add_column("Amount", justify="right", no_wrap=True)
    for d in dividends[:5]:
        table.add_row(
            (d.get("paidOn") or d.get("date") or "")[:10],
            analytics.symbol(d.get("ticker", "")),
            f"[green]{_money(d.get('amount', 0) or 0, d.get('currency') or analytics.currency)}[/]",
        )
    return table


def _orders_table(analytics: "PortfolioAnalytics", orders: list) -> Table:
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("Date", style="dim", no_wrap=True)
    table.add_column("Ticker", no_wrap=True)
    table.add_column("Side", no_wrap=True)
    table.add_column("Qty", justify="right", no_wrap=True)
    table.add_column("Price", justify="right", no_wrap=True)
    for o in map(_parse_order, orders[:5]):
        price = (
            f"{o['price']:,.0f}p" if o["currency"] == "GBX" else _money(o["price"], o["currency"])
        )
        table.add_row(
            o["date"],
            analytics.symbol(o["ticker"]),
            f"[{'red' if o['sell'] else 'green'}]{o['side']}[/]",
            f"{o['qty']:.4f}".rstrip("0").rstrip("."),
            price,
        )
    return table


def _value_chart_panel(daily_history, value_history, width, chart_height, cur):
    """Chart panel: daily snapshots when there are 2+ days, else this session."""
    if daily_history and len(daily_history) >= 2:
        points = [(datetime.strptime(d, "%Y-%m-%d").strftime("%d %b"), v) for d, v in daily_history]
        chart_title = f"Portfolio value · last {len(points)} days"
    elif value_history and len(value_history) >= 2:
        points = [(t.strftime("%H:%M"), v) for t, v in value_history]
        chart_title = "Portfolio value · this session"
    else:
        points = None
    if points:
        vals = [v for _, v in points]
        change = vals[-1] - vals[0]
        change_pct = (change / vals[0] * 100) if vals[0] else 0
        subtitle = Text.assemble(
            (f"{'▲' if change >= 0 else '▼'} {_money(abs(change), cur)} ({change_pct:+.2f}%)", "green" if change >= 0 else "red"),
            (f"  ·  low {_money(min(vals), cur)}  ·  high {_money(max(vals), cur)}  ·  {len(vals)} points", "dim"),
        )
        return Panel(
            _area_chart(points, width - 4, chart_height, cur),
            title=f"[bold]{chart_title}[/]",
            title_align="left",
            subtitle=subtitle,
            subtitle_align="right",
            border_style="blue",
            padding=(0, 1),
        )
    return Panel(
        Text("Chart appears after the second refresh…", style="dim"),
        title="[bold]Portfolio value[/]",
        title_align="left",
        border_style="blue",
    )


def _hbar(value: float, max_value: float, width: int = 20) -> str:
    """Horizontal bar with eighth-block precision."""
    if max_value <= 0 or value <= 0:
        return ""
    eighths = int(round(min(value / max_value, 1) * width * 8))
    full, part = divmod(eighths, 8)
    return "█" * full + (" ▏▎▍▌▋▊▉"[part] if part else "")


def _stat(value: str, label: str, style: str = "bright_white", border: str = "blue"):
    return _kpi(value, label, style=style, border=border)


def _stat_row(*panels):
    row = Table.grid(expand=True, padding=(0, 1))
    for _ in panels:
        row.add_column(ratio=1)
    row.add_row(*panels)
    return row


def _two_columns(left, right, wide: bool, right_width: int = 52):
    """Side by side on wide terminals, stacked otherwise."""
    if not wide:
        return Group(left, right)
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(width=right_width)
    grid.add_row(left, right)
    return grid


def _panel(body, title: str, border: str = "blue", subtitle=None):
    return Panel(body, title=f"[bold]{title}[/]", title_align="left", border_style=border, subtitle=subtitle, subtitle_align="right")


def _month_label(key: str) -> str:
    return datetime.strptime(key, "%Y-%m").strftime("%b %y")


def _monthly_bars(series: list, cur: str, style: str) -> Table:
    """One row per month: label, bar, amount (negative months in red)."""
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(no_wrap=True, style="dim")
    table.add_column(ratio=1, no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    peak = max((abs(v) for _, v in series), default=0)
    for key, value in series:
        bar_style = style if value >= 0 else "red"
        table.add_row(
            _month_label(key),
            f"[{bar_style}]{_hbar(abs(value), peak, 28)}[/]",
            f"[{bar_style}]{_money(value, cur, signed=value < 0)}[/]" if value else "[dim]—[/]",
        )
    return table


# ── Pages ──────────────────────────────────────────────────────────────

PAGES = ["Overview", "Planner", "Performance", "Income", "Risk"]


def _screen_header(ctx: dict) -> Table:
    header = Table.grid(expand=True)
    header.add_column()
    header.add_column(justify="right")
    left = Text()
    left.append(" NEXT DEPOSIT ", style="bold white on blue")
    if ctx.get("demo"):
        left.append(" DEMO DATA ", style="bold black on yellow")
    left.append("  ")
    for i, name in enumerate(PAGES):
        if i == ctx["page"]:
            left.append(f" {i + 1} {name} ", style="bold black on cyan")
        else:
            left.append(f" {i + 1} ", style="cyan")
            left.append(f"{name} ", style="dim")
    status = Text()
    if ctx.get("error"):
        status.append(f"⚠ {ctx['error']}  ", style="bold yellow")
    if ctx.get("updated_at"):
        status.append(f"Updated {ctx['updated_at']:%H:%M:%S}", style="dim")
    if ctx.get("next_in") is not None:
        status.append(f" · next in {ctx['next_in']:>2}s ", style="dim")
    header.add_row(left, status)
    return header


def _kpi_row(analytics: "PortfolioAnalytics") -> Table:
    cur = analytics.currency
    pnl_style = "green" if analytics.total_pnl >= 0 else "red"
    cash_pct = (analytics.cash / analytics.total_value * 100) if analytics.total_value else 0
    return _stat_row(
        _kpi(_money(analytics.total_value, cur), "Total value"),
        _kpi(_money(analytics.invested, cur), "Invested", border="cyan"),
        _kpi(
            f"{_money(analytics.total_pnl, cur, signed=True)} ({analytics.total_pnl_pct:+.1f}%)",
            "Unrealised P&L",
            style=pnl_style,
            border=pnl_style,
        ),
        _kpi(_money(analytics.cash, cur), f"Cash · {cash_pct:.0f}%", border="yellow"),
        _kpi(
            f"{analytics.win_rate:.0f}%",
            f"Win rate · {len(analytics.winners)}/{len(analytics.positions)}",
            border="magenta",
        ),
    )


def _footer(ctx: dict) -> Text:
    analytics = ctx["analytics"]
    footer = Text(" ")
    warnings = analytics.concentration_warning()
    if warnings and ctx["page"] == 0:
        footer.append(
            "⚠ Concentration: "
            + ", ".join(f"{w['_symbol']} {w['_weight']:.0f}%" for w in warnings)
            + f" over {CONCENTRATION_THRESHOLD}%   ",
            style="yellow",
        )
    keys = "1-5/←→ pages"
    if ctx["page"] == 1:
        keys += " · ↑↓ Enter edit · m auto/scenario · r reload"
    footer.append(f"{keys} · q quit", style="dim")
    return footer


def page_overview(ctx: dict) -> list:
    analytics, width = ctx["analytics"], ctx["width"]
    wide = width >= 140
    dividends = (ctx.get("dividends") or [])[:5]
    orders = (ctx.get("orders") or [])[:5]

    holdings = _panel(
        _holdings_table(analytics, name_width=34 if wide else 28),
        f"Holdings [dim]· {len(analytics.holdings)}[/]",
    )
    side = [_panel(_allocation_table(analytics), "Allocation", "cyan")]
    if dividends:
        side.append(_panel(_dividends_table(analytics, dividends), "Recent dividends", "green"))
    left = [holdings]
    if orders and (wide or ctx["height"] >= 55):  # stacked layout is tall
        left.append(_panel(_orders_table(analytics, orders), "Recent orders", "magenta"))

    body = Table.grid(expand=True, padding=(0, 1))
    if wide:
        body.add_column(ratio=1)
        body.add_column(width=44)
        body.add_row(Group(*left), Group(*side))
        return [body]
    body.add_column(ratio=1)
    body.add_column(ratio=1)
    body.add_row(*side) if len(side) == 2 else body.add_row(side[0], "")
    return [holdings, body, *left[1:]]


class Planner:
    """Interactive planner state: a row cursor, inline number entry, and two
    modes. "auto" splits the deposit by your target weights; "scenario" uses
    the £ amounts you type per holding, so you can try "what if" buys."""

    def __init__(self, deposit: float):
        self.mode = "auto"
        self.deposit = deposit
        self.targets = {}
        self.buys = {}  # scenario mode: {holding key: £}
        self.selected = None  # holding key under the cursor (rows can reorder)
        self.edit = None  # None | "target" | "buy" | "deposit"
        self.buffer = ""
        self.placeholder = ""  # old value, shown faintly while typing
        self.message = ""

    def load(self, config: dict):
        self.targets = {k: float(v) for k, v in (config.get("targets_pct") or {}).items()}

    def plan(self, holdings: list) -> dict:
        if self.mode == "auto":
            buys = insights.auto_buys(holdings, self.targets, self.deposit)
        else:
            buys = self.buys
        return insights.plan_rows(holdings, self.targets, buys)

    def keys_in_order(self, holdings: list) -> list:
        return [r["key"] for r in self.plan(holdings)["rows"]]

    def cursor(self, rows: list) -> int:
        """Row index of the selected holding (first row if it has gone)."""
        if self.selected not in rows:
            self.selected = rows[0] if rows else None
        return rows.index(self.selected) if rows else 0

    def handle(self, key: str, holdings: list) -> bool:
        """Apply a key press; True if the planner used it."""
        if self.edit:
            if key in "0123456789" or (key == "." and "." not in self.buffer):
                if len(self.buffer) < 9:
                    self.buffer += key
            elif key in ("\x7f", "\x08"):
                self.buffer = self.buffer[:-1]
            elif key in ("\r", "\n"):
                self._commit(holdings)
            elif key == "esc":
                self.edit, self.message = None, "Cancelled"
            return True  # swallow everything else while typing

        rows = self.keys_in_order(holdings)
        i = self.cursor(rows)
        current = self.selected
        if key == "up" and rows:
            self.selected = rows[max(0, i - 1)]
        elif key == "down" and rows:
            self.selected = rows[min(len(rows) - 1, i + 1)]
        elif key in ("\r", "\n") and current:
            self.edit = "target" if self.mode == "auto" else "buy"
            existing = self.targets.get(current) if self.mode == "auto" else self.buys.get(current)
            self.buffer, self.placeholder = "", f"{existing:g}" if existing else ""
        elif key == "d" and self.mode == "auto":
            self.edit, self.buffer, self.placeholder = "deposit", "", f"{self.deposit:g}"
        elif key == "m":
            self.mode = "scenario" if self.mode == "auto" else "auto"
            self.message = "Scenario: type your own £ per holding" if self.mode == "scenario" else "Auto: deposit split by targets"
        elif key == "c":
            if self.mode == "scenario":
                self.buys, self.message = {}, "Scenario cleared"
            elif current and current in self.targets:
                del self.targets[current]
                insights.save_targets(self.targets)
                self.message = f"Removed target for {current} · saved"
        elif key in ("+", "=") and self.mode == "auto":
            self.deposit += 10
        elif key == "-" and self.mode == "auto":
            self.deposit = max(0, self.deposit - 10)
        elif key == "]" and self.mode == "auto":
            self.deposit += 50
        elif key == "[" and self.mode == "auto":
            self.deposit = max(0, self.deposit - 50)
        else:
            return False
        return True

    def _commit(self, holdings: list):
        if not self.buffer:  # Enter on an empty field keeps the old value
            self.edit, self.message = None, "Unchanged"
            return
        try:
            value = float(self.buffer)
        except ValueError:
            self.edit, self.buffer, self.message = None, "", "Not a number, unchanged"
            return
        key = self.selected
        if self.edit == "deposit":
            self.deposit = value
            self.message = f"Deposit set to £{value:,.2f}"
        elif self.edit == "target" and key:
            if value:
                self.targets[key] = value
            else:
                self.targets.pop(key, None)
            insights.save_targets(self.targets)
            self.message = f"Target for {key} set to {value:g}% · saved to {insights.CONFIG_FILE.name}"
        elif self.edit == "buy" and key:
            if value:
                self.buys[key] = value
            else:
                self.buys.pop(key, None)
            self.message = f"Scenario: £{value:,.2f} into {key}"
        self.edit, self.buffer = None, ""


def _impact_panel(ctx: dict, plan: dict) -> Panel:
    """Before → after for the planned buys: value, fees, mix, concentration."""
    analytics, cur, config = ctx["analytics"], ctx["analytics"].currency, ctx["config"] or {}
    buys = {r["key"]: r["buy"] for r in plan["rows"] if r["buy"] > 0}
    impact = insights.scenario_impact(analytics.positions, analytics.holdings, buys)
    amount = plan["amount"]
    fees = impact["fx_fee"] + impact["stamp_duty"]

    t = Table(box=None, padding=(0, 1), expand=True, show_header=True, header_style="dim")
    t.add_column("", no_wrap=True, ratio=1)
    t.add_column("Now", justify="right", no_wrap=True)
    t.add_column("After", justify="right", no_wrap=True)
    t.add_column("Change", justify="right", no_wrap=True)

    def change(before, after):
        delta = after - before
        style = "dim" if abs(delta) < 0.05 else ("green" if delta > 0 else "red")
        return f"{before:.1f}%", f"{after:.1f}%", f"[{style}]{delta:+.1f}[/]"

    value_now = analytics.total_value
    value_after = value_now + amount - fees
    t.add_row("[bold]You invest[/]", "", f"[bold green]{_money(amount, cur)}[/]", "")
    t.add_row("Est. fees", "", _money(fees, cur), "")
    t.add_row(
        "[dim]  FX · stamp[/]",
        "",
        f"[dim]{_money(impact['fx_fee'], cur)} · {_money(impact['stamp_duty'], cur)}[/]",
        "",
    )
    t.add_row("Portfolio value", _money(value_now, cur), _money(value_after, cur), f"[green]{_money(value_after - value_now, cur, signed=True)}[/]")

    # Largest holding and concentration (holding level, as in the app)
    rows = plan["rows"]
    if rows:
        top_before = max(rows, key=lambda r: r["value"])
        top_after = max(rows, key=lambda r: r["after_value"])
        short = lambda r: "Pie" if r["is_pie"] else r["key"]
        t.add_row(
            "Largest holding",
            f"{short(top_before)} {top_before['current_pct']:.0f}%",
            f"{short(top_after)} {top_after['after_pct']:.0f}%",
            "",
        )
        over = [short(r) for r in rows if r["after_pct"] > CONCENTRATION_THRESHOLD]
        was_over = [short(r) for r in rows if r["current_pct"] > CONCENTRATION_THRESHOLD]
        t.add_row(
            f"Over {CONCENTRATION_THRESHOLD}%",
            ", ".join(was_over) or "none",
            f"[{'yellow' if over else 'green'}]{', '.join(over) or 'none'}[/]",
            "",
        )

    for label, field in (("Currency", "currency"), ("Asset type", "type")):
        t.add_row(f"[bold]{label}[/]", "", "", "")
        before, after = impact["before"][field], impact["after"][field]
        for k in after:
            t.add_row(f"  {k}", *change(before.get(k, 0), after[k]))

    if config.get("is_isa", True) and ctx.get("transactions") is not None:
        left = float(config.get("isa_allowance", 20000)) - insights.isa_used(ctx["transactions"])
        after_left = left - amount
        t.add_row("ISA left", _money(left, cur), f"[{'red' if after_left < 0 else 'white'}]{_money(after_left, cur)}[/]", "")

    warnings = Text()
    small = [r["key"] for r in rows if 0 < r["buy"] < 1]
    if small:
        warnings.append(f"\n⚠ Under the £1 minimum order: {', '.join(small)}", style="yellow")
    if impact["unmapped"]:
        warnings.append(f"\n⚠ Not held, impact not modelled: {', '.join(impact['unmapped'])}", style="yellow")
    warnings.append("\nLook-through: pie buys are spread over the pie's stocks. Fees are estimates.", style="dim")
    return _panel(Group(t, warnings), "Impact before you buy", "cyan")


def page_planner(ctx: dict) -> list:
    analytics, cur = ctx["analytics"], ctx["analytics"].currency
    planner: Planner = ctx["planner"]
    plan = planner.plan(analytics.holdings)
    wide = ctx["width"] >= 140
    auto = planner.mode == "auto"

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    table.add_column("", no_wrap=True, width=1)
    table.add_column("Holding", max_width=30, no_wrap=True, overflow="ellipsis", ratio=1)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    table.add_column("Now", justify="right", no_wrap=True)
    table.add_column("Target" + (" ✎" if auto else ""), justify="right", no_wrap=True)
    table.add_column("Drift", justify="right", no_wrap=True)
    table.add_column("Buy" + ("" if auto else " ✎"), justify="right", no_wrap=True)
    table.add_column("After", justify="right", no_wrap=True)

    cursor = planner.cursor([r["key"] for r in plan["rows"]])
    editing_cell = (
        f"[black on yellow]{planner.buffer}▌[/]"
        if planner.buffer
        else f"[black on yellow]▌[/][dim]{planner.placeholder}[/]"
    )
    for i, r in enumerate(plan["rows"]):
        selected = i == cursor
        drift = r["drift_pct"]
        drift_style = "dim" if abs(drift) < 1 else ("yellow" if drift > 0 else "cyan")
        if planner.edit == "buy" and selected:
            buy = editing_cell
        elif r["buy"] >= 1:
            buy = f"[bold green]{_money(r['buy'], cur)}[/]"
        elif r["buy"] > 0:
            buy = f"[yellow]{_money(r['buy'], cur)}[/]"
        else:
            buy = "[dim]—[/]"
        if planner.edit == "target" and selected:
            target = editing_cell
        else:
            target = f"{r['target_raw']:g}%" if r["target_raw"] else "[dim]—[/]"
        name = escape(r["name"])
        table.add_row(
            "[bold cyan]▶[/]" if selected else "",
            f"[magenta]◔ {name}[/]" if r["is_pie"] else (f"[dim]{name} (not held)[/]" if r["missing"] else name),
            "[magenta]Pie[/]" if r["is_pie"] else r["key"],
            _money(r["value"], cur),
            f"{r['current_pct']:.1f}%",
            target,
            f"[{drift_style}]{drift:+.1f}%[/]" if r["target_pct"] else "[dim]—[/]",
            buy,
            f"{r['after_pct']:.1f}%",
            style="on grey15" if selected else None,
        )

    mode = Text()
    mode.append(" Auto: split by targets ", style="bold black on green" if auto else "dim")
    mode.append(" ")
    mode.append(" Scenario: my own buys ", style="bold black on cyan" if not auto else "dim")
    mode.append("   m to switch", style="dim")

    if auto:
        if planner.edit == "deposit":
            deposit = Text.from_markup(f"Deposit: £{editing_cell}  [dim]type an amount, Enter to confirm, Esc to cancel[/]")
        else:
            deposit = Text.from_markup(f"Deposit: [bold green]{_money(planner.deposit, cur)}[/]  [dim]d to type · +/- £10 · ]/[ £50[/]")
        tsum = plan["targets_sum"]
        hint = (
            f"Targets add up to {tsum:g}%"
            + ("" if abs(tsum - 100) < 0.5 else " (rescaled to 100%)")
            + " · holdings with no target get no new money"
        )
    else:
        deposit = Text.from_markup(f"Scenario total: [bold cyan]{_money(plan['amount'], cur)}[/]  [dim]Enter on a row to type £ · c to clear[/]")
        hint = "Nothing is bought here. Use this to see the impact first, then place the orders in the app."

    help_line = Text(
        "↑↓ select · Enter edit · Esc cancel" + (" · c remove target" if auto else ""),
        style="dim",
    )
    message = Text(planner.message, style="italic green") if planner.message else Text("")
    left = _panel(
        Group(mode, deposit, Text(""), table, Text(hint, style="dim"), help_line, message),
        "Planner",
        "green",
    )

    right = [_impact_panel(ctx, plan)]
    # Inside the pie: slices furthest below their own targets
    for pie_id, detail in (ctx.get("pie_details") or {}).items():
        name = (detail.get("settings") or {}).get("name", f"Pie {pie_id}")
        slices = insights.pie_slices(detail)
        t = Table.grid(padding=(0, 1), expand=True)
        t.add_column(no_wrap=True)
        t.add_column(justify="right", no_wrap=True)
        t.add_column(justify="right", no_wrap=True)
        for sl in slices[:5]:
            t.add_row(analytics.symbol(sl["ticker"]), f"{sl['current_pct']:.1f}% of {sl['target_pct']:.1f}%", f"[cyan]{sl['drift_pct']:+.2f}%[/]")
        right.append(
            _panel(
                Group(t, Text("Money into the pie is split by its own targets.", style="dim")),
                f"Most underweight in {escape(name)}",
                "magenta",
            )
        )
    return [_two_columns(left, Group(*right), wide, right_width=58)]


def page_performance(ctx: dict) -> list:
    analytics, cur, config = ctx["analytics"], ctx["analytics"].currency, ctx["config"]
    tx = ctx.get("transactions")
    if tx is None:
        return [_panel(Text("Loading transaction history… (history endpoints are rate-limited, this can take a minute)", style="dim"), "Performance")]
    perf = insights.performance(tx, analytics.total_value)
    flows = insights.contributions(tx)
    monthly = insights.monthly_contributions(flows)
    streak = insights.contribution_streak(flows)
    fee = insights.fees(ctx.get("orders") or [])

    growth_style = "green" if perf["growth"] >= 0 else "red"
    xirr = perf["xirr"]
    since = f" · since {perf['first_deposit']:%b %y}" if perf["first_deposit"] else ""
    stats = _stat_row(
        _stat(_money(perf["net_contributed"], cur), "Your money in (net)", border="cyan"),
        _stat(
            f"{_money(perf['growth'], cur, signed=True)} ({perf['growth_pct']:+.1f}%)",
            "Market growth on it",
            style=growth_style,
            border=growth_style,
        ),
        _stat(
            f"{xirr * 100:+.1f}% a year" if xirr is not None else "n/a",
            f"Real return{since}",
            style="green" if (xirr or 0) >= 0 else "red",
            border="magenta",
        ),
        _stat(_money(perf["interest"], cur), "Interest on cash", border="yellow"),
        _stat(_money(fee["total"], cur), f"Fees · {fee['pct_of_traded']:.2f}% of trades", border="red"),
    )

    positive = [v for _, v in monthly if v > 0]
    avg = sum(positive) / len(positive) if positive else 0
    contrib = _panel(
        Group(
            _monthly_bars(monthly, cur, "cyan"),
            Text(
                f"\nStreak: {streak} month{'s' if streak != 1 else ''} in a row · "
                f"average deposit month {_money(avg, cur)} · "
                f"in {_money(perf['deposited'], cur)}, out {_money(perf['withdrawn'], cur)} all-time",
                style="dim",
            ),
        ),
        "Net money in, last 12 months",
        "cyan",
    )

    side = []
    if config.get("is_isa", True):
        allowance = float(config.get("isa_allowance", 20000))
        used = insights.isa_used(tx)
        start = insights.tax_year_start()
        end = date(start.year + 1, 4, 5)
        pct = used / allowance * 100 if allowance else 0
        isa = Table.grid(padding=(0, 1), expand=True)
        isa.add_column(no_wrap=True)
        isa.add_column(justify="right", no_wrap=True)
        isa.add_row("Used this tax year", f"[bold]{_money(used, cur)}[/]")
        isa.add_row("Remaining", f"[green]{_money(allowance - used, cur)}[/]")
        isa.add_row("Tax year ends", f"{end:%d %b %Y} [dim]({(end - date.today()).days} days)[/]")
        isa.add_row(f"[cyan]{_hbar(pct, 100, 30) or '▏'}[/]", f"{pct:.1f}%")
        side.append(_panel(Group(isa, Text("Counts deposits only; ISA transfers-in don't use the allowance.", style="dim")), f"ISA allowance · {_money(allowance, cur)}", "green"))

    fees_t = Table.grid(padding=(0, 1), expand=True)
    fees_t.add_column(no_wrap=True)
    fees_t.add_column(justify="right", no_wrap=True)
    for name, amount in sorted(fee["by_type"].items(), key=lambda kv: -kv[1]):
        fees_t.add_row(insights.FEE_LABELS.get(name, name.replace("_", " ").title()), _money(amount, cur))
    fees_t.add_row("[bold]Total[/]", f"[bold]{_money(fee['total'], cur)}[/]")
    fees_t.add_row("[dim]Traded (all fills)[/]", f"[dim]{_money(fee['traded'], cur)}[/]")
    side.append(_panel(fees_t, "Costs", "red"))

    return [stats, _two_columns(contrib, Group(*side), ctx["width"] >= 140)]


def page_income(ctx: dict) -> list:
    analytics, cur = ctx["analytics"], ctx["analytics"].currency
    dividends = ctx.get("dividends")
    if dividends is None:
        return [_panel(Text("Loading dividend history…", style="dim"), "Income")]
    summary = insights.dividend_summary(dividends, analytics.invested)
    names = {d.get("ticker"): (d.get("instrument") or {}).get("name") for d in dividends}

    stats = _stat_row(
        _stat(_money(summary["ttm"], cur), "Dividends, last 12 months", style="green", border="green"),
        _stat(f"{summary['yield_on_cost']:.2f}%", "Yield on cost", border="cyan"),
        _stat(_money(summary["ttm"] / 12, cur), "Average per month", border="blue"),
        _stat(_money(summary["all_time"], cur), f"All-time · {summary['count']} payments", border="magenta"),
        _stat(_money(summary["ttm"], cur), "Next 12m if payouts repeat", border="yellow"),
    )
    monthly = _panel(_monthly_bars(summary["monthly"], cur, "green"), "Dividends by month", "green")

    top = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    top.add_column("Payer", max_width=26, no_wrap=True, overflow="ellipsis", ratio=1)
    top.add_column("Ticker", style="cyan", no_wrap=True)
    top.add_column("12m", justify="right", no_wrap=True)
    top.add_column("Share", justify="right", no_wrap=True)
    for ticker, amount in summary["top_payers"][:10]:
        share = amount / summary["ttm"] * 100 if summary["ttm"] else 0
        top.add_row(escape(names.get(ticker) or ""), analytics.symbol(ticker), _money(amount, cur), f"{share:.0f}%")
    payers = _panel(top, "Top payers, last 12 months", "green")

    recent = _panel(_dividends_table(analytics, dividends[:6]), "Latest payments", "green")
    return [stats, _two_columns(monthly, Group(payers, recent), ctx["width"] >= 140)]


def page_risk(ctx: dict) -> list:
    analytics, cur = ctx["analytics"], ctx["analytics"].currency
    frag = insights.fragmentation(analytics.positions)
    groups = insights.overlap_groups(analytics.positions)
    base = analytics.positions_value or 1
    top = max(analytics.holdings, key=lambda h: h["_value"], default=None)

    conc = Table.grid(padding=(0, 1), expand=True)
    conc.add_column(no_wrap=True)
    conc.add_column(justify="right", no_wrap=True)
    if top:
        conc.add_row("Largest holding", f"{escape(top['_symbol'])} [bold]{top['_weight']:.1f}%[/]")
    conc.add_row("Top 5 positions (look-through)", f"[bold]{frag['top5_pct']:.0f}%[/]")
    conc.add_row(
        "Effective number of holdings",
        f"[bold]{frag['effective_n']:.1f}[/] [dim]of {frag['count']}[/]",
    )
    conc.add_row(
        "Positions under 1% each",
        f"[bold]{frag['small_count']}[/] [dim]= {_money(frag['small_value'], cur)} ({frag['small_pct']:.0f}%)[/]",
    )
    conc_note = Text(
        "Effective holdings = 1 / Σweight²: how many equal-sized positions your "
        "portfolio behaves like. Look-through counts each stock inside the pie.",
        style="dim",
    )
    concentration = _panel(Group(conc, conc_note), "Concentration", "yellow")

    ov = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=True, show_edge=False)
    ov.add_column("Same exposure", no_wrap=True, ratio=1)
    ov.add_column("Funds", style="cyan", no_wrap=True)
    ov.add_column("Combined", justify="right", no_wrap=True)
    ov.add_column("Share", justify="right", no_wrap=True)
    for g in groups:
        ov.add_row(
            f"[{'red' if g['same_isin'] else 'white'}]{escape(g['label'])}[/]",
            ", ".join(m["_symbol"] for m in g["members"]),
            _money(g["value"], cur),
            f"{g['value'] / base * 100:.0f}%",
        )
    overlap = _panel(
        Group(ov, Text("Funds tracking the same index add holdings without adding diversification.", style="dim"))
        if groups
        else Text("No overlapping funds found.", style="dim"),
        "Overlap",
        "yellow",
    )

    daily = ctx.get("daily") or []
    risk = insights.risk_metrics(daily, insights.contributions(ctx.get("transactions") or []))
    rt = Table.grid(padding=(0, 1), expand=True)
    rt.add_column(no_wrap=True)
    rt.add_column(justify="right", no_wrap=True)
    rt.add_row("Days of snapshots", f"[bold]{risk['days']}[/]")
    if risk["days"] >= 2:
        rt.add_row("Return over the period", f"{risk['period_return_pct']:+.2f}%")
        rt.add_row("Max drawdown (peak → trough)", f"[red]{risk['max_drawdown_pct']:.2f}%[/]")
        rt.add_row("Below the peak right now", f"{risk['current_drawdown_pct']:.2f}%")
        if risk.get("best_day"):
            d, r = risk["best_day"]
            rt.add_row("Best day", f"[green]{r * 100:+.2f}%[/] [dim]{d}[/]")
            d, r = risk["worst_day"]
            rt.add_row("Worst day", f"[red]{r * 100:+.2f}%[/] [dim]{d}[/]")
        rt.add_row(
            "Volatility (annualised)",
            f"{risk['volatility_pct']:.1f}%" if "volatility_pct" in risk else f"[dim]needs 10+ days ({risk['days']}/10)[/]",
        )
    risk_note = Text(
        "From the daily snapshots this dashboard saves; deposits are removed so "
        "adding money doesn't count as a gain. Grows more useful every day it runs.",
        style="dim",
    )
    risk_panel = _panel(Group(rt, risk_note), "Risk from your history", "red")

    return [_two_columns(Group(concentration, overlap), risk_panel, ctx["width"] >= 140, right_width=60)]


PAGE_BUILDERS = [page_overview, page_planner, page_performance, page_income, page_risk]


def build_screen(ctx: dict):
    """Whole screen for the current page, sized to the terminal."""
    analytics = ctx["analytics"]
    parts = [_screen_header(ctx), _kpi_row(analytics)]
    parts += PAGE_BUILDERS[ctx["page"]](ctx)
    parts.append(_footer(ctx))
    if ctx["page"] == 0:
        # Give the value chart whatever rows the rest of the page leaves
        probe = Console(width=ctx["width"], file=io.StringIO(), color_system=None)
        used = len(probe.render_lines(Group(*parts), probe.options, pad=False))
        chart_height = max(4, min(14, ctx["height"] - used - 5))
        parts.insert(
            2,
            _value_chart_panel(ctx.get("daily"), ctx.get("value_history"), ctx["width"], chart_height, analytics.currency),
        )
    return Group(*parts)


# ── Data feed (background threads) ─────────────────────────────────────


class DataFeed:
    """Fetches data on background threads so the screen never blocks.

    One thread polls the live endpoints every REFRESH_SECONDS; another pulls
    full history (transactions, orders, dividends) every
    HISTORY_REFRESH_SECONDS, waiting out rate limits as it pages.
    """

    def __init__(self, client: "BrokerClient", snapshots: "SnapshotManager"):
        self.client = client
        self.snapshots = snapshots
        self.lock = threading.Lock()
        self.value_history: deque = deque(maxlen=2880)  # 24h at 30s refreshes
        self.state = {
            "analytics": None,
            "updated_at": None,
            "error": None,
            "next_refresh_at": time.time(),
            "transactions": None,
            "orders": None,
            "dividends": None,
            "pie_details": {},
            "daily": [],
        }
        self._instruments = []
        self._instruments_tried = 0.0
        self._raw_pies = None
        self._pies_fetched = 0.0
        self._last_snapshot_date = None

    def start(self):
        threading.Thread(target=self._live_loop, daemon=True).start()
        threading.Thread(target=self._history_loop, daemon=True).start()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state, value_history=list(self.value_history))

    def _set(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def _live_loop(self):
        while True:
            try:
                self._refresh_live()
            except Exception as e:  # keep the thread alive on surprises
                self._set(error=f"{type(e).__name__}: {e}")
            self._set(next_refresh_at=time.time() + REFRESH_SECONDS)
            time.sleep(REFRESH_SECONDS)

    def _history_loop(self):
        while True:
            for key, fetch in (
                ("transactions", self.client.history_transactions),
                ("orders", self.client.history_orders),
                ("dividends", self.client.history_dividends),
            ):
                try:
                    items = fetch(retries=6)
                except Exception:
                    items = None
                if items is not None:
                    self._set(**{key: items})
            time.sleep(HISTORY_REFRESH_SECONDS)

    def _refresh_live(self):
        c = self.client
        # Instrument metadata is large and rarely changes: fetch until it works
        if not self._instruments and time.time() - self._instruments_tried >= 60:
            self._instruments_tried = time.time()
            self._instruments = c.instruments() or []

        summary = c.account_summary()
        positions = c.positions() if summary else None
        if not summary or positions is None:
            self._set(error=c.last_error or "Could not reach the broker API")
            return

        # /equity/pies allows 1 request / 30s; keep the last good result
        if time.time() - self._pies_fetched >= 30:
            self._raw_pies = c.pies() or self._raw_pies
            self._pies_fetched = time.time()
            known = self.state["pie_details"]
            missing = [p for p in self._raw_pies or [] if p["id"] not in known]
            if missing:
                self._set(pie_details={**known, **c.pie_details(missing)})
        details = self.state["pie_details"]
        pies = (
            [{**p, "name": (details.get(p["id"], {}).get("settings") or {}).get("name")} for p in self._raw_pies]
            if self._raw_pies is not None
            else None
        )

        analytics = PortfolioAnalytics(summary, positions, self._instruments, pies)
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_snapshot_date != today:
            self.snapshots.save_snapshot(analytics)
            self._last_snapshot_date = today
        with self.lock:
            self.value_history.append((datetime.now(), analytics.total_value))
        self._set(
            analytics=analytics,
            updated_at=datetime.now(),
            error=None,
            daily=self.snapshots.daily_values(days=90, live_value=analytics.total_value),
        )


class KeyReader:
    """Non-blocking single-key input (cbreak mode) for the live screen."""

    ARROWS = {
        "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
        "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left",
    }

    def __enter__(self):
        self.fd = None
        if sys.stdin.isatty():
            import termios
            import tty

            self.fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def read(self, timeout: float) -> list:
        """Keys pressed within `timeout` seconds (sleeps if no terminal)."""
        if self.fd is None:
            time.sleep(timeout)
            return []
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        data = os.read(self.fd, 64).decode(errors="ignore")
        keys = []
        while data:
            for seq, name in self.ARROWS.items():
                if data.startswith(seq):
                    keys.append(name)
                    data = data[len(seq):]
                    break
            else:
                ch = data[0]
                keys.append({"\t": "right", "\x1b": "esc"}.get(ch, ch))
                data = data[1:]
        return keys


# ── Main Loop ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Next Deposit: live portfolio planner and monitor")
    parser.add_argument("--invest", type=float, help="deposit amount for the planner (default: monthly_deposit in the config)")
    parser.add_argument("--page", type=int, default=1, choices=range(1, len(PAGES) + 1), help="page to open on")
    parser.add_argument("--demo", action="store_true", help="run on a fictional portfolio (no API key needed)")
    args = parser.parse_args()

    if args.demo:
        import demo_data

        client, snapshot_dir, insights.CONFIG_FILE = demo_data.demo_environment()
        feed = DataFeed(client, SnapshotManager(snapshot_dir))
    elif not API_KEY or not API_SECRET:
        print("\n  ❌ Add BROKER_API_KEY and BROKER_API_SECRET to your .env file!")
        print("     See .env.example, or try: python next_deposit.py --demo\n")
        return
    else:
        feed = DataFeed(BrokerClient(API_KEY, API_SECRET, BASE_URL), SnapshotManager())
    feed.start()

    if not RICH_AVAILABLE:
        while True:
            time.sleep(REFRESH_SECONDS)
            state = feed.snapshot()
            if state["analytics"]:
                render_simple(state["analytics"], state["value_history"], state["daily"])
            if state["error"]:
                print(f"  ⚠ {state['error']}")

    console = Console()
    page = args.page - 1
    config = None
    planner = None
    with KeyReader() as keys, Live(console=console, screen=True, auto_refresh=False) as live:
        while True:
            state = feed.snapshot()
            analytics = state["analytics"]
            if analytics and config is None:
                config = insights.load_config(analytics.holdings)
                planner = Planner(args.invest if args.invest is not None else float(config.get("monthly_deposit", 100)))
                planner.load(config)

            for key in keys.read(0.25):
                # The planner gets first refusal on its page (typing digits,
                # arrows to move the cursor, ...)
                if page == 1 and planner and planner.handle(key, analytics.holdings):
                    continue
                if key in ("1", "2", "3", "4", "5"):
                    page = int(key) - 1
                elif key == "right":
                    page = (page + 1) % len(PAGES)
                elif key == "left":
                    page = (page - 1) % len(PAGES)
                elif key == "r" and analytics:
                    config = insights.load_config(analytics.holdings)
                    planner.load(config)
                    planner.message = f"Reloaded {insights.CONFIG_FILE}"
                elif key in ("q", "Q"):
                    return

            if analytics:
                ctx = dict(
                    state,
                    page=page,
                    width=console.width,
                    height=console.height,
                    config=config,
                    planner=planner,
                    demo=args.demo,
                    next_in=max(0, int(state["next_refresh_at"] - time.time())),
                )
                view = build_screen(ctx)
            else:
                msg = f"⚠ {state['error']} — retrying…" if state["error"] else f"Connecting to {BASE_URL}…"
                view = Text(f"\n  {msg}", style="yellow" if state["error"] else "dim")
            live.update(view, refresh=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  👋 Goodbye!\n")
