# EdgeOS: multi-agent stock market platform — product spec

Oct 27, 2026 · @Robin

## Product overview

EdgeOS is a one-stop website for Indian investors, traders and families: 25 specialist AI agents, each owning one task, run in parallel under a mother agent that verifies, debates and publishes one combined analysis. It replaces the 5–6 tools traders juggle today (Screener, Tickertape, Sensibull, Opstra, AlgoTest, TradingView, a journal) and adds what none of them have: personal, honest, agent-driven checks.

**Who it is for**

- **Long-term investors:** deep stock and mutual fund research, a portfolio watchdog.
- **F&O traders:** option chain, strategy builder, backtests, pre-trade checks, tilt alerts.
- **Families:** shared net worth, goals, lost-money recovery, succession readiness.

**Positioning:** one platform at ₹299–499/month Pro, below Opstra and Sensibull's non-Zerodha Pro (₹1,300/month list), with a free tier for search traffic.

**Product principles**

1. **One agent, one job.** Each agent has a fixed input contract, output schema and data source; it never does another agent's work.
2. **Code computes, AI explains.** Every number comes from Python or SQL; language models choose what to compute and explain results.
3. **Every claim has a source** and passes the verifier before it reaches the page.
4. **Describe, never advise.** No buy, sell or hold calls; the compliance guard enforces this.
5. **Precompute what is shared, compute live what is personal.** Stock and fund pages come from nightly batches; portfolios, trades and family data run on demand.

## System architecture

Every request goes through the mother agent twice: once to plan and dispatch, once to verify and synthesize.

&#91;embedded content: system architecture · planner, 5 desks, judge\]

Markets and mutual funds desks read from nightly batch results, so public pages load from cache; the F&O, You and Family desks run on demand on the user's own data. All desks share one data layer: EOD prices, filings, AMFI NAV, the user's broker session and uploads (detailed under Data sources).

## Mother agent

The mother agent runs in two roles: a planner that decides which specialists to call, and a judge that verifies, debates and synthesizes their outputs. It never computes market numbers itself.

**Planner**

- **Input:** user request (query text or page context), user profile (holdings, broker link, family members, plan tier), cached batch results available.
- **Steps:** classify intent (stock research, fund research, F&O, personal, family), select the minimum set of agents, check cache freshness, dispatch uncached agents in parallel with typed task payloads, set a time budget per agent.
- **Output:** a task graph (agent, payload, deadline, dependencies) written to the job queue.

**Judge**

1. **Collect:** every specialist returns the standard envelope (below). Missing or timed-out agents are marked, never guessed.
2. **Verify:** a verifier sub-agent re-runs each headline number against the source query; a mismatch beyond tolerance drops the claim and logs it.
3. **Debate:** a bull sub-agent and a bear sub-agent each argue from the verified findings only, two rounds, citing finding ids.
4. **Synthesize:** writes the page report: summary, key findings with sources, bull vs bear, risks, data gaps, confidence (high, medium, low).
5. **Compliance guard:** removes any buy, sell, hold, target price or return promise; adds the standard disclaimer.

**Standard agent output envelope** (every specialist returns this)

| Field | Type | Meaning |
| --- | --- | --- |
| agent\_id | string | Which specialist produced it |
| subject | string | Stock symbol, fund code, user id or family id |
| as\_of | timestamp | Data freshness |
| metrics | JSON | Computed numbers, each with formula id and source query id |
| findings | list | Plain-language statements, each linked to metric ids |
| flags | list | Severity (info, watch, alert) plus reason |
| sources | list | Dataset, filing URL or API call behind each metric |
| confidence | enum | High, medium, low, with reason |
| gaps | list | Data the agent needed but could not get |

**Model sizing:** planner and judge use a strong model; specialists use a small fast model for interpretation only.

## Markets desk (7 agents)

The markets desk powers every stock page and runs nightly for all listed stocks, so pages load from cache. Phase 1 ships all seven.

