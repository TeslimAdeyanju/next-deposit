# Trading 212 Portfolio Monitor

This project began with a practical question I faced every month: *where should my next deposit go, and is my investing actually working?* The Trading 212 app shows what each holding is worth, but not whether the portfolio is balanced, how its return compares once deposit timing is considered, what it costs, or how much its funds overlap. I built a live terminal monitor on the official Trading 212 Public API. It uses Python and financial analysis techniques to answer those questions every time new money goes in.

## The research question

> **For a regular monthly investor, where should the next deposit go, and is the portfolio genuinely performing, diversified and cost-efficient?**

**What the app shows on its own:** account value, the cost of current holdings and an unrealised gain. These are accurate but incomplete:
- the return ignores *when* each deposit was made;
- a pie appears as one holding, even though its stocks change currency exposure and can overlap with funds held directly;
- the number of holdings overstates diversification.

**How I investigated it:** I connected to the Trading 212 Public API with a read-only key.
- **Data:** I retrieved the complete history of transactions, orders and dividends, and standardised currencies and pie holdings.
- **Measures:** I implemented each financial measure as a documented, independently testable function.
- **Interface:** the monitor runs as a five-page, full-screen terminal application. Its planner recommends how to split a deposit toward target weights **without selling**, and previews the impact of any purchase before it's made.

[Explore the full project methodology and documentation](PROJECT_DOCUMENTATION.md).

## Key findings

Applying the monitor to my own Stocks ISA produced the findings below. **Balances are deliberately omitted; only ratios and counts are shown.**

<img src="docs/images/key-findings-table.svg" alt="Findings on a live account: 54 positions behave like 11.3; 44 positions under 1% equal 27% of value; three S&P 500 funds hold 24% and two All-World funds 14%; one gold ETC held on two listings; 10.5% a year money-weighted return versus 10.0% simple; fees 0.13% of amount traded." width="100%" />

<details>
<summary>View the findings as a text table</summary>

<table width="100%">
  <thead><tr><th align="left" width="30%">Question</th><th align="left" width="40%">Finding on a live account</th><th align="left" width="30%">Why it matters</th></tr></thead>
  <tbody>
    <tr><td>How diversified is it really?</td><td><strong>54</strong> positions behave like <strong>11.3</strong> equal holdings</td><td>A few holdings drive results</td></tr>
    <tr><td>Is it fragmented?</td><td><strong>44</strong> positions under 1% = <strong>27%</strong> of value</td><td>Hard to monitor, little individual effect</td></tr>
    <tr><td>Do funds overlap?</td><td>3 S&amp;P 500 funds (<strong>24%</strong>), 2 FTSE All-World funds (<strong>14%</strong>)</td><td>Same index, no extra diversification</td></tr>
    <tr><td>Any duplicate holdings?</td><td>One gold ETC held on <strong>two</strong> listings</td><td>Same ISIN held twice</td></tr>
    <tr><td>Is the investing working?</td><td><strong>10.5%</strong> a year (XIRR) vs 10.0% simple return</td><td>Deposit timing matters</td></tr>
    <tr><td>What does it cost?</td><td>Fees <strong>0.13%</strong> of the amount traded</td><td>FX conversion is the largest cost</td></tr>
  </tbody>
</table>

</details>

**Why it matters:** each finding points to something a monthly investor can act on: consolidating overlapping funds, directing new money toward under-target holdings, and preferring GBP-quoted funds where FX fees can be avoided.

**Basis and limitations:**
- **Reported vs estimated figures:** values, costs, fees charged and dividends come directly from the Trading 212 API. Planned-purchase fees and the next-12-month income figure are labelled **estimates**.
- **No historical prices:** the API provides none, so trend and risk measures are built from the monitor's own daily snapshots, and there's no benchmark comparison yet.
- **One account:** the findings describe a single account and are not investment advice.

## The monitor

<img src="docs/images/page-2-planner.svg" alt="Planner page (demo data): target weights, drift, suggested buys and a before-and-after impact panel showing fees, currency mix, asset type and ISA allowance." width="100%" />

