# Development guide

How the code is organised, how data flows through it, and how to change it safely.

## Layout

| File | Responsibility |
|---|---|
| `trading212_dashboard.py` | Entry point. API client, `PortfolioAnalytics`, `SnapshotManager`, `DataFeed` (background threads), `Planner` (interactive state), `KeyReader`, page builders and `main()` |
| `insights.py` | **Pure functions only**, with no network calls and no Rich. Config load/save, planner maths, scenario impact, contributions, XIRR, ISA, fees, dividends, overlap, fragmentation, risk |
| `demo_data.py` | Fictional portfolio in exact API payload shapes (`DemoClient`) plus generated snapshot history; used by `--demo` |
| `tools/render_screenshots.py` | Regenerates `docs/images/page-*.svg` from demo data |
| `portfolio_config.json` | User planner settings (created on first run, git-ignored) |
| `snapshots/*.csv` | Daily history written by the dashboard. Git-ignored (personal data). Column order is relied on by external analysis, so don't reorder |

Dependencies: `requests`, `python-dotenv`, `rich` (see `requirements.txt`). Rich is optional; without it the dashboard falls back to a plain ANSI clear-and-print view of the Overview.

## Data flow

```
             ┌────────────── live thread (every REFRESH_SECONDS) ───────────────┐
Trading 212 ─┤ summary, positions, pies (≥30s), pie details, instruments         ├─┐
   API       └────────────── history thread (every HISTORY_REFRESH_SECONDS) ────┘ │
                  transactions, orders, dividends (all pages, rate-limit aware)    │
                                                                                   ▼
                                                     DataFeed.state  (lock-protected dict)
                                                                                   │
  main loop (every 0.25s): KeyReader → Planner / page switch → build_screen(ctx) ──┘
                                                       │
                              header + KPI row + PAGE_BUILDERS[page](ctx) + footer
                                                       │
                                               Rich Live(screen=True)
```

- **`DataFeed`** owns all API calls. A failure keeps the previous good data and records `state["error"]`, which the header shows. It saves a snapshot once per calendar day per process.
- **`build_screen(ctx)`** is a pure function of `ctx` (state + page + terminal size + config + planner). On the Overview it renders everything else first, measures the height, and gives the value chart whatever rows are left.
- **`Planner`** gets first refusal on key presses while page 2 is active, so digits go into number entry instead of switching pages. Its cursor follows a holding **key**, not a row index, because rows reorder when the pie loads.

## API client

`Trading212Client._get` **never raises or prints**, because printing would corrupt the full-screen view. On error it sets `client.last_error` (a readable reason) and returns `None`. Always check for `None`.

`_paginate(endpoint, limit=None, retries=0)`:

- follows `nextPagePath`, which already contains the `/api/v0` prefix, so the prefix is stripped before reuse;
- `retries > 0` sleeps 11s and retries rate-limited pages. Only use it on background threads.

### Endpoints and rate limits

| Endpoint | Used for | Approx. limit |
|---|---|---|
| `GET /equity/account/summary` | KPIs | 1 / 2s |
| `GET /equity/positions` | positions | 1 / 1s |
| `GET /equity/metadata/instruments` | type, short name (large) | 1 / 50s |
| `GET /equity/pies` | pie value/result/cash | 1 / 30s |
| `GET /equity/pies/{id}` | pie name, slice targets | 1 / 5s |
| `GET /equity/history/orders` | fills, fees | 6 / min |
| `GET /equity/history/dividends` | dividends | 6 / min |
| `GET /equity/history/transactions` | deposits, withdrawals, transfers, interest | 6 / min |

Limits are **per account**, so any other client using the same key competes with the dashboard. See the [official API docs](https://t212public-api-docs.redoc.ly/).

### Payload shapes worth knowing

- History orders are nested: `{"order": {...}, "fill": {"price", "quantity", "filledAt", "walletImpact": {"netValue", "fxRate", "taxes": [...]}}}`. `_parse_order` flattens them.
- Positions: `instrument.currency` is the **quote** currency (`GBX` = pence). `walletImpact.*` values are in the account currency and include FX.
- Transactions: `TRANSFER` amounts are signed. `WITHDRAW` amounts are negative.

## `PortfolioAnalytics`

On construction it adds underscore fields to each raw position dict: `_value _cost _pnl _pnl_pct _weight _ticker _symbol _name _currency _type _price _avg_price _price_ccy`. Renderers and `insights` read these, never the raw API shape.

- `_ticker`: raw Trading 212 ticker (`VUAGl_EQ`), written to snapshots.
- `_symbol`: display name (instrument `shortName`, else the cleaned ticker). This is also the planner's target key.
- `holdings`: the app-style view (direct slices + one row per pie). `positions`: look-through.

## Testing without the API or a terminal

The quickest check is `python trading212_dashboard.py --demo`. It exercises every page, the planner and the background threads with `DemoClient`, a stand-in for `Trading212Client` with the same methods.

There's no test suite. Rendering is deterministic given a `ctx`, so you can check pages offline from saved payloads:

```python
import json
from datetime import datetime
from rich.console import Console
from trading212_dashboard import PortfolioAnalytics, Planner, build_screen

d = json.load(open("fixture.json"))      # {"summary", "positions", "instruments"} saved from the API
a = PortfolioAnalytics(d["summary"], d["positions"], d["instruments"], pies=None)
planner = Planner(100)
planner.load({"targets_pct": {"VUAG": 50, "VWRP": 50}})

ctx = dict(analytics=a, page=1, width=160, height=50, config={}, planner=planner,
           next_in=10, updated_at=datetime.now(), error=None, transactions=[], orders=[],
           dividends=[], pie_details={}, value_history=[], daily=[])
console = Console(width=160, record=True)
console.print(build_screen(ctx))
```

`insights` functions take plain lists and dicts, so you can unit-test them directly. For example, `insights.xirr([(date(2025,1,1), -100), (date(2026,1,1), 110)])` gives about `0.10`.

**Don't hammer the live API while a dashboard is running**, because rate limits are shared. Save payloads once and render from those.

## Conventions

- Keep calculations in `insights.py` and presentation in `trading212_dashboard.py`.
- New pages: write `page_<name>(ctx) -> list[renderable]` and add it to `PAGES` and `PAGE_BUILDERS`.
- Use `_money(value, currency)` for account-currency values and `_share_price(value, quote_ccy)` for prices.
- Escape user and API text with `rich.markup.escape` before putting it in markup.
- The UI is terminal-only by design. A web front end was tried and removed.
