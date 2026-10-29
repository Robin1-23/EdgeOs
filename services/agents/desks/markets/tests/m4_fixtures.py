"""Synthetic filings for a fictional company (ACME) and hand-written model responses for M4 tests.

The responses are written by hand, not captured from a live model; write_recordings() saves them through the
normal record mechanism so tests exercise the same replay path as real recordings. Each response includes one
statement that must be dropped (a quote not in the filing, or a number not in its quote).
Regenerate after changing M4's prompt or schema:
    uv run python -m services.agents.desks.markets.tests.m4_fixtures
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ISIN = "INE000000011"
SYMBOL = "ACME"
RECORDINGS = Path(__file__).parent / "recordings" / "m4"

RESULTS_TEXT = (
    "Acme Widgets Limited - Outcome of Board Meeting. Revenue from operations for the quarter ended 30 September "
    "2026 was Rs 206 crore, compared with Rs 176 crore in the same quarter last year. Profit after tax was Rs 22 "
    "crore. The Board declared an interim dividend of Rs 2 per share. Commenting on the results, the Managing "
    "Director said: “We are pleased with the steady demand and expect margins to improve in the second half "
    "of the year.” The company plans capital expenditure of Rs 40 crore in FY27."
)
CONCALL_TEXT = (
    "Transcript of the Q1 FY27 earnings conference call held on 4 August 2026. CEO: Demand was soft this quarter "
    "and we remain watchful on raw material costs. We do not expect a quick recovery in exports. CFO: Working "
    "capital days were 74 at the end of June."
)
AUDITOR_TEXT = (
    "Intimation of resignation of Statutory Auditor. M/s ABC & Co, Chartered Accountants, have resigned as "
    "statutory auditors of the Company with effect from 11 September 2026, citing pre-occupation with other "
    "assignments."
)

# (isin, exchange, filed_at, category, title, url, text): the shape services.pipelines.filings.build takes
FILINGS = [
    (ISIN, "NSE", datetime(2026, 10, 20, 17, 5, tzinfo=IST), "results", "Financial results for Q2 FY27",
     "https://example.invalid/acme/q2-fy27-results.pdf", RESULTS_TEXT),
    (ISIN, "NSE", datetime(2026, 10, 1, 9, 30, tzinfo=IST), "announcement", "Newspaper advertisement",
     "https://example.invalid/acme/newspaper-ad.pdf", None),
    (ISIN, "NSE", datetime(2026, 9, 12, 20, 0, tzinfo=IST), "announcement", "Resignation of statutory auditor",
     "https://example.invalid/acme/auditor-resignation.pdf", AUDITOR_TEXT),
    (ISIN, "NSE", datetime(2026, 8, 5, 18, 0, tzinfo=IST), "concall", "Q1 FY27 earnings call transcript",
     "https://example.invalid/acme/q1-fy27-concall.pdf", CONCALL_TEXT),
]

RESPONSES = {
    "Financial results for Q2 FY27": {
        "event_tag": "results",
        "summary": [
            {"text": "Revenue from operations was Rs 206 crore in the quarter ended 30 September 2026, against Rs 176 crore a year earlier.",
             "quote": "Revenue from operations for the quarter ended 30 September 2026 was Rs 206 crore, compared with Rs 176 crore"},
            {"text": "Profit after tax was Rs 22 crore.", "quote": "Profit after tax was Rs 22 crore."},
            {"text": "The board declared an interim dividend of Rs 2 per share.", "quote": "declared an interim dividend of Rs 2 per share"},
            # must be dropped: 25% is not in the quote
            {"text": "Profit after tax rose 25% from a year earlier.", "quote": "Profit after tax was Rs 22 crore."},
        ],
        "guidance": [
            {"text": "The managing director said the company expects margins to improve in the second half of the year.",
             "quote": "expect margins to improve in the second half of the year"},
            {"text": "The company plans capital expenditure of Rs 40 crore in FY27.",
             "quote": "The company plans capital expenditure of Rs 40 crore in FY27."},
        ],
        "tone": "positive",
        "tone_evidence": ["We are pleased with the steady demand"],
    },
    "Resignation of statutory auditor": {
        "event_tag": "auditor_change",
        "summary": [{"text": "The statutory auditors, ABC & Co, resigned with effect from 11 September 2026, citing other assignments.",
                     "quote": "have resigned as statutory auditors of the Company with effect from 11 September 2026"}],
        "guidance": [],
        "tone": "not_applicable",
        "tone_evidence": [],
    },
    "Q1 FY27 earnings call transcript": {
        "event_tag": "results",
        "summary": [
            {"text": "The CEO said demand was soft in the quarter and the company is watchful on raw material costs.",
             "quote": "Demand was soft this quarter and we remain watchful on raw material costs"},
            # must be dropped: this quote is not in the transcript
            {"text": "The CEO said sales reached a record.", "quote": "We expect record sales this year"},
        ],
        "guidance": [{"text": "The CEO said the company does not expect a quick recovery in exports.",
                      "quote": "We do not expect a quick recovery in exports"}],
        "tone": "negative",
        "tone_evidence": ["Demand was soft this quarter"],
    },
}


def filing_dicts() -> list[dict]:
    """FILINGS as M4's compute() input (ids 1..n, newest first), for pure tests."""
    from services.pipelines.filings import sha256

    return [{"id": i, "filed_at": f[2], "category": f[3], "title": f[4], "url": f[5], "text": f[6], "sha": sha256(f[6])}
            for i, f in enumerate(FILINGS, 1)]


class HandWritten:
    """Live-provider stand-in that answers from RESPONSES by filing title."""

    name = "hand_written"

    def complete_json(self, model, system, user, schema_name, schema, effort):
        from services.agents.llm.client import RawResponse

        title = next(line.split(": ", 1)[1] for line in user.splitlines() if line.startswith("Filing title: "))
        return RawResponse(text=json.dumps(RESPONSES[title]), model=model, input_tokens=len(user) // 4,
                           output_tokens=180, cached_tokens=0, reasoning_tokens=0)


def write_recordings() -> None:
    from services.agents.desks.markets import m4_filings as m4
    from services.agents.llm.client import RecordedProvider, call_json

    for old in RECORDINGS.glob("*.json"):
        old.unlink()
    recorder = RecordedProvider(RECORDINGS, live=HandWritten())
    for f in filing_dicts():
        if f["text"]:
            call_json("specialist", m4.SYSTEM, m4.prompt(SYMBOL, f), m4.FilingSummary, provider=recorder)
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["origin"] = "hand-written test response (not captured from a live model)"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(list(RECORDINGS.glob('*.json')))} recordings to {RECORDINGS}")


if __name__ == "__main__":
    write_recordings()
