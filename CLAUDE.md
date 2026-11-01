# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Next Deposit**: a read-only live terminal planner and monitor for an Invest/ISA account, built on the broker's public API (v0, beta), aimed at someone who invests monthly. A Streamlit web front end was removed on purpose because the user prefers the terminal, so don't reintroduce one.

- `next_deposit.py`: API client, `PortfolioAnalytics`, snapshots, background data feed and the Rich full-screen UI (plain-ANSI fallback).
- `insights.py`: pure functions (no API calls, no rendering) for the planner (`auto_buys` → `plan_rows`), scenario impact (`scenario_impact`: look-through currency/type mix and estimated FX/stamp-duty fees), contributions/XIRR, ISA allowance, fees, dividends, overlap/fragmentation and snapshot-based risk.
- `docs/`: `USER_GUIDE.md` (user-facing), `METRICS.md` (formula for every figure; keep in sync when calculations change), `DEVELOPMENT.md`.
- `demo_data.py` + `tools/render_screenshots.py`: fictional portfolio for `--demo` and the README screenshots. Screenshots must only ever come from demo data.
- `portfolio_config.json`: user-edited planner config (`monthly_deposit`, `is_isa`, `isa_allowance`, `targets_pct` keyed by ticker symbol or pie name). Created on first run from the current weights.

## Commands

```bash
pip install -r requirements.txt               # requests, python-dotenv, rich
python next_deposit.py                # live dashboard; q or Ctrl+C quits
python next_deposit.py --invest 250   # planner uses £250 instead of monthly_deposit
python next_deposit.py --page 2       # open on a page (1 Overview … 5 Risk)
python next_deposit.py --demo         # fictional portfolio, no API key
python tools/render_screenshots.py            # regenerate docs/images/page-*.svg from demo data
```

In-app keys: `1`–`5` / ←→ / Tab switch pages, `q` quits. On the Planner (handled by the `Planner` class, which gets first refusal on keys so digits can be typed): ↑↓ select a row, Enter edits the cell (target % in Auto mode, £ buy in Scenario mode), Esc cancels, `m` toggles Auto/Scenario, `d` types the deposit, `+`/`-` `]`/`[` nudge it, `c` clears (scenario buys / the selected target), `r` reloads the config. Target edits are written straight back to `portfolio_config.json` via `insights.save_targets`. The cursor tracks a holding key, not a row index, because rows reorder when the pie loads.

There are no tests, linter config, or build step. To check rendering without the API or a TTY, build a `ctx` dict and call `build_screen(ctx)` on a `Console(width=…, record=True)`.

## Configuration

Read from `.env` via `python-dotenv` at import time (module-level constants):

- `BROKER_API_KEY`, `BROKER_API_SECRET`: required; sent as HTTP Basic auth (`key:secret`, base64).
- `BROKER_BASE_URL`: defaults to the broker's live endpoint (hard-coded in `next_deposit.py`, the only place the broker's domain should appear); swap `live` for `demo` for paper trading.
- `REFRESH_SECONDS`: default 30.

Copy `.env.example` to `.env` (git-ignored).

## Architecture

1. **`BrokerClient`**: thin `requests` wrapper. `_get` never raises or prints (printing would corrupt the full-screen view). On any error it sets `client.last_error` and returns `None`, so callers must check for `None`. `_paginate(endpoint, limit=None, retries=0)` follows `nextPagePath`, which already includes the `/api/v0` prefix (stripped before reuse). `retries` sleeps and retries rate-limited pages, so only use it off the UI thread.
2. **`PortfolioAnalytics(summary, positions, instruments, pies)`**: on construction it mutates each raw position dict, adding underscore-prefixed fields (`_value`, `_cost`, `_pnl`, `_pnl_pct`, `_weight`, `_ticker`, `_symbol`, `_name`, `_currency`, `_type`, `_price`, `_avg_price`, `_price_ccy`). Type and short name come only from `/equity/metadata/instruments`. `_ticker` is the raw the broker ticker (written to snapshots), and `_symbol` / `analytics.symbol(ticker)` is the display name. `analytics.holdings` is the app-style view: each position scaled to the quantity held outside pies (`quantity - quantityInPies`), plus one row per pie (value = `result.priceAvgValue` + pie `cash`). The planner targets these rows by `_symbol`. Breakdowns, win rate, concentration, overlap and snapshots use the underlying `positions` (look-through).
3. **`SnapshotManager`**: appends to `snapshots/portfolio_history.csv` and `positions_history.csv`. `daily_values()` collapses them to one value per day, used for the chart and `insights.risk_metrics`.
4. **`DataFeed`**: two daemon threads write into a lock-protected `state` dict. The live thread polls summary/positions every `REFRESH_SECONDS` (pies every ≥30s, instruments until they load) and saves a snapshot once per calendar day per process. The history thread pulls *all* transactions, orders and dividends every `HISTORY_REFRESH_SECONDS` (30 min), waiting out rate limits. Failures keep the last good data and set `state["error"]`.
5. **UI**: `main()` runs `Live(screen=True)` and redraws every 0.25s from `feed.snapshot()`, reading keys via `KeyReader` (cbreak + `select`). `build_screen(ctx)` = header with tabs + KPI row + `PAGE_BUILDERS[page](ctx)` + footer. Only the Overview measures the other parts and gives the value chart the leftover rows. Layouts switch to two columns at ≥140 columns.

## Gotchas

- History orders are nested (`{"order": {...}, "fill": {...}}`). `_parse_order` flattens them. Fill prices are in the instrument's currency. Fees are `fill.walletImpact.taxes[]` (`CURRENCY_CONVERSION_FEE`, `STAMP_DUTY_RESERVE_TAX`).
- Contributions are `DEPOSIT`, `TRANSFER` (signed amount) and `WITHDRAW` transactions. `INTEREST_ON_FREE_CASH` is income, not a contribution. The ISA allowance counts `DEPOSIT` only, per UK tax year (6 Apr–5 Apr).
- `averagePricePaid` / `currentPrice` are in the quote currency (`_price_ccy`, GBX shown as pence), while `walletImpact` values and returns are in the account currency and include FX.
- `GBX` positions count as `GBP` exposure. Sector/currency percentages divide by `positions_value`, not `total_value`.
- `insights.risk_metrics` removes deposits from day-to-day changes, so adding money isn't counted as a gain. Volatility needs 10+ days of snapshots.
- Rate limits are per account, so two dashboards (or a dashboard plus ad-hoc API scripts) starve each other. Tightest: `/equity/pies` 1 req/30s, `/equity/pies/{id}` 1 req/5s, history endpoints ~6 req/min.
- The repo is public: `snapshots/*.csv`, `portfolio_config.json` and `.env` hold the owner's real data and are git-ignored. Never commit them, and never put real account figures in docs or screenshots (the README findings are ratios only, approved by the owner). Snapshot column order is relied on by Excel/Power BI.