<details>
<summary>View all five pages (demo data)</summary>

| Page | Decision it supports |
| --- | --- |
| [1 Overview](docs/images/page-1-overview.svg) | What do I own, how is it doing, and how is the value trending? |
| [2 Planner](docs/images/page-2-planner.svg) | Where should this deposit go, and what will it change? |
| [3 Performance](docs/images/page-3-performance.svg) | Money in vs market growth, XIRR, deposit streak, ISA allowance, fees |
| [4 Income](docs/images/page-4-income.svg) | Dividends by month, yield on cost, top payers |
| [5 Risk](docs/images/page-5-risk.svg) | Concentration, fund overlap, drawdown and volatility |

<img src="docs/images/page-1-overview.svg" alt="Overview page (demo data)" width="100%" />
<img src="docs/images/page-3-performance.svg" alt="Performance page (demo data)" width="100%" />
<img src="docs/images/page-4-income.svg" alt="Income page (demo data)" width="100%" />
<img src="docs/images/page-5-risk.svg" alt="Risk page (demo data)" width="100%" />

</details>

All screenshots use a **fictional demo portfolio** (`--demo`), never a real account. They are regenerated with `python tools/render_screenshots.py`.

## Data architecture

![Trading 212 portfolio monitor six-stage architecture](docs/images/architecture.svg)

The six stages are Data Sourcing, Data Ingestion, Data Cleaning & Transformation, Data Modelling & Analytics, Data Preparation for Reporting, and Data Visualisation & Insights:
- **Ingestion** runs on two background threads, so the interface never blocks on the API.
- **Analysis** sits in `insights.py` as pure functions, separate from presentation.
- **Your data stays on your machine:** daily snapshots and investment targets are kept locally and are never committed.

Endpoint choices, transformations and design decisions are explained in [Project Documentation](PROJECT_DOCUMENTATION.md).

### Verified status: 1 November 2026

- Live Stocks ISA connected with a read-only key.
- Complete history retrieved after fixing a pagination defect that had limited every history call to its first 50 records: 115 transactions, 158 orders and 28 dividends.
- Net money in reconciled to Trading 212's reported cost of holdings to within £0.22.
- Holdings view matched the Trading 212 app line by line, including the pie.
- All five pages rendered at 160×50 and 110×45. Keyboard interaction tested in a pseudo-terminal without exceptions.
- Demo mode runs end to end without credentials. No credentials or account data are in the repository.

## Run it

```bash
git clone https://github.com/TeslimAdeyanju/Trading212-Portfolio-Monitor.git
cd Trading212-Portfolio-Monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python trading212_dashboard.py --demo      # try it with a fictional portfolio, no account needed
```

To use your own account, copy `.env.example` to `.env` and add a **read-only** API key (Trading 212 app → Settings → API), then run `python trading212_dashboard.py`. Press `1`–`5` to switch pages, use `↑` `↓` and `Enter` on the Planner, and `q` to quit.

## Documentation and reproducibility

| Resource | Contents |
| --- | --- |
| [Project Documentation](PROJECT_DOCUMENTATION.md) | Research question, account context, six-stage methodology, validation, findings and limitations |
| [User Guide](docs/USER_GUIDE.md) | Daily use, every page explained, planner walkthroughs, configuration, troubleshooting |
| [Metrics](docs/METRICS.md) | Definition and formula for every figure: XIRR, planner split, fee estimates, effective holdings, drawdown |
| [Development Guide](docs/DEVELOPMENT.md) | Code structure, data flow, endpoints and rate limits, testing without the API |

**Source:** [Trading 212 Public API](https://t212public-api-docs.redoc.ly/) (beta; Invest and Stocks ISA accounts).

The monitor is read-only and never places orders. Not affiliated with Trading 212. This is an analysis tool, not financial advice.

## Author

**Teslim Adeyanju** | [Portfolio](https://adeyanjuteslim.co.uk) | [LinkedIn](https://www.linkedin.com/in/adeyanjuteslimuthman)
