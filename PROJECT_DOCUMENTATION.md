# Trading 212 Portfolio Monitor: Project Documentation

**Personal investment analytics | Trading 212 Public API (v0) | Stocks ISA / Invest | Python terminal application**

This document explains the investor problem, how a Trading 212 account is structured, the system's architecture, its six-stage data lifecycle, the analytical measures, the validation performed and the known limitations. It is written for readers who want to understand **what the project does, why each design choice matters, how to reproduce it, and which results are estimates rather than reported figures**.

**Project status (1 November 2026):**
- **Data:** the monitor runs against a live Trading 212 Stocks ISA, with all five pages working from complete account history: 115 transactions, 158 orders and 28 dividends after the pagination fix.
- **Demo mode:** reproduces every page from a fictional portfolio, without credentials.
- **Testing:** rendering and keyboard interaction were tested in a pseudo-terminal, and contributions were reconciled to the account's reported cost.
- **Not yet built:** a benchmark comparison (needs a third-party price source) and an automated unit test suite.
- **Estimates:** fee and stamp-duty figures in the planner are estimates, not broker quotes.

## Contents

### Part I: Background and Research Context

- [Research Question and Executive Summary](#research-question-and-executive-summary)
- [Retail Investing on Trading 212](#retail-investing-on-trading-212)
- [Objectives, Scope and Definitions](#objectives-scope-and-definitions)

### Part II: Research Methodology

**Methodology: Six-Stage Data Analytics Lifecycle**

1. [Data Sourcing](#stage-1-data-sourcing): identify the authoritative account data and its limits.
2. [Data Ingestion](#stage-2-data-ingestion): collect live and historical records within rate limits.
3. [Data Cleaning & Transformation](#stage-3-data-cleaning--transformation): standardise currencies, pies and instrument metadata.
4. [Data Modelling & Analytics](#stage-4-data-modelling--analytics): calculate performance, income, risk and allocation measures.
5. [Data Preparation for Reporting](#stage-5-data-preparation-for-reporting): maintain reporting state, snapshots and investor targets.
6. [Data Visualisation & Insights](#stage-6-data-visualisation--insights): present decisions on a five-page terminal interface.

### Part III: Findings, Validation and Project Outcomes

- [Verified Run: 1 November 2026](#verified-run-1-november-2026)
- [Findings from a Live Account](#findings-from-a-live-account)
- [Data Quality, Auditability and Limitations](#data-quality-auditability-and-limitations)
- [Reproducibility and Operational Workflow](#reproducibility-and-operational-workflow)
- [Project Deliverables and Next Steps](#project-deliverables-and-next-steps)
- [Glossary and Reference Material](#glossary-and-reference-material)

---

# PART I: BACKGROUND AND RESEARCH CONTEXT

## Research Question and Executive Summary

### Why I undertook this project

I invest monthly through a Trading 212 Stocks ISA, using a mix of index funds, individual shares and a dividend "pie". The app shows each holding's value and gain. It doesn't answer the questions that matter when new money arrives:
- Where should this month's deposit go to keep the portfolio balanced?
- Is my investing actually working, once you allow for *when* each deposit was made?
- What am I paying in fees?
- Am I as diversified as the number of holdings suggests?

I developed this project to apply **financial analysis, accounting discipline and software engineering** to my own investing. The aim was a tool I would actually leave running. Every figure needed a clear definition, and estimates had to be distinguished from reported values.

### Research question

> **For a regular monthly investor, where should the next deposit go, and is the portfolio genuinely performing, diversified and cost-efficient?**

The question has two parts:
- **A forward-looking decision:** how to allocate new money without selling.
- **A backward-looking assessment:** money-weighted performance, income, costs and concentration.

Neither can be read directly from the broker's app.

### What the account data shows on its own

Trading 212 reports an account value, the cost of current holdings and an unrealised profit or loss. These are accurate, but limited in three ways:

1. **Unrealised return ignores timing.** A 10% gain earned over two years is not the same as 10% earned in two months.
2. **Pies hide their contents.** A pie appears as one holding, but its stocks change your currency exposure and can overlap with funds you hold directly.
3. **The number of holdings overstates diversification.** Fifty small positions alongside a few large funds behave like a much smaller portfolio.

### Analytical approach

The project uses the official Trading 212 Public API as its only source of account data:
- **Python** for ingestion, transformation and analysis, in a module of pure functions (`insights.py`) that can be checked independently of the interface.
- **Daily CSV snapshots** for history the API doesn't provide.
- **A full-screen terminal interface** built with Rich for presentation.

Each measure is documented with its formula in [METRICS.md](docs/METRICS.md). A clear chart is not a substitute for a correctly defined measure.

---

## Retail Investing on Trading 212

### Account structure

| Concept | Meaning for the analysis |
| --- | --- |
| **Stocks ISA** | UK tax-free wrapper with an annual subscription allowance of £20,000 (tax year 6 April to 5 April) |
| **Position** | One instrument held, including any quantity inside pies (`quantityInPies`) |
| **Pie** | A user-defined basket with target weights. Deposits are split by those targets. It has its own value, cost and cash |
| **Fractional shares** | Value orders in £; Trading 212's minimum order is £1 |
| **Quote currency** | The currency an instrument trades in: USD, EUR, GBP, or **GBX** (pence) for many London listings |
| **Account currency** | GBP. Values, costs and returns are converted and **include FX movements** |

### Costs relevant to a monthly investor

| Cost | When it applies | Rate used for estimates |
| --- | --- | --- |
| FX conversion | Buying instruments quoted in a non-GBP currency | 0.15% |
| Stamp Duty Reserve Tax | Buying UK shares (ETFs are exempt) | 0.5% |

Actual fees charged are read from each order fill (`fill.walletImpact.taxes`). The rates above are used only to *estimate* the cost of planned purchases.

### The Trading 212 Public API

The API is in beta. It is read-only for this project's purposes (no order permissions are needed) and is rate-limited **per account**. It provides current positions, pies, instrument metadata and paginated history of orders, dividends and transactions. **It doesn't provide historical prices or portfolio values**, which shaped the snapshot design in Stage 5.

---

## Objectives, Scope and Definitions

**Objectives**

1. Present holdings exactly as the app does (each pie as one line), while analysing everything underneath it ("look-through").
2. Recommend how to split a deposit toward target weights **without selling**, and show the impact of any proposed purchase before it is made.
3. Measure performance as a **money-weighted annualised return (XIRR)** and separate money contributed from market growth.
4. Report income, costs, ISA allowance usage, concentration, fund overlap and deposit-adjusted risk.
5. Run continuously, recover from API failures without losing the screen, and never place orders.

### Scope

| In scope | Out of scope |
| --- | --- |
| One Trading 212 Invest or Stocks ISA account | CFD accounts, other brokers |
| Live positions, pies, full transaction/order/dividend history | Historical market prices, benchmarks (needs an external source) |
| Daily snapshots generated by the monitor | Tax calculations beyond ISA allowance tracking |
| Allocation guidance by user-defined targets | Investment advice or security selection |

### Key definitions

| Term | Definition used |
| --- | --- |
| **Holdings** | App-style view: direct quantity of each instrument plus one row per pie |
| **Positions (look-through)** | Every underlying instrument, including those held in pies |
| **Net money in** | Deposits + signed transfers − withdrawals |
| **Market growth** | Total value − net money in |
| **XIRR** | Annualised rate that sets the net present value of all contributions and today's value to zero |
| **Effective number of holdings** | 1 ÷ Σ(weight²), the inverse Herfindahl index |
| **Overlap** | Positions sharing an ISIN, or tracking the same index family |

---

# PART II: RESEARCH METHODOLOGY

## Methodology: Six-Stage Data Analytics Lifecycle

### Methodology roadmap

The project follows six consecutive stages, named for **the work performed** rather than the library used.

![Trading 212 portfolio monitor six-stage architecture](docs/images/architecture.svg)

| Stage | Purpose | Main assets / implementation |
| --- | --- | --- |
| **1. Data Sourcing** | Identify authoritative account data and its limits | Trading 212 Public API v0; read-only key in `.env` |
| **2. Data Ingestion** | Collect live and historical records reliably | `Trading212Client`, `DataFeed` threads, rate-limit-aware pagination |
| **3. Data Cleaning & Transformation** | Standardise currencies, pies and metadata | `PortfolioAnalytics` (holdings and positions views) |
| **4. Data Modelling & Analytics** | Calculate decision and assessment measures | `insights.py` (pure functions) |
| **5. Data Preparation for Reporting** | Maintain state, history and targets | `DataFeed.state`, `snapshots/*.csv`, `portfolio_config.json` |
| **6. Data Visualisation & Insights** | Communicate results and support decisions | Rich full-screen UI: Overview, Planner, Performance, Income, Risk |

---

## Stage 1: Data Sourcing

**Purpose.** Establish where account data comes from, what it contains and what it cannot provide.

The only source is the official Trading 212 Public API, using a **read-only** key. I used eight endpoints:

| Endpoint | Provides | Approx. rate limit |
| --- | --- | --- |
| `/equity/account/summary` | Total value, cost, unrealised P&L, cash | 1 per 2s |
| `/equity/positions` | Quantity, prices, value, cost, quantity in pies | 1 per 1s |
| `/equity/metadata/instruments` | Instrument type, short name (≈18,500 instruments) | 1 per 50s |
| `/equity/pies` | Pie value, invested amount, result, cash | 1 per 30s |
| `/equity/pies/{id}` | Pie name and per-slice target vs current share | 1 per 5s |
| `/equity/history/orders` | Fills, prices, fees and taxes | 6 per min |
| `/equity/history/dividends` | Dividend payments | 6 per min |
| `/equity/history/transactions` | Deposits, withdrawals, transfers, interest | 6 per min |

I took the payload shapes from live responses, not assumptions. That revealed nested order records (`order` + `fill`), signed transfer amounts and pence-denominated (GBX) prices.

**Output:** A documented source inventory and its constraints. In particular, there is no historical price series.

---

## Stage 2: Data Ingestion

**Purpose.** Collect current and historical data continuously without breaking the interface or exceeding rate limits.

- **Two background threads.** The *live* thread polls summary and positions every 30 seconds, pies at most every 30 seconds, and instruments until they load. The *history* thread downloads **all pages** of orders, dividends and transactions every 30 minutes, waiting out rate limits between pages.
- **Failure isolation.** The API client never raises or prints; a failed call returns `None` and a readable reason. The interface keeps the last good data and shows the reason in its status bar.
- **Pagination.** The API's `nextPagePath` already includes the `/api/v0` prefix. The original implementation appended it again, so **only the first 50 records of any history were ever retrieved**. Correcting this increased coverage to the full history (e.g. 158 orders rather than 50).

**Output:** A lock-protected state object holding the latest complete data set.

---

## Stage 3: Data Cleaning & Transformation

**Purpose.** Convert raw payloads into consistent, analysable records.

- **Two views of the same data.** *Holdings* scale each position to the quantity held outside pies and add one row per pie, matching the app. *Positions* retain every underlying instrument for look-through analysis.
- **Currency handling.** Values and returns use the account currency (GBP, including FX effects); entry and current prices remain in the quote currency, with GBX shown as pence and grouped with GBP for exposure.
- **Enrichment.** Instrument metadata supplies type (ETF or stock) and a clean ticker. Raw tickers such as `VUAGl_EQ` are retained for snapshots, and display names such as `VUAG` are used on screen and as planner keys.
- **Pie valuation.** Value = invested value at average price + uninvested pie cash, matching the figure the app displays.

**Output:** Standardised holdings and positions with documented fields (`_value`, `_cost`, `_pnl`, `_weight`, `_currency`, `_type`, `_price`, `_avg_price`, …).

---

## Stage 4: Data Modelling & Analytics

**Purpose.** Turn standardised records into measures that answer the research question.

All calculations live in `insights.py` as pure functions, separate from presentation:

| Area | Measures |
| --- | --- |
| Decision support | No-sell deposit split toward targets; scenario impact on value, fees, currency mix, asset-type mix, concentration and ISA allowance |
| Performance | Net money in, market growth, XIRR, deposit streak, monthly contributions, fees actually paid |
| Income | Trailing-12-month dividends, monthly income, yield on cost, top payers |
| Structure | Largest holding, top-5 share, effective number of holdings, positions under 1%, index and ISIN overlap |
| Risk | Deposit-adjusted period return, maximum and current drawdown, best and worst day, annualised volatility |

Two design choices matter for correctness:
- **Risk measures remove each day's deposits**, so adding money is not mistaken for a gain.
- **The planner never sells.** It fills the largest shortfalls against targets first, then splits any remainder by target weight.

Formulas: [METRICS.md](docs/METRICS.md).

---

## Stage 5: Data Preparation for Reporting

**Purpose.** Supply each page with consistent, up-to-date inputs, and create the history the API lacks.

- **Daily snapshots.** On the first refresh of each day the monitor appends portfolio and position rows to CSV files (`snapshots/`). These feed the daily value chart and the risk measures, and can be analysed in Excel or Power BI. They are kept **locally only**, never committed.
- **Investor targets.** `portfolio_config.json` stores the monthly deposit, ISA settings and target weights. It is created from current weights on first run and edited from the Planner page.
- **Demo data.** `demo_data.py` produces a complete, fictional data set in the same shape as the API, including generated snapshot history, so the whole lifecycle can be reviewed without an account.

---

## Stage 6: Data Visualisation & Insights

**Purpose.** Present the analysis where decisions are made, in a view that can run all day.

The interface is a full-screen terminal application that updates in place every second (countdown) and every 30 seconds (data). It adapts to the terminal: two columns at 140+ characters, stacked below that, with the value chart taking whatever height remains.

| Page | Decision it supports |
| --- | --- |
| [Overview](docs/images/page-1-overview.svg) | What do I own, and how is it doing today? |
| [Planner](docs/images/page-2-planner.svg) | Where should this deposit go, and what will it change? |
| [Performance](docs/images/page-3-performance.svg) | Is my investing working after allowing for timing and costs? |
| [Income](docs/images/page-4-income.svg) | What is the portfolio paying me? |
| [Risk](docs/images/page-5-risk.svg) | How concentrated, overlapping and volatile is it? |

A web front end was prototyped and **removed**, because the terminal suited continuous monitoring better.

---

# PART III: FINDINGS, VALIDATION AND PROJECT OUTCOMES

## Verified Run: 1 November 2026

| Check | Result |
| --- | --- |
| Live account connection (read-only key) | Passed |
| Full history retrieval after pagination fix | 115 transactions, 158 orders, 28 dividends |
| Net money in vs Trading 212's reported cost of holdings | Agreed to within £0.22 |
| Holdings view vs Trading 212 app (pie as one line, direct quantities) | Matched line by line |
| All five pages rendered at 160×50 and 110×45 | Passed (narrow layout drops the orders panel to fit) |
| Keyboard interaction in a pseudo-terminal (pages, planner edit and save, scenario mode, quit) | Passed, no exceptions |
| Rate limiting with a second client on the same account | Last good data retained; reason shown |
| Demo mode end-to-end, without credentials | Passed |
| Credentials or account data in the repository | None (`.env`, snapshots and targets git-ignored) |

---

## Findings from a Live Account

Applying the monitor to my own account produced the following. **Monetary balances are deliberately omitted; only ratios and counts are shown.**

![Findings from a live account](docs/images/key-findings-table.svg)

1. **Diversification was lower than it looked.** 54 positions behaved like 11.3 equal-sized holdings, because a few funds and the pie dominated.
2. **The portfolio was fragmented.** 44 positions were each under 1%, together 27% of value: too small to matter individually, but time-consuming to monitor.
3. **Funds overlapped.** Three S&P 500 trackers held 24% of the portfolio and two FTSE All-World funds 14%, adding lines without adding diversification. One gold ETC was held on two listings with the same ISIN.
4. **Performance needed timing context.** The money-weighted return was 10.5% a year, compared with a 10.0% simple unrealised return.
5. **Costs were low but not zero.** Fees were 0.13% of the amount traded, mostly FX conversion on US shares.
6. **Data completeness was a real risk.** Before the pagination fix, any analysis of contributions, fees or income would have silently used only the first 50 records.

**Why it matters.** These are exactly the issues a monthly investor can act on: consolidating overlapping funds, directing new money toward under-target holdings, and preferring GBP-quoted funds where FX fees are avoidable. **They are observations about one account, not investment advice.**

---

## Data Quality, Auditability and Limitations

### Controls that make the results credible

- Every measure has a written formula, implemented in one place (`insights.py`).
- Reported figures (values, costs, fees charged, dividends received) come directly from the API. **Estimates** (planned fees, the next-12-month income estimate) are labelled as such.
- Contributions were reconciled to the account's reported cost.
- Failures are visible: the status bar shows the reason, and stale data is never presented as fresh.

### Known limitations

| Limitation | Effect | Mitigation / next step |
| --- | --- | --- |
| No historical prices from the API | No benchmark comparison; risk metrics need the monitor's own snapshots | Add an external price source |
| Snapshots exist only for days the monitor ran | Gaps are treated as a single step | Run daily; document gaps |
| Several pies are combined for look-through | Allocation inside each pie is approximated | Use per-pie slice quantities |
| Fee and stamp-duty estimates use fixed rates | Planned costs are approximate | Compare with fees actually charged (shown on Performance) |
| ISA usage counts deposits only | Transfers from other accounts need checking | Flag transfers explicitly |
| No automated test suite yet | Regressions rely on manual and scripted checks | Add unit tests for `insights.py` |

---

## Reproducibility and Operational Workflow

### Prerequisites

Python 3.9+, and a Trading 212 Invest or Stocks ISA account with a read-only API key. **Or** no account at all, using demo mode.

### Sequence

```bash
git clone <this repository> && cd <repository>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python trading212_dashboard.py --demo         # fictional portfolio, no key needed

cp .env.example .env                          # add your read-only API key
python trading212_dashboard.py                # live account
python trading212_dashboard.py --invest 250   # plan a £250 deposit
python tools/render_screenshots.py            # regenerate README images (demo data)
```

Operational guidance, keys and troubleshooting: [User Guide](docs/USER_GUIDE.md). Code structure and testing approach: [Development Guide](docs/DEVELOPMENT.md).

---

## Project Deliverables and Next Steps

| Deliverable | Repository evidence | Status |
| --- | --- | --- |
| API client with rate-limit-aware, complete pagination | `trading212_dashboard.py` (`Trading212Client`) | Complete; pagination defect found and fixed |
| Holdings/positions transformation with pie look-through | `PortfolioAnalytics` | Complete; matches the app |
| Analytical measures | `insights.py`, [METRICS.md](docs/METRICS.md) | Complete |
| Interactive planner with scenario impact | Planner page | Complete; fee figures are estimates |
| Five-page live terminal interface | `build_screen`, page builders | Complete |
| Demo data and reproducible screenshots | `demo_data.py`, `tools/render_screenshots.py` | Complete |
| Benchmark comparison | | Not started (requires an external price source) |
| Automated tests | | Not started |

### Recommended order of completion

1. Add unit tests for `insights.py` (XIRR, planner split, drawdown, overlap).
2. Add an external price source for a benchmark ("what if every deposit had gone into a global index fund").
3. Model several pies separately for look-through allocation.
4. Optionally export snapshots to a format suited to Power BI.

### What the project demonstrates

- **API integration:** with rate limits and robust failure handling.
- **Financial measurement:** money-weighted returns, look-through allocation, deposit-adjusted risk, concentration indices.
- **Decision-support design:** a no-sell allocation algorithm with a before-and-after impact preview.
- **Disciplined reporting:** reported figures, estimates and limitations are kept clearly separate.

---

## Glossary and Reference Material

| Term | Meaning |
| --- | --- |
| ISA | Individual Savings Account, a UK tax-free wrapper |
| Pie | Trading 212 basket of instruments with target weights |
| Look-through | Analysing the instruments inside a pie or fund wrapper, not the wrapper alone |
| GBX | Pence sterling; the quote currency of many London listings |
| XIRR | Extended internal rate of return: annualised, money-weighted return for irregular cash flows |
| Herfindahl index | Σ(weight²), a concentration measure. Its inverse is the effective number of holdings |
| Drawdown | Fall from a previous peak value |
| SDRT | Stamp Duty Reserve Tax, 0.5% on purchases of most UK shares |
| ETF / ETC | Exchange-traded fund / commodity |

### Primary references

- [Trading 212 Public API documentation](https://t212public-api-docs.redoc.ly/)
- [Trading 212 Help Centre: API](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157)
- [HMRC: Individual Savings Accounts](https://www.gov.uk/individual-savings-accounts)
- [HMRC: Stamp Duty Reserve Tax](https://www.gov.uk/guidance/stamp-duty-reserve-tax-the-basics)

### Repository technical references

- [User Guide](docs/USER_GUIDE.md): operation, pages, planner, configuration, troubleshooting
- [Metrics](docs/METRICS.md): formula for every figure
- [Development Guide](docs/DEVELOPMENT.md): architecture, endpoints, testing approach
- [`insights.py`](insights.py) and [`trading212_dashboard.py`](trading212_dashboard.py)

---

*This document is a transparent technical and financial record. Figures labelled as estimates should not be read as broker-reported values, and the live-account findings describe one portfolio, not general investment advice.*