| Agent | Inputs | Outputs | Data sources | Runs |
| --- | --- | --- | --- | --- |
| M1 Price and technicals | Symbol, daily OHLCV history (10y), index series | Trend state, RSI, MACD, moving averages, support/resistance zones, 52-week position, relative strength vs Nifty | EOD bhavcopy from NSE/BSE archives; licensed vendor for intraday later | Nightly |
| M2 Fundamentals | Symbol, quarterly and annual statements (10y) | Revenue/profit CAGR, margins, ROE, ROCE, debt/equity, cash conversion, growth quality score | Exchange XBRL filings; fundamentals vendor if coverage gaps | Nightly, plus on results day |
| M3 Forensic red flags | M2 outputs, shareholding pattern, auditor reports, related-party notes | Red-flag score (0–100) with each flag: promoter pledge rise, auditor resignation, CFO vs PAT gap, contingent liabilities, frequent equity dilution | Shareholding and pledge disclosures, annual reports, exchange announcements | Nightly |
| M4 Filings and concalls | New announcements, results, concall transcripts or audio | Plain-language summary per filing, management guidance extracted, tone change vs last quarter, material-event tag | NSE/BSE corporate announcements feed, company IR pages | Within minutes of filing |
| M5 News and sentiment | Symbol, company name, aliases | Headline list with sentiment score, volume of coverage, event tags | Licensed news API or RSS of financial publishers | Hourly |
| M6 Peers and sector | Symbol, sector map, M2 outputs of peers | Percentile rank vs peers on growth, margins, returns, valuation | Sector classification (exchange industry codes), M2 cache | Nightly |
| M7 Valuation | M2 outputs, price, peer multiples | P/E, EV/EBITDA, P/B bands vs own 10y history and peers, reverse-DCF implied growth | M1, M2, M6 caches | Nightly |

**Output rule:** M7 shows ranges and implied assumptions only, never a target price.

## F&O desk (5 agents)

The F&O desk needs live option data, which in Phase 2 flows through each user's own broker login (the Sensibull model) once Zerodha multi-user platform access is approved. Backtests run on end-of-day option history.

| Agent | Inputs | Outputs | Data sources | Runs |
| --- | --- | --- | --- | --- |
| F1 Option chain and OI | Underlying, expiry, live chain snapshot | OI and change by strike, PCR, max pain, long/short build-up classification, support/resistance from OI | User's broker session (Kite Connect quote/WebSocket); EOD F&O bhavcopy for history | Live, every 1–5 min while page open |
| F2 Volatility | Chain snapshot, IV history | IV per strike (Black-76), IV rank and percentile, skew, term structure, expected move to expiry, IV-crush risk around events | F1 output, EOD option history | Live plus nightly history |
| F3 Strategy builder | Legs (strike, type, qty, side), spot, IV, days to expiry, lot size | Payoff at expiry and today, Greeks per leg and net, breakevens, max profit/loss, margin estimate, probability of profit | F1, F2, exchange lot-size and margin files | On demand |
| F4 Backtest | Strategy in plain English or rules, date range, cost model | Parsed rule set (shown to user to confirm), equity curve, win rate, max drawdown, expectancy after brokerage, STT, exchange fees, slippage | EOD option history; charges engine | On demand, queued |
| F5 Event risk | Underlying, holdings or open position | Upcoming events within expiry window: results, RBI policy, budget, ex-dates, expiry day, with historical move size on similar events | Corporate event calendar, RBI calendar, exchange holiday and expiry calendar, M1 history | Nightly plus on demand |

**Build note:** F4 shows the parsed rules back to the user before running, so a misread strategy is caught before results are trusted.

## Mutual funds desk (3 agents)

The mutual funds desk runs entirely on public daily NAV and monthly portfolio data, so it ships in Phase 1 with no market-data licence.

| Agent | Inputs | Outputs | Data sources | Runs |
| --- | --- | --- | --- | --- |
| MF1 Fund X-ray | Scheme code(s) or the user's CAS holdings | Stock-level overlap across funds, effective sector and stock exposure, expense ratio, regular vs direct cost gap in rupees over 10 and 20 years | AMFI daily NAV, monthly portfolio disclosures from AMC sites, scheme expense data | Nightly; on demand for a user's portfolio |
| MF2 Manager and consistency | Scheme code, manager history | Rolling 1/3/5-year returns vs benchmark and category, downside capture, manager tenure, performance before and after manager change | AMFI NAV history, AMC factsheets, benchmark index series | Monthly, plus on manager change |
| MF3 Closet indexer detector | Scheme portfolio, benchmark constituents | Active share vs benchmark, tracking error, fee paid per unit of active share, closet-indexer flag | Monthly portfolio disclosures, index constituent files | Monthly |

**Output rule:** MF agents compare and describe; they never say switch or exit a fund.

## You desk (5 agents)

The You desk is the retention moat: it works only on the user's own data and runs on demand or on the user's schedule. All agents are read-only; none places orders.

