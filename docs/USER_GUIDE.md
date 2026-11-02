# User guide

This guide covers how to run the dashboard day to day, what each page shows, how to use the planner, and how to fix the usual problems. For how each number is worked out, see [METRICS.md](METRICS.md).

- [1. Setup](#1-setup)
- [2. Daily routine](#2-daily-routine)
- [3. The screen](#3-the-screen)
- [4. Page 1: Overview](#4-page-1-overview)
- [5. Page 2: Planner](#5-page-2-planner)
- [6. Page 3: Performance](#6-page-3-performance)
- [7. Page 4: Income](#7-page-4-income)
- [8. Page 5: Risk](#8-page-5-risk)
- [8b. Page 6: History](#8b-page-6-history)
- [9. Daily snapshots](#9-daily-snapshots)
- [10. Configuration reference](#10-configuration-reference)
- [11. Troubleshooting](#11-troubleshooting)
- [12. FAQ](#12-faq)

---

## 1. Setup

You need Python 3.9 or newer. For live data you also need your own **Invest** or **Stocks ISA** account with a broker whose public API this tool reads. The broker's API terms permit personal use only, so check them first.

```bash
git clone https://github.com/TeslimAdeyanju/next-deposit.git
cd next-deposit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

No account yet? Run `python next_deposit.py --demo` to explore every page with a fictional portfolio. It needs no API key, and nothing is saved.

### API key

1. In the broker's app, open **Settings → API (Beta) → Generate API key**.
2. Give it **read-only** permissions: account data, portfolio, history (orders, dividends, transactions), pies and metadata. The dashboard never places orders, so don't grant order permissions.
3. Paste the key and secret into `.env`:

```
BROKER_API_KEY=...
BROKER_API_SECRET=...
BROKER_BASE_URL=https://<broker-api-host>/api/v0
```

`.env` is git-ignored, so it never leaves your machine.

### Optional shortcut

Add this to `~/.zshrc` (or `~/.bashrc`) to start the dashboard by typing `nd`:

```bash
alias nd='cd ~/path/to/next-deposit && .venv/bin/python next_deposit.py'
```

---

## 2. Daily routine

```bash
cd next-deposit
source .venv/bin/activate
python next_deposit.py
```

- **Leave it running as long as you like.** It's free: it uses the broker's API only, not any paid service, and very little CPU. It saves one snapshot per day, including just after midnight if it's still running.
- **Run one copy at a time.** Rate limits are per account, so a second copy (or another script using the same key) makes both slower and can hide your pie for a while.
- **Keep the Mac awake** while it runs: `caffeinate -i python next_deposit.py`.
- **Quit** with `q` or `Ctrl+C`. The terminal goes back to normal.

### When you add money

1. Open the Planner: press `2`, or start with `--page 2` or `--invest 150`.
2. Check or adjust the deposit amount and your targets.
3. Read the **Impact before you buy** panel.
4. Place the orders in the broker's app as **value orders** (£, not shares).
5. On the next refresh you'll see the new weights.

---

## 3. The screen

```
 NEXT DEPOSIT   1 Overview  2 Planner  3 Performance  4 Income  5 Risk  6 History  Updated 09:41:05 · next in 12s
╭──────────────╮ ╭──────────────╮ ╭──────────────────╮ ╭──────────────╮ ╭────────────────────╮
│ £1,250.00    │ │ £1,100.00    │ │ +£150.00 (+13.6%)│ │ £20.00       │ │ 62%                │
│ Total value  │ │ Invested     │ │ Unrealised P&L   │ │ Cash · 2%    │ │ Win rate · 18/29   │
╰──────────────╯ ╰──────────────╯ ╰──────────────────╯ ╰──────────────╯ ╰────────────────────╯
 …page content…
 1-5/←→ pages · q quit
```

- **Top bar:** the page tabs (the current page is highlighted), when the data last updated, and a countdown to the next refresh. A yellow **⚠** message here means a request failed, usually a rate limit. The numbers on screen are then the last good ones, and it retries on its own.
- **KPI row:** shown on every page.

| Card | Meaning |
|---|---|
| Total value | Account value reported by the broker. The label shows the split: **holdings + cash**, so the two always add up |
| Invested | What your current holdings cost you |
| Unrealised P&L | Holdings value minus Invested (not sold gains). Cash isn't included, so Total value − Invested ≠ P&L whenever you hold cash |
| Cash | Cash outside your investments: available to trade **plus cash reserved for pending orders** (shown as *£x pending*) |
| Win rate | Positions in profit / all positions (look-through, including pie stocks) |

- **Footer:** the keys that work on the current page, plus a **concentration alert** on the Overview when any holding is over 25%.

---

## 4. Page 1: Overview

### Value chart

- **Session:** until there are snapshots from two different days, the chart plots this run's 30-second points, so it starts flat and builds up.
- **Daily:** after that it switches to one point per day for the last 90 days.
- **Reading it:** green means above the starting value and red below. The £ scale is on the left and dates or times are along the bottom. The panel's subtitle shows the change, low, high and number of points.

### Holdings

Holdings are listed the way the broker's app shows them: **your pie is one line**, and each stock or fund shows only the shares held **outside** the pie.

| Column | Meaning |
|---|---|
| Investment / Ticker | Full name and ticker (`◔ … Pie` for a pie) |
| Entry | Your average price paid, in the share's own currency ($, €, £ or p for pence) |
| Price | Current price in the same currency. **Green if above your entry price, red if below** (FX doesn't affect the colour) |
| Value | Current value in £ |
| Return / % | Unrealised gain in £ and %. **This includes currency moves**, so a US stock's price can be up while its £ return is down |
| Weight | Share of your holdings, with a bar scaled to your largest holding |

### Allocation, dividends and orders

- **Allocation:** Asset type (ETF / Stock) and Currency (GBP / USD / EUR), **look-through**, so the stocks inside your pie count by their own type and currency. GBX (pence) counts as GBP.
- **Recent dividends / Recent orders:** the latest five of each. Order prices are in the instrument's currency.
- **Daily change:** the last 7 days of total value, each with its **market move** (deposits removed) as ▲ up or ▼ down, and a tally of up days, down days and the net move. *+Nd* marks a gap of N days without data; gaps are shown but not counted. The full schedule is on the History page (`6`).

---

## 5. Page 2: Planner

The planner works out how to put money in so your portfolio moves toward the mix you want. **It never sells anything and never places orders.**

### Modes

Press `m` to switch.

| Mode | You enter | It works out |
|---|---|---|
| **Auto: split by targets** | A target % per holding and a deposit amount | How much to buy of each holding (the **Buy** column) |
| **Scenario: my own buys** | £ amounts per holding | Only the impact. Use it for "what if I put £50 in MSFT?" |

### Setting targets (Auto)

1. `↑`/`↓` to highlight a holding.
2. `Enter`, type the target, e.g. `25`, then `Enter`. It's **saved straight away** to `portfolio_config.json`.
3. `c` removes the selected holding's target. Holdings without a target get no new money (they're kept, never sold).
4. `d` lets you type the deposit amount. `+`/`-` change it by £10, `]`/`[` by £50.

Targets don't have to add up to 100. The planner tells you the total and rescales them, so `30, 30, 20, 10, 10` and `3, 3, 2, 1, 1` mean the same thing.

**First run:** your targets start as your current weights, so the planner simply keeps your current mix until you change them.

### The planner table

| Column | Meaning |
|---|---|
| Now | Current share of your holdings |
| Target ✎ | Your target (✎ marks the editable column) |
| Drift | Now minus target. **Cyan = under target, yellow = over**, dim = within 1% |
| Buy | £ to buy. Green when ≥ £1, yellow when under the broker's £1 minimum |
| After | Share after these buys |

**Why does an over-target holding sometimes still get money?** Targets are shares of your **whole** portfolio. If some holdings have no target (say 30% of your money), every targeted holding is under its target once the new money is counted, so they all get a share. Give every holding a target if you want stricter behaviour.

### Impact before you buy

This panel recalculates every time you change something.

| Row | Meaning |
|---|---|
| You invest | Total of the Buy column |
| Est. fees | FX 0.15% on non-GBP stocks + stamp duty 0.5% on UK shares (ETFs are exempt). Pie buys are split across the pie's stocks |
| Portfolio value | Now → after (deposit minus estimated fees) |
| Largest holding | Before and after |
| Over 25% | Holdings above the concentration threshold, before and after |
| Currency / Asset type | Look-through mix before → after, with the change in percentage points |
| ISA left | This tax year's remaining allowance before → after (red if you'd go over) |

Warnings appear below the table for buys under £1 or holdings it can't model.

### Most underweight in your pie

These are the stocks in your pie furthest below the pie's own targets. Money added to a pie is spread by those targets automatically, so you don't buy them yourself.

### Walkthrough: a monthly £150

1. `python next_deposit.py --invest 150 --page 2`
2. Check the targets. Edit any that are wrong (`↑`/`↓`, `Enter`, type, `Enter`).
3. Read the Buy column, e.g. *VWRP £52, VUAG £45, Pie £33, VHYL £20*.
4. Check the impact panel. Are fees small? Is anything going over 25%? Is the currency mix what you want?
5. Place those four value orders in the app.

### Walkthrough: testing an idea

1. Press `m` for **Scenario**.
2. Highlight MSFT, press `Enter`, type `50`, press `Enter`.
3. The impact panel might show *USD 34% → 44%* and *Largest holding: Pie → VUAG*. Decide whether that's what you want.
4. Press `c` to clear, and `m` to go back to Auto.

---

## 6. Page 3: Performance

| Card | Meaning |
|---|---|
| **Your money in (net)** | Deposits + transfers in − withdrawals − transfers out |
| **Market growth on it** | Total value − net money in. The part the market made for you |
| **Real return … a year** | Money-weighted annualised return (XIRR). It accounts for *when* each deposit went in. Needs 30+ days of history |
| **Interest on cash** | Interest the broker paid on uninvested cash |
| **Fees paid** | FX conversion + stamp duty actually charged on your fills, and as a % of the amount traded |

- **Net money in, last 12 months:** one bar per month, red for months where more went out than in. Below it: your **deposit streak** (consecutive months with a deposit), your average month, and all-time in/out.
- **ISA allowance** (if `is_isa` is true): used this tax year (6 April – 5 April), remaining, days left. Only deposits count. An ISA **transfer-in** doesn't use your allowance.
- **Costs:** fees by type, and how much you've traded in total.

The page shows *Loading…* for up to a minute after starting, because full history is fetched slowly to respect rate limits. It refreshes every 30 minutes.

---

## 7. Page 4: Income

| Card | Meaning |
|---|---|
| Dividends, last 12 months | Total received in the last 365 days |
| Yield on cost | Last-12-month dividends ÷ what your holdings cost |
| Average per month | Last 12 months ÷ 12 |
| All-time | Everything received, and number of payments |
| Next 12m if payouts repeat | A simple estimate: the same as the last 12 months |

Below the cards are **Dividends by month** (bars), **Top payers** (last 12 months, with each one's share of your income) and **Latest payments**.

---

## 8. Page 5: Risk

### Concentration

| Row | Meaning |
|---|---|
| Largest holding | Biggest line in the holdings view (the pie counts as one) |
| Top 5 positions | Share of the five biggest *underlying* positions |
| Effective number of holdings | How many equal-sized positions your portfolio behaves like (1 ÷ Σweight²). 54 positions that behave like 11 means a few big ones dominate |
| Positions under 1% each | Count and total value of very small positions |

### Overlap

Funds that hold the same thing:

- **Same index:** e.g. three S&P 500 trackers, or two FTSE All-World funds. More lines, but no extra diversification.
- **Same fund, two listings (red):** identical ISIN bought twice, e.g. the same gold ETC in GBP and EUR.

### Risk from your history

Calculated from your daily snapshots, **with deposits removed** so adding money isn't counted as a gain:

- **Return over the period:** growth since the first snapshot, deposit-adjusted.
- **Max drawdown:** the biggest fall from a peak.
- **Below the peak right now:** how far you currently are from the high.
- **Best / worst day.**
- **Volatility (annualised):** needs 10+ days of snapshots.

These get more meaningful the more days the dashboard has run.

---

## 8b. Page 6: History

A **schedule of your total value**, one row per day, newest first:

| Column | Meaning |
|---|---|
| Date | The day. **today · live** updates every refresh. *(+Nd gap)* marks days the monitor didn't run |
| Total value | That day's latest saved value |
| Change / % | Movement since the previous row |
| Money in | Deposits/withdrawals since the previous row (so adding money isn't mistaken for growth) |
| Market move | Change − money in: what the market did. The bar shows its size |
| Invested / Unrealised P&L | Cost of holdings and paper gain at that point |

The cards above it summarise the period: change since the first day, split into money in and market move, plus the best and worst single days. These appear once there are two day-to-day moves; gaps of more than 3 days are left out.

The schedule fills in from your daily snapshots, so it grows by one row each day the monitor runs. If the window is too short for every row, the oldest are hidden.

## 9. Daily snapshots

While it runs, the dashboard appends a portfolio row **at most once an hour** (so each day's last row is its latest value), and per-position rows **once a day**:

| File | Columns |
|---|---|
| `snapshots/portfolio_history.csv` | `date, timestamp, total_value, invested, pnl, pnl_pct, cash, positions_count, win_rate` |
| `snapshots/positions_history.csv` | `date, ticker, name, value, cost, pnl, pnl_pct, weight, currency` (one row per underlying position) |

Restarting on the same day appends another set of rows. The dashboard uses the **last** row for each date. The files are plain CSV, so you can use them in Excel, Power BI or pandas. Don't reorder the columns.

Snapshots are **git-ignored**: they hold your real portfolio history, so they stay on your machine. Back up the `snapshots/` folder another way (e.g. iCloud or Time Machine) if you want to keep the history.

---

## 10. Configuration reference

### `.env`

| Variable | Default | |
|---|---|---|
| `BROKER_API_KEY` | required | |
| `BROKER_API_SECRET` | required | |
| `BROKER_BASE_URL` | required | The broker's API endpoint (live, or demo for a practice account), from the broker's API documentation |
| `REFRESH_SECONDS` | `30` | Lower values hit rate limits faster |

### `portfolio_config.json`

Created on first run. You can manage targets from the Planner, but you can also edit the file and press `r`.

```json
{
  "monthly_deposit": 100,
  "is_isa": true,
  "isa_allowance": 20000,
  "targets_pct": {
    "My Dividend Pie": 30,
    "VUAG": 30,
    "VWRP": 20,
    "VHYL": 10,
    "BP": 10
  }
}
```

| Key | Meaning |
|---|---|
| `monthly_deposit` | Default deposit on the Planner (overridden by `--invest`) |
| `is_isa` | Show the ISA allowance on Performance and in the impact panel |
| `isa_allowance` | Annual allowance in £ (UK: 20,000) |
| `targets_pct` | Target weight per holding. Keys are the **ticker shown on screen** (`VUAG`, not `VUAGl_EQ`) or the pie's **name** |

### Command-line options

| Option | |
|---|---|
| `--invest AMOUNT` | Planner deposit for this run |
| `--page N` | Open on page N (1–6) |
| `--demo` | Fictional portfolio, no API key; uses a temporary folder, so your snapshots and targets are untouched |

### Constants (top of `next_deposit.py`)

| Constant | Default | |
|---|---|---|
| `TOP_N_POSITIONS` | 15 | Holdings rows before the rest are grouped as "+N others" |
| `CONCENTRATION_THRESHOLD` | 25 | % that triggers a concentration alert |
| `HISTORY_REFRESH_SECONDS` | 1800 | How often full history is reloaded |

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| ⚠ *Rate limited by the broker API — is another dashboard running…* | Another copy, or a script, is using the same API key | Close the other copy. This one recovers on its own |
| Pie missing / holdings show every stock separately | The pie request was rate-limited at startup | Wait ~30s, it fills in. Make sure only one copy is running |
| Pie shows as `Pie 1234567` | Pie name lookup failed | It retries on later refreshes |
| Performance / Income say *Loading…* | Full history is downloading at the API's slow history rate | Wait up to a minute |
| ⚠ *HTTP 401/403: check …* | Wrong or revoked key, or missing permissions | Regenerate the key with read permissions and update `.env` |
| *Network error* | No internet / the broker's API unreachable | It keeps the last data and retries |
| Layout cut off at the bottom | Terminal too small | Make the window bigger (≥ 140 columns for side-by-side, ~50 rows) |
| Keys do nothing | The terminal window isn't focused, or input isn't a real terminal | Click the window; run it directly, not through a pipe |
| Planner edits went to the wrong row | (Fixed) rows used to shift when the pie loaded | Update to the latest version |

---

## 12. FAQ

**Does it cost anything to leave it running?**
No. It uses only the broker's API, which is free, and no AI or paid services.

**Can it trade for me?**
No. It's read-only by design. Use read-only API permissions.

**Why is my "real return" different from the P&L %?**
P&L % compares today's value with what you paid, and ignores timing. XIRR weights each pound by how long it was invested, and annualises the result. See [METRICS.md](METRICS.md#real-return-xirr).

**Why does "Invested" differ slightly from "Your money in"?**
"Invested" is the cost of what you hold now. "Money in" is cash you deposited minus cash you took out, so it includes cash, interest and realised gains or losses on things you sold.

**Can I compare against the S&P 500?**
Not yet. The broker's API has no historical prices, so that needs an outside data source.

---

*Next Deposit is an independent, open-source personal project, published as a portfolio case study. It is not affiliated with, endorsed by or sponsored by any broker. It reads the author's own account through the broker's public API with a read-only key, never places orders, and is an analysis tool, not financial advice.*
