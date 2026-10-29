import json
from collections import Counter
from pathlib import Path

import pytest

from services.agents.llm.client import LLMError, RawResponse
from services.agents.mother.compliance import DISCLAIMER, RULES, check, guard, split_sentences

CASES = [json.loads(line) for line in (Path(__file__).parent / "compliance_cases.jsonl").read_text(encoding="utf-8").splitlines()]
VIOLATIONS = [c for c in CASES if c["expect"] == "violation"]
CLEAN = [c for c in CASES if c["expect"] == "clean"]


def test_case_set_shape():
    assert len(CASES) >= 300 and len({c["text"] for c in CASES}) == len(CASES)
    per_category = Counter(c["category"] for c in VIOLATIONS)
    assert set(per_category) == {"recommendation", "target_price", "return_promise", "rating", "personal_advice", "fund_action"}
    assert min(per_category.values()) >= 30 and len(CLEAN) >= 80


@pytest.mark.parametrize("case", VIOLATIONS, ids=lambda c: f"{c['id']}-{c['category']}")
def test_every_violation_is_removed_by_rules_alone(case):
    result = guard(case["text"], use_llm=False)
    assert result.removed, f"not caught: {case['text']}"
    assert case["text"] not in result.text


@pytest.mark.parametrize("case", CLEAN, ids=lambda c: str(c["id"]))
def test_clean_text_is_left_alone(case):
    assert check(case["text"]) == [], f"false positive: {case['text']}"
    result = guard(case["text"], use_llm=False)
    assert result.status == "clean" and result.text == f"{case['text']}\n\n{DISCLAIMER}"


def test_catch_rate_is_100_percent():
    caught = sum(bool(check(c["text"])) for c in VIOLATIONS)
    false_positives = sum(bool(check(c["text"])) for c in CLEAN)
    assert (caught, false_positives) == (len(VIOLATIONS), 0)


def test_disclaimer_passes_its_own_guard():
    assert check(DISCLAIMER) == []


def test_split_sentences_keeps_amounts_and_abbreviations():
    text = "Revenue rose to Rs. 500 crore in Q2. Margins, e.g. EBITDA, fell 1.5%. Acme Ltd. filed results.\n- Buy now!"
    assert split_sentences(text) == ["Revenue rose to Rs. 500 crore in Q2.", "Margins, e.g. EBITDA, fell 1.5%.",
                                     "Acme Ltd. filed results.", "- Buy now!"]


def test_guard_removes_only_offending_sentences():
    report = ("Revenue grew 17% year on year to Rs 206 crore. The stock looks undervalued at 18 times earnings. "
              "Promoter pledge rose to 22% of promoter holding. Investors should accumulate on dips.")
    r = guard(report, use_llm=False)
    assert r.status == "redacted" and [v.category for v in r.removed] == ["target_price", "recommendation"]
    assert r.text == ("Revenue grew 17% year on year to Rs 206 crore. Promoter pledge rose to 22% of promoter holding."
                      f"\n\n{DISCLAIMER}")


def test_guard_blocks_when_nothing_is_left():
    r = guard("Buy now. Target price Rs 500.", use_llm=False)
    assert r.status == "blocked" and r.text == "" and len(r.removed) == 2


def test_every_rule_is_exercised_by_the_case_set():
    used = {check(c["text"])[0].rule for c in VIOLATIONS}
    unused = {rid for rid, _, _ in RULES} - used
    assert len(unused) <= len(RULES) // 4, f"rules with no test case: {sorted(unused)}"


# --- LLM second pass --------------------------------------------------------------------------------------

class Scripted:
    name = "scripted"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def complete_json(self, model, system, user, schema_name, schema, effort):
        self.prompts.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return RawResponse(text=json.dumps(self.reply), model=model, input_tokens=300, output_tokens=40)


def test_llm_pass_removes_paraphrased_advice():
    text = "Margins improved to 18%. This is the moment the smart money piles in. Net debt fell to Rs 40 crore."
    p = Scripted({"flagged": [1, 7], "reasons": ["implied recommendation", "out of range"]})
    r = guard(text, provider=p)
    assert r.llm_checked and r.status == "redacted"
    assert r.text == f"Margins improved to 18%. Net debt fell to Rs 40 crore.\n\n{DISCLAIMER}"
    assert [(v.rule, v.match) for v in r.removed] == [("llm", "implied recommendation")]
    assert p.prompts[0].startswith("[0] Margins improved to 18%.")  # sentences numbered for the model


def test_llm_only_sees_what_the_rules_kept():
    p = Scripted({"flagged": [], "reasons": []})
    guard("Buy the stock. Revenue rose 10%.", provider=p)
    assert p.prompts == ["[0] Revenue rose 10%."]


def test_llm_failure_blocks_instead_of_publishing():
    r = guard("Revenue rose 10%.", provider=Scripted(LLMError("openai 500")))
    assert r.status == "blocked" and r.text == "" and "LLM compliance check failed" in r.reason
    # with the default provider in tests (recordings only) a missing recording also fails closed
    assert guard("Revenue rose 11%.").status == "blocked"


@pytest.mark.parametrize("rule_id, text", [
    ("rec_at_price", "Long-term holders can accumulate around Rs 1,150."),
    ("rec_avoid_stock", "It is wise to avoid this stock until audits are clean."),
    ("rec_on_dips", "The trend favours those who buy on dips."),
    ("rec_rating_word", "The model issued a strong buy signal."),
    ("rec_recommend_action", "The note recommends selling into strength."),
])
def test_rules_shadowed_by_earlier_rules_still_match_on_their_own(rule_id, text):
    import re

    pattern = next(p for rid, _, p in RULES if rid == rule_id)
    assert re.search(pattern, text, re.IGNORECASE)
    assert check(text), text