| Agent | Inputs | Outputs | Data sources | Runs |
| --- | --- | --- | --- | --- |
| Y1 Portfolio watchdog | Holdings, M3/M4/MF2 outputs for held items | Alerts only on material events (results surprise, pledge change, auditor exit, fund manager change), in Hindi or English, on site, email or WhatsApp | Kite holdings (read-only), CAS PDF upload, M3/M4/MF2 caches | Event-driven |
| Y2 Edge test | Tradebook (CSV or broker history), charges model | Net P&L after charges, Monte Carlo edge verdict, SEBI cohort percentile, Nifty counterfactual, top 3 leaks, shareable report card | Tradebook upload, charges engine, SEBI F&O study tables, Nifty EOD | On upload |
| Y3 Tilt guardian | Live positions and orders, user's loss limits | Real-time alerts: daily loss limit hit, size-up after losses, rapid re-entry, trading after set hours | Kite positions/orders polled read-only | Every 30–60 s during market hours, opt-in |
| Y4 Pre-trade check | Intended trade, F2, F5, Y2 history, current capital | Warnings: event risk, IV percentile, round-trip cost and breakeven move, user's record on similar setups, size vs normal | F2, F5, Y2, charges engine | On demand, under 3 s target |
| Y5 Tax agent | Tradebook, holdings with buy dates and cost | Realised and unrealised gains split by short/long term, F&O turnover and business P&L, loss carry-forward, harvesting windows | Tradebook, holdings, current tax rules table (reviewed each budget) | On demand, plus March reminder |

**Privacy rule:** personal data is stored encrypted, never used to train models, and deletable by the user at any time.

## Family desk (5 agents)

The family desk extends the platform to everyone in a household and can reuse the existing Family CFO codebase. It ships in Phase 4.

| Agent | Inputs | Outputs | Data sources | Runs |
| --- | --- | --- | --- | --- |
| FA1 Family net worth | Each member's CAS, holdings, FDs, insurance, manual entries | Consolidated net worth, asset mix, concentration, per-member view with consent controls | CAS PDFs, Kite holdings, manual entry; Account Aggregator later | Monthly refresh, on upload |
| FA2 Goals and SIP planner | Goals (amount, date), current assets, risk band | Required monthly SIP per goal, gap vs current SIPs, on-track status, scenario ranges | FA1, MF2 return history | On demand, monthly check |
| FA3 Lost money finder | Member names, PANs (with consent), photos of old share certificates, warrants, passbooks | List of likely unclaimed items with amount and route (IEPF, bank, insurer, AMC, EPF) | OCR of uploads; public search portals used by the user (IEPF, RBI UDGAM, insurer and AMC tracing tools) | On demand |
| FA4 Paperwork and chaser | Items from FA3, member documents | Pre-filled forms (transmission request, IEPF-5, bank claim), document checklist, scheduled follow-up emails, status tracker | Form templates per route, user's email (with approval), registrar contacts | On demand plus scheduled follow-ups |
| FA5 Guardian | Family accounts, nominee data, policy dates | Nominee gaps, heir vault (what we own and where), renewal reminders for policies and KYC | FA1, user-entered nominee and policy data | Monthly |

**Human-in-the-loop rule:** FA3 never logs into government portals or solves CAPTCHAs; it prepares everything and the user completes those steps. FA4 sends nothing without the user's approval of each message.

## Data sources and licensing

Phase 1 runs only on end-of-day, filing and NAV data, which avoids the costly real-time redistribution licence. Live prices arrive later through each user's own broker session.

| Data | Source | Used by | Licence status | Phase |
| --- | --- | --- | --- | --- |
| EOD prices (equity, F&O) | NSE/BSE daily bhavcopy archives | M1, M7, F4, Y2 | Check exchange terms for display; delayed/EOD licence is far cheaper than real-time | 1 |
| Company financials | Exchange XBRL filings; fundamentals vendor for gaps | M2, M3, M6, M7 | Filings public; vendor contract if used | 1 |
| Corporate announcements | NSE/BSE announcement feeds; TrueData corporate announcements API as paid option | M4, Y1 | Public pages or vendor licence | 1 |
| Mutual fund NAV and portfolios | AMFI NAV files, AMC monthly disclosures | MF1–MF3, FA2 | Public | 1 |
| News | Licensed news API or publisher RSS | M5 | Per-provider terms; headlines and links only | 1 |
| Live quotes and option chain | User's broker session (Kite Connect multi-user platform access) | F1, F2, F3, Y4 | Needs Zerodha platform approval (email kiteconnect(at)zerodha.com) | 2 |
| Real-time feed for all users | Authorised vendor (TrueData, Global Datafeeds) | All live views | Separate exchange redistribution licence; quote on request | Later, if needed |
| User holdings and trades | Kite read-only, CAS PDF, tradebook CSV | Y1–Y5, FA1 | User consent | 2–3 |
| Unclaimed asset routes | IEPF, RBI UDGAM, insurer and AMC tracing portals (user-operated) | FA3, FA4 | User performs logins | 4 |

**Rule:** data pulled through one user's Kite session is shown only to that user, never cached for others.

## Tech stack, data model and cost controls

The stack splits a TypeScript web app from a Python agent and compute backend, joined by a job queue so agents run in parallel.

