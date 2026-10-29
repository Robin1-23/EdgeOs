"""Synthetic headlines for a fictional company (ACME, "Acme Widgets Limited") and hand-written labels for M5 tests.

The labels are written by hand, not captured from a live model; write_recordings() saves them through the normal
record mechanism. Regenerate after changing M5's prompt or schema:
    uv run python -m services.agents.desks.markets.tests.m5_fixtures
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ISIN, SYMBOL, NAME = "INE000000011", "ACME", "Acme Widgets Limited"
RECORDINGS = Path(__file__).parent / "recordings" / "m5"


def t(month, day, hour=10):
    return datetime(2026, month, day, hour, 0, tzinfo=IST)


# (published_at, publisher, headline, url, expected label or None when the headline must not match)
NEWS = [
    (t(10, 27), "Daily Ledger", "Acme Widgets Q2 profit rises to Rs 22 crore", "https://example.invalid/n/1", ("positive", "results")),
    (t(10, 26), "Market Wire", "ACME shares slip 3% as metal costs rise", "https://example.invalid/n/2", ("negative", "market_move")),
    (t(10, 25), "Daily Ledger", "Acme Widgets wins Rs 300 crore defence order", "https://example.invalid/n/3", ("positive", "order_win")),
    (t(10, 24), "Business Post", "Auditor quits Acme Widgets citing other assignments", "https://example.invalid/n/4", ("negative", "governance")),
    (t(10, 22), "Market Wire", "Acme Widgets names new CFO", "https://example.invalid/n/5", ("neutral", "management_change")),
    (t(10, 21), "Street Notes", "Brokerage upgrades Acme Widgets to buy", "https://example.invalid/n/6", ("positive", "rating_or_broker_action")),
    (t(10, 15), "Business Post", "Acme Widgets to expand Pune plant", "https://example.invalid/n/7", ("positive", "product_or_expansion")),
    (t(10, 2), "Daily Ledger", "Acme Widgets faces GST notice", "https://example.invalid/n/8", ("negative", "regulatory_or_legal")),
    (t(9, 10), "Market Wire", "Acme Widgets AGM approves dividend", "https://example.invalid/n/9", ("neutral", "other")),
    (t(9, 1), "Daily Ledger", "Acme Widgets shares hit 52-week high", "https://example.invalid/n/10", ("positive", "market_move")),
    # must not match ACME
    (t(10, 26), "Global Wire", "Acme Corp of US reports earnings", "https://example.invalid/n/11", None),
    (t(10, 25), "Market Wire", "Widgets demand rises nationwide", "https://example.invalid/n/12", None),
    (t(10, 24), "Street Notes", "acme rally lifts small caps", "https://example.invalid/n/13", None),
    (t(8, 15), "Daily Ledger", "Acme Widgets marks listing anniversary", "https://example.invalid/n/14", None),  # outside 60 days
]
RECORDS = [n[:4] for n in NEWS]
LABELS = {n[2]: n[4] for n in NEWS if n[4]}


class HandWritten:
    """Answers M5's labelling prompt from LABELS by headline text, plus one out-of-range index that must be ignored."""

    name = "hand_written"

    def complete_json(self, model, system, user, schema_name, schema, effort):
        from services.agents.llm.client import RawResponse

        labels = []
        for line in user.splitlines():
            if line.startswith("[") and "] " in line:
                idx, headline = line[1:].split("] ", 1)
                sentiment, tag = LABELS[headline]
                labels.append({"index": int(idx), "sentiment": sentiment, "event_tag": tag})
        labels.append({"index": 99, "sentiment": "negative", "event_tag": "other"})
        return RawResponse(text=json.dumps({"labels": labels}), model=model, input_tokens=len(user) // 4, output_tokens=220)


def write_recordings() -> None:
    """Runs M5's own matching and batching over the fixtures so the recorded prompt is exactly what M5 sends."""
    from services.agents.desks.markets import m5_news as m5
    from services.agents.llm.client import RecordedProvider, call_json

    for old in RECORDINGS.glob("*.json"):
        old.unlink()
    names, symbols = m5.aliases_for(NAME, SYMBOL, [])
    end = datetime(2026, 10, 28, 0, 0, tzinfo=IST)
    recent = sorted((n for n in NEWS if m5.mentions(n[2], names, symbols) and end - m5.timedelta(days=m5.WINDOW_DAYS) <= n[0] < end),
                    key=lambda n: n[0], reverse=True)[:m5.MAX_LABEL_BATCH]
    recorder = RecordedProvider(RECORDINGS, live=HandWritten())
    call_json("specialist", m5.SYSTEM, m5.prompt(names[0], [(i, n[2]) for i, n in enumerate(recent)]), m5.NewsLabels, provider=recorder)
    for path in RECORDINGS.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["origin"] = "hand-written test response (not captured from a live model)"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(list(RECORDINGS.glob('*.json')))} recording(s) to {RECORDINGS}")


if __name__ == "__main__":
    write_recordings()
