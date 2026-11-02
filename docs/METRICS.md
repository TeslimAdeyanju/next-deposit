# How the numbers are calculated

Every figure on the dashboard, how it's derived, and where it lives in the code. "Account currency" is your account's base currency, normally GBP.

- [Holdings vs positions](#holdings-vs-positions)
- [Overview figures](#overview-figures)
- [Planner](#planner)
- [Scenario impact and fee estimates](#scenario-impact-and-fee-estimates)
- [Performance](#performance)
- [Real return (XIRR)](#real-return-xirr)
- [ISA allowance](#isa-allowance)
- [Income](#income)
- [Concentration and overlap](#concentration-and-overlap)
- [Risk from snapshots](#risk-from-snapshots)
- [Value schedule](#value-schedule-history-page)
- [Currencies](#currencies)

---

## Holdings vs positions

The API returns one **position** per instrument, and its quantity includes any shares held inside pies (`quantityInPies`). The dashboard uses two views of these:

| View | Built from | Used for |
|---|---|---|
| **Holdings** (`analytics.holdings`) | Each position scaled to `(quantity − quantityInPies) / quantity`, **plus one row per pie** | Holdings table, planner, "largest holding". Matches the broker's app |
| **Positions** (`analytics.positions`) | Every underlying instrument, including those inside pies | Allocation, win rate, overlap, concentration, snapshots ("look-through") |

A pie's row uses the pie endpoint's figures:

```
pie value  = result.priceAvgValue + pie cash
pie cost   = result.priceAvgInvestedValue
pie return = result.priceAvgResult  (and result.priceAvgResultCoef × 100 as %)
```

---

## Overview figures

| Figure | Formula / source |
|---|---|
| Total value | `summary.totalValue` |
| Invested | `summary.investments.totalCost` |
| Unrealised P&L | `summary.investments.unrealizedProfitLoss`, and ÷ Invested for % |
| Holdings value | `summary.investments.currentValue` (= Invested + Unrealised P&L; includes pie cash) |
| Cash | `summary.cash.availableToTrade` + `summary.cash.reservedForOrders`, and ÷ Total value for %. Total value = Holdings value + Cash |
| Win rate | positions with P&L > 0 ÷ all positions |
| Position value / cost / P&L | `walletImpact.currentValue` / `totalCost` / `unrealizedProfitLoss` (account currency) |
| Return % | (value − cost) ÷ cost |
| Weight | holding value ÷ sum of holding values |
| Entry / Price | `averagePricePaid` / `currentPrice` in the **instrument's** currency |
| Allocation % | look-through position values grouped by type or currency ÷ sum of position values |

`Total value` can be slightly higher than the sum of holdings because it also includes cash and pending amounts.

---

## Planner

**Auto mode** (`insights.auto_buys`). Targets are rescaled to sum to 1. With current holding values `Vᵢ`, deposit `D`, and `T = ΣV + D`:

```
shortfallᵢ = max(0, targetᵢ × T − Vᵢ)          # how far below target after the deposit
gap        = Σ shortfallᵢ

if gap ≥ D:   buyᵢ = D × shortfallᵢ / gap      # not enough money to fix everything: split by need
else:         buyᵢ = shortfallᵢ + (D − gap) × targetᵢ   # fix every gap, split the rest by target
```

It never sells. Holdings without a target get `buy = 0`. Each row's **After %** is `(Vᵢ + buyᵢ) ÷ T`, and **Drift** is `Now % − Target %`.

Because targets are shares of the *whole* portfolio, holdings with no target make every targeted holding look under target once new money arrives.

**Scenario mode:** `buyᵢ` is whatever you typed. The same table and impact are calculated from it (`insights.plan_rows`).

---

## Scenario impact and fee estimates

`insights.scenario_impact` spreads each buy over the instruments it would actually purchase:

- **Direct holding:** 100% into that instrument.
- **Pie:** spread across the pie's stocks in proportion to their current in-pie value.

Each slice is then charged estimated costs and added to that position's value:

| Cost | Rule | Rate |
|---|---|---|
| FX conversion | instrument quoted in a non-GBP currency (USD, EUR…) | 0.15% |
| Stamp duty (SDRT) | UK-quoted (GBP/GBX) **stock**; ETFs are exempt | 0.5% |

These are estimates: real stamp duty depends on where the company is incorporated, and the broker can change its FX fee. The after-buy currency and asset-type mix is recalculated from the adjusted position values.

**ISA left after** = allowance − deposits this tax year − amount invested.

---

## Performance

From `/equity/history/transactions` (all pages):

| Type | Treated as |
|---|---|
| `DEPOSIT` | money in (+) |
| `TRANSFER` | money in/out using its sign (e.g. between your accounts at the broker) |
| `WITHDRAW` | money out (−) |
| `INTEREST_ON_FREE_CASH` | income (shown separately), not a contribution |

```
net money in   = Σ contributions
market growth  = total value − net money in       (and ÷ net money in for %)
```

**Streak:** consecutive calendar months with net money in > 0, counting back from this month (or last month if nothing has gone in yet this month).

**Fees paid:** the sum of `fill.walletImpact.taxes[].quantity` across all filled orders (`CURRENCY_CONVERSION_FEE`, `STAMP_DUTY_RESERVE_TAX`). The % is fees ÷ Σ|`fill.walletImpact.netValue`|.

---

## Real return (XIRR)

The money-weighted, annualised return. It's the rate `r` that solves

```
Σ  CFₖ / (1 + r)^((dₖ − d₀)/365)  = 0
```

with cash flows from your point of view: **deposits negative, withdrawals positive, and today's total value as a final positive flow**. It's solved by bisection between −99% and +1000% (`insights.xirr`). It isn't shown until there are at least 30 days between the first and last flow.

Why it differs from P&L %: £100 that grew 10% over two years is about 4.9% a year, while £100 that grew 10% in two months is far more. XIRR also handles deposits made at different times.

---

## ISA allowance

```
used = Σ DEPOSIT amounts with date ≥ 6 April of the current tax year
left = isa_allowance − used
```

The UK tax year runs from 6 April to 5 April. Transfers are excluded because an ISA transfer-in doesn't use the allowance. If you move money from an Invest account into the ISA, check the figure yourself.

---

## Income

From `/equity/history/dividends` (all pages):

| Figure | Formula |
|---|---|
| Last 12 months | Σ amount where `paidOn` ≥ today − 365 days |
| Yield on cost | last 12 months ÷ Invested |
| Average per month | last 12 months ÷ 12 |
| Next 12m estimate | equals the last 12 months (simple "payouts repeat" assumption) |
| Top payers | last-12-month total per ticker, with its share of the total |

---

## Concentration and overlap

With look-through position weights `wᵢ` (summing to 1):

| Figure | Formula |
|---|---|
| Top 5 | Σ of the five largest `wᵢ` |
| Effective number of holdings | `1 / Σ wᵢ²` (inverse Herfindahl index). Equal weights across N give N. One dominant position gives close to 1 |
| Positions under 1% | count and Σ value where `wᵢ < 1%` |

**Overlap** (`insights.overlap_groups`) groups positions by:

- **identical ISIN:** the same fund on two listings, shown in red;
- **index family** from the instrument name: *S&P 500*, *S&P 500 (sector slice)*, *FTSE All-World*, *FTSE All-World (high dividend)*, *MSCI World / ACWI*, *Gold*.

A family is only listed when it has two or more members and isn't already covered by an identical-ISIN group.

---

## Risk from snapshots

Uses one value per day from `snapshots/portfolio_history.csv` (the last row of each date). Each day's return removes that day's net contributions:

```
rₜ = (Vₜ − Vₜ₋₁ − flowsₜ) / Vₜ₋₁
index₀ = 1,  indexₜ = indexₜ₋₁ × (1 + rₜ)
```

| Figure | Formula |
|---|---|
| Return over the period | `index_last − 1` |
| Max drawdown | min over t of `indexₜ / max(index₀..ₜ) − 1` |
| Below the peak now | `index_last / max(index) − 1` |
| Best / worst day | max / min `rₜ` |
| Volatility (annualised) | `stdev(rₜ) × √252` (needs 10+ returns) |

Snapshot gaps (days the dashboard didn't run) are treated as a single step between the two dates.

---

## Value schedule (History page)

`insights.value_schedule` uses one value per day (the last snapshot row of each date; today is live). For consecutive rows *p → c*:

```
change      = Vc − Vp                      (and ÷ Vp for %)
money in    = Σ contributions with  p.date < date ≤ c.date
market move = change − money in
```

The summary covers the first to the last row. Best/worst day use only rows whose gap from the previous row is ≤ 3 days.

## Currencies

- **Account currency (£):** values, costs, P&L, returns, dividends, fees. These **include FX movements**.
- **Quote currency:** Entry, Price and order fill prices, shown in the instrument's own currency. `GBX` is pence sterling, shown as `694.3p`.
- **GBX counts as GBP** for currency exposure.
- **Price colour vs return colour:** a US stock can be up in dollars (price green) while down in pounds (return red) if the dollar weakened.