| Layer | Choice | Why |
| --- | --- | --- |
| Web app | Next.js (TypeScript), Tailwind, TradingView charting library | SEO for public stock and fund pages; mature charts |
| Auth | Firebase Auth | Existing team stack |
| API | FastAPI (Python) | Same language as the compute and agents |
| Orchestration | Claude Agent SDK or LangGraph for planner and judge | Typed tool calls, parallel fan-out |
| Job queue | Redis + Celery (or Temporal) | Parallel agent runs, retries, deadlines |
| Compute | pandas, NumPy, py\_vollib for Greeks, vectorbt-style backtests | Deterministic numbers |
| Storage | Postgres + TimescaleDB (prices, metrics), pgvector (filings, transcripts), object storage (PDFs) | One database family |
| Hosting | Vercel for web; container host for workers | Simple scaling |

**Core tables**

- `securities` (symbol, ISIN, sector, lot size), `prices_daily`, `fundamentals_quarterly`, `filings`, `fund_schemes`, `fund_nav`, `fund_holdings`.
- `agent_runs` (agent\_id, subject, as\_of, status, cost, duration) and `agent_outputs` (the standard envelope as JSON).
- `reports` (mother agent synthesis per subject and date) and `verification_log` (claims checked, dropped, why).
- `users`, `portfolios`, `trades`, `alerts`, `families`, `family_members`, `claims` (Phase 2 onward).

**Cost controls**

1. Nightly batch for about 2,000 actively traded stocks and all mutual fund schemes; pages read from `reports`.
2. Re-run an agent only when its inputs change (new filing, new price day, new NAV).
3. Small model for specialists, strong model only for planner and judge.
4. Per-user daily budget for on-demand agents, higher on Pro.
5. Track cost per report in `agent_runs` and alert when a desk exceeds its budget.

## Phase 1 build plan

Phase 1 ships the markets and mutual funds desks plus the mother agent as public stock and fund pages, in about 10 weeks with no live-data licence. Later phases: Phase 2 F&O desk and edge test, Phase 3 You desk, Phase 4 Family desk.

**Scope**

- Agents: M1–M7, MF1–MF3, mother planner and judge, verifier, bull/bear debate, compliance guard.
- Coverage: about 2,000 actively traded NSE stocks and all open-ended mutual fund schemes.

**Pages**

- Home with market overview and search.
- Stock page: chart, summary report, fundamentals, red flags, filings, peers, valuation bands, bull vs bear.
- Fund page: returns vs benchmark, X-ray, manager record, closet-indexer check.
- Ask box: free-text research questions answered by the mother agent with sources.
- Fund overlap tool: paste or upload funds, get overlap and cost gap.
- Account, plans and billing.

**Milestones (weeks from start)**

1. Weeks 1–2: data pipelines for EOD prices, filings, AMFI NAV and portfolios; core tables; nightly scheduler.
2. Weeks 3–4: compute layer for M1, M2, M6, M7 and MF1, MF2 with unit tests on known values.
3. Weeks 5–6: M3, M4, M5, MF3; standard output envelope; agent\_runs logging.
4. Weeks 7–8: mother planner and judge, verifier, debate, compliance guard; reports table.
5. Weeks 9–10: Next.js pages, charts, ask box, SEO, beta with 100 users.

**Acceptance criteria**

- [ ] Every metric on a page links to its source and formula.
- [ ] Verifier re-checks headline numbers; mismatch rate under 1% on a 200-stock audit sample.
- [ ] Compliance guard catches 100% of buy/sell phrasing in a test set of 300 prompts.
- [ ] Stock and fund pages load in under 1.5 s from cache.
- [ ] Ask box answers in under 30 s for single-stock questions.
- [ ] Nightly batch for all covered stocks finishes before 7 AM IST.

## Compliance, risks and open questions

The two biggest risks are giving advice without SEBI registration and showing exchange data without a licence; both are designed out of Phase 1.

| Risk | Mitigation |
| --- | --- |
| Output reads as investment advice (needs SEBI Research Analyst registration) | Compliance guard, descriptive-only prompts, legal review before launch |
| Exchange data shown without licence | EOD and public data in Phase 1; live data only via the user's own broker session |
| Wrong numbers from AI | Code computes all metrics; verifier; source on every claim; audit sample each release |
| Personal data breach (holdings, PAN, family documents) | Encryption at rest, DPDP Act compliance, consent per member, deletion on request |
| Agent cost overruns | Batch-first design, per-user budgets, cost tracking per run |
| Zerodha platform access delayed | Launch Phase 2 with tradebook upload first; add live chain after approval |

**Open questions**

- [ ] Final product name (EdgeOS is a working name).
- [ ] Fundamentals source: parse XBRL in-house or buy from a vendor?
- [ ] Which news provider, and its display terms?
- [ ] Does Family CFO merge into this platform or stay a separate app sharing a backend?
- [ ] Legal opinion on the exact wording allowed in bull vs bear output.
