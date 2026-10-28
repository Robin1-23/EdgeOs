# EdgeOS — project guide for Claude Code

EdgeOS is a one-stop Indian stock market and family-finance website. Specialist AI agents (one job each) run in parallel under a mother agent that plans, verifies, debates and synthesizes. Full product spec: `docs/SPEC.md` — read it before starting any feature.

## Current phase: Phase 1

Build only these:
- Markets desk agents M1–M7 (price/technicals, fundamentals, forensic red flags, filings/concalls, news/sentiment, peers/sector, valuation)
- Mutual funds desk agents MF1–MF3 (fund X-ray, manager/consistency, closet indexer detector)
- Mother agent: planner, judge, verifier, bull/bear debate, compliance guard
- Pages: home + search, stock page, fund page, ask box, fund overlap tool, account/billing
- Data: EOD prices (NSE/BSE bhavcopy), exchange filings, AMFI NAV + monthly fund portfolios, news

Do NOT build F&O, You desk, or Family desk yet (Phases 2–4). No live market data in Phase 1.

## Non-negotiable rules

1. **Code computes, AI explains.** Every number (ratios, returns, indicators, overlap) is computed in Python/SQL with unit tests. LLM calls only choose what to compute and write explanations. Never let an LLM produce a number that is shown to users.
2. **One agent, one job.** Each agent lives in its own module, has a typed input model and returns the standard output envelope. Agents never call each other directly; only the mother agent orchestrates.
3. **Standard output envelope** (Pydantic model, shared by all agents): `agent_id, subject, as_of, metrics (each with formula_id + source_query_id), findings (linked to metric ids), flags (severity info|watch|alert + reason), sources, confidence (high|medium|low + reason), gaps`.
4. **Every claim has a source.** Findings without a linked metric and source are dropped by the verifier.
5. **Describe, never advise.** No buy/sell/hold, target prices or return promises anywhere in output. The compliance guard runs last on every report; keep its test set passing.
6. **Missing data → say so.** Agents report gaps; they never guess or fill in values.
7. **Batch first.** Markets and MF agents run nightly and write to `reports`; pages read from cache. On-demand runs only for the ask box.
8. **Data only through source adapters.** Pipelines get rows from `services/pipelines/sources.py` (`dev_local` = files already downloaded, dev only; `licensed_vendor` = stub for the licensed feed). No pipeline downloads from exchange or AMFI websites (their terms forbid automated collection and public display). Every row carries `source`; pages may show only `agent_outputs.public_ok` results.

## Stack

- `apps/web` — Next.js (TypeScript), Tailwind, TradingView charting library, Firebase Auth
- `services/api` — FastAPI (Python 3.12)
- `services/agents` — mother agent + desks (Claude Agent SDK or LangGraph), Redis + Celery queue
- `services/pipelines` — data ingestion jobs (bhavcopy, filings, AMFI)
- Postgres + TimescaleDB, pgvector for filings/transcripts, object storage for PDFs

## Repo layout

```
apps/web/
services/api/
services/agents/
  mother/        planner.py, judge.py, verifier.py, debate.py, compliance.py
  desks/markets/ m1_technicals.py … m7_valuation.py
  desks/funds/   mf1_xray.py, mf2_manager.py, mf3_closet_index.py
  envelope.py    shared output model
services/pipelines/
packages/shared/ shared types
docs/SPEC.md
```

## Working conventions

- Small, reviewable steps: one agent or one pipeline per task, with tests, then commit.
- Every compute function gets a unit test against a hand-checked known value.
- Log each agent run to `agent_runs` (agent_id, subject, as_of, status, cost, duration).
- Secrets in `.env` (never committed). Market data is shown only under its licence terms.
- Default model: small fast model for specialist interpretation, strong model for planner/judge.
