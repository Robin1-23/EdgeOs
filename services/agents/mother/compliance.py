"""Compliance guard: runs last on every report (CLAUDE.md rule 5). Describe, never advise.

Removes whole sentences that recommend buying/selling/holding, give target prices or valuation calls, promise
returns, carry ratings, give personal advice, or tell fund investors to switch/exit; then appends the standard
disclaimer. Two layers:
  1. rules (deterministic regexes below): the 300-case test set must be caught by these alone;
  2. an optional LLM pass (specialist tier) for paraphrases the rules miss. It can only remove sentences; if it
     fails, the text is blocked rather than published.
Restating historical facts ("promoters bought shares", "the fund returned 12% over 5 years", "exit load of 1%",
"management targets 20% growth") is allowed.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from services.agents.llm.client import LLMError, Provider, call_json

DISCLAIMER = ("This is an automated, descriptive analysis based on the sources cited. It is not investment advice, "
              "a research report or a recommendation to transact in any security or fund. EdgeOS is not registered "
              "with SEBI as an investment adviser or research analyst.")

_P = r"(?:₹|rs\.?|inr)?\s*\d"  # a price or amount
# a rupee price, not an amount in crore/lakh/billion (keeps management guidance like "Rs 500 crore" out)
_PRICE = r"(?:₹|rs\.?|inr)\s*\d[\d,]*(?:\.\d+)?(?![\d,.]*\s*(?:crore|cr\b|lakh|lac\b|bn\b|billion|million|mn\b|thousand))"
_SUBJ = r"(?:stock|shares?|scrip|counter|price|it|nifty|sensex|index|fund|nav|scheme)"
_ACT = r"(?:buy|sell|hold|exit|accumulate|book|avoid|switch|redeem|add|reduce|trim|stay|invest|enter|wait for|look to|go|consider|lighten|average)"

# (rule id, category, pattern). Case-insensitive.
RULES: list[tuple[str, str, str]] = [
    # recommendation
    ("rec_rating_word", "recommendation", r"\b(?:strong\s+)?(?:buy|sell|hold|accumulate|reduce|add)\s+(?:rating|call|recommendation|signal|tag)s?\b"),
    ("rec_rated", "recommendation", r"\b(?:rated?|rating\s+of|rating\s+it|rates?\s+it)\s+(?:as\s+)?(?:an?\s+)?(?:strong\s+)?(?:buy|sell|hold|accumulate|reduce)\b"),
    ("rec_is_a_buy", "recommendation", r"\b(?:is|looks\s+like|remains|as|still|becomes|turns)\s+an?\s+(?:strong\s+|good\s+|clear\s+|solid\s+)?(?:buy|sell|hold)\b"),
    ("rec_we_recommend", "recommendation", r"\b(?:we|i)\s+(?:would\s+|strongly\s+)?(?:recommend|suggest|advise|advocate|prefer|like)\b"),
    ("rec_recommend_action", "recommendation", r"\brecommend(?:s|ed|ing)?\s+(?:buying|selling|holding|accumulating|investing|exiting|switching|adding|booking|avoiding|reducing)\b"),
    ("rec_imperative", "recommendation", r"^[\W_]*(?:buy|sell|accumulate|grab|avoid)\b(?![\s-]*(?:off|back|side|ers?\b))|^[\W_]*short\s+(?:the|this|it|these|shares|stocks?|nifty|on|at|below|above)\b"),
    ("rec_imperative_hold", "recommendation", r"^[\W_]*hold\s+(?:on|the|this|it|your|for|till|until|tight)\b"),
    ("rec_imperative_exit", "recommendation", r"^[\W_]*exit\s+(?:the|this|your|now|at|before|on)\b"),
    ("rec_time_to", "recommendation", r"\b(?:time|moment|opportunity|chance|occasion)\s+to\s+(?:buy|sell|exit|enter|accumulate|invest|book\s+profits?|get\s+in|get\s+out|load\s+up|switch|add|cash\s+out|lock\s+in\s+gains)\b"),
    ("rec_entry_point", "recommendation", r"\b(?:attractive|good|great|ideal|perfect|decent)\s+(?:entry|exit|buying|selling)\s+(?:point|level|price|opportunity|zone)s?\b"),
    ("rec_on_dips", "recommendation", r"\b(?:buy|add|accumulate)\s+(?:on|in|during)\s+(?:the\s+)?(?:dips?|declines?|corrections?)\b|\bsell\s+(?:on|into)\s+(?:the\s+)?(?:rise|rallies|rally|strength)\b"),
    ("rec_book_profits", "recommendation", r"\bbook(?:ing)?\s+(?:partial\s+|some\s+)?profits?\b"),
    ("rec_stop_loss", "recommendation", r"\b(?:keep|place|put|set|maintain|use|trail|with)\s+(?:a\s+|the\s+|your\s+)?(?:strict\s+|tight\s+|trailing\s+)?stop[\s-]?loss\b|\bstop[\s-]?loss\s+(?:at|below|above|of|near|around)\b"),
    ("rec_should", "recommendation", rf"\b(?:investors|traders|shareholders|holders|unitholders|you|one|readers|buyers)\b[^.]{{0,40}}?\b(?:should|must|may\s+want\s+to|might\s+want\s+to|ought\s+to|could\s+consider|can\s+consider|need\s+to|would\s+do\s+well\s+to|are\s+advised\s+to|had\s+better)\s+(?:not\s+)?{_ACT}\b"),
    ("rec_consider_ing", "recommendation", r"\bconsider\s+(?:buying|selling|exiting|adding|accumulating|switching|redeeming|investing|booking|trimming|reducing|holding|averaging)\b"),
    ("rec_stay_invested", "recommendation", r"\b(?:stay|remain|keep)\s+invested\b"),
    ("rec_at_price", "recommendation", rf"\b(?:buy|sell|accumulate|add|exit)\s+(?:at|around|near|below|above|between|under|over)\s+{_P}"),
    ("rec_go_long", "recommendation", r"\bgo(?:ing)?\s+(?:long|short)\b"),
    ("rec_avoid_stock", "recommendation", r"\bavoid\s+(?:the|this|these|such)\s+(?:stock|share|fund|scheme|counter|company|name)s?\b"),
    ("rec_dont", "recommendation", r"\b(?:do\s+not|don'?t|never)\s+(?:buy|sell|miss|chase|panic[\s-]sell)\b"),
    ("rec_must_buy", "recommendation", r"\bmust[\s-](?:buy|own|have|sell)\b|\btop\s+picks?\b|\bpick\s+of\s+the\s+(?:week|month|year)\b"),
    ("rec_to_buy", "recommendation", r"\b(?:stocks?|shares?|funds?|schemes?)\s+to\s+(?:buy|sell|own|hold|avoid|accumulate)\b"),
    ("rec_load_up", "recommendation", r"\bload\s+up\b|\bbuy\s+more\b|\bsell\s+(?:half|some|all|everything|your)\b|\bbuy\s+(?:now|today|immediately|aggressively|more)\b"),
    ("rec_averaging", "recommendation", r"\baverage\s+(?:down|out|your\s+(?:position|cost))\b"),
    # target price / valuation calls / price forecasts
    ("tp_target_price", "target_price", r"\b(?:target|tgt)\s*(?:prices?|levels?)\b|\bprice\s+targets?\b|\b\d+[\s-]month\s+target\b|\btarget\s+of\s+(?:₹|rs\.?|inr)"),
    ("tp_tp", "target_price", r"\btp\s*(?:of|at|:|-)?\s*(?:₹|rs\.?|inr)?\s*\d"),
    ("tp_fair_value", "target_price", rf"\b(?:fair|intrinsic|true|fundamental)\s+value\b(?!\s+(?:through|changes?|gains?|loss(?:es)?|hierarchy|measurement|of\s+(?:investments|financial|derivatives?|plan\s+assets|assets|liabilities)))[^.]{{0,40}}?{_PRICE}|\b(?:fair|intrinsic)\s+value\s+per\s+share\b"),
    ("tp_mis_valued", "target_price", r"\b(?:under|over)[\s-]?valued\b|\bmis[\s-]?priced\b"),
    ("tp_cheap", "target_price", rf"\b{_SUBJ}\s+(?:is|are|look|looks|appear|appears|seem|seems|remain|remains|trades\s+as)\s+(?:very\s+|quite\s+|still\s+|too\s+)?(?:cheap|expensive|attractive|a\s+bargain|a\s+steal|pricey|rich|overheated|oversold|overbought)\b"),
    ("tp_forecast", "target_price", rf"\b{_SUBJ}\b[^.]{{0,40}}?\b(?:could|can|will|may|might|should|is\s+likely\s+to|are\s+likely\s+to|is\s+expected\s+to|is\s+set\s+to|is\s+poised\s+to|is\s+headed\s+to|is\s+bound\s+to)\s+(?:reach|touch|hit|rise\s+to|climb\s+to|rally\s+to|fall\s+to|drop\s+to|slip\s+to|test|go\s+to|cross|head\s+(?:to|towards)|scale|breach)\b"),
    ("tp_price_forecast", "target_price", rf"\b(?:is|are)\s+(?:headed|heading|set|poised|likely|expected|bound)\s+(?:to|for|towards)\s+{_PRICE}|\b(?:will|could|can|may|might|should)\s+(?:reach|touch|hit|rise\s+to|climb\s+to|rally\s+to|fall\s+to|drop\s+to|slip\s+to|test|go\s+to|cross|scale|breach)\s+{_PRICE}"),
    ("tp_upside", "target_price", r"\b(?:upside|downside)\s+(?:potential|of|to|target|risk\s+of|is\s+limited|is\s+capped|from\s+here)\b|\b\d+(?:\.\d+)?\s*%\s+(?:upside|downside)\b"),
    # return promises / forecasts of returns
    ("ret_guaranteed", "return_promise", r"\b(?:guaranteed|assured|sure[\s-]?shot|risk[\s-]free|certain|promised|safe)\s+(?:returns?|profits?|gains?|income|money|wealth\s+creation)\b"),
    ("ret_risk_free", "return_promise", r"\brisk[\s-]free\b(?!\s+rate)"),
    ("ret_will_deliver", "return_promise", r"\b(?:will|would|shall|is\s+going\s+to|are\s+going\s+to|set\s+to|bound\s+to|likely\s+to|expected\s+to|should)\s+(?:give|deliver|generate|earn|return|yield|fetch|make|offer)\b[^.]{0,30}?\d+(?:\.\d+)?\s*(?:%|per\s*cent|percent|x\b|times)"),
    ("ret_double", "return_promise", r"\b(?:double|triple|treble|multiply)\s+(?:your|the|their|investors'?)\s+(?:money|investment|wealth|capital|corpus)\b|\b(?:stock|shares?|money|investment|fund|nav|price|it)\b[^.]{0,30}?\b(?:will|could|can|may|is\s+set\s+to|is\s+likely\s+to)\s+(?:double|triple|treble|multiply)\b"),
    ("ret_cannot_lose", "return_promise", r"\b(?:can(?:no|')t|cannot|will\s+not|won'?t)\s+(?:lose|go\s+wrong|fail|disappoint)\b|\bno[\s-]lose\b|\bsafe\s+bet\b|\bzero\s+risk\b|\bwithout\s+(?:any\s+)?risk\b|\bno\s+downside\b"),
    ("ret_expected", "return_promise", r"\bexpected\s+(?:annual\s+|yearly\s+)?returns?\s+(?:of|is|are|around|near|at)\s+\d|\breturns?\s+of\s+\d+(?:\.\d+)?\s*%\s+(?:is|are)\s+(?:likely|expected|assured|guaranteed|possible|achievable)\b"),
    ("ret_you_can_expect", "return_promise", r"\b(?:you|investors|one)\s+(?:can|could|will|should|may)\s+(?:expect|earn|make|get|see|pocket)\s+(?:returns?\s+of\s+|gains?\s+of\s+|about\s+|around\s+)?\d+(?:\.\d+)?\s*(?:%|x\b|times)"),
    ("ret_multibagger", "return_promise", r"\bmulti[\s-]?baggers?\b|\b\d+[\s-]?baggers?\b|\bwealth\s+creator\s+for\s+you\b"),
    ("ret_will_outperform", "return_promise", r"\b(?:will|is\s+set\s+to|is\s+likely\s+to|should|is\s+expected\s+to|is\s+bound\s+to)\s+(?:outperform|beat|underperform|lag)\b"),
    # ratings / stances
    ("rate_stance_word", "rating", r"\b(?:overweight|underweight|outperform|underperform|neutral|equal[\s-]?weight|market[\s-]?perform|sector[\s-]?perform|positive|negative)\s+(?:rating|call|stance|recommendation|view\s+on\s+the\s+stock)\b"),
    ("rate_we_are", "rating", r"\b(?:we|i)\s+(?:are|am|remain|stay|turn|turned|became|become|have\s+(?:turned|become)|are\s+turning|am\s+turning)\s+(?:overweight|underweight|bullish|bearish|positive|negative|constructive|cautious)\b"),
    ("rate_up_downgrade", "rating", r"\b(?:upgrade|downgrade|cut|raise|lower|move|revise)(?:s|d|ed)?\b[^.]{0,30}?\bto\s+(?:a\s+)?(?:strong\s+)?(?:buy|sell|hold|outperform|underperform|overweight|underweight|neutral|accumulate|reduce|add)\b"),
    ("rate_bullish_on", "rating", r"\b(?:bullish|bearish|positive|negative|optimistic|pessimistic)\s+(?:on|about)\s+(?:the\s+)?(?:stock|shares|scrip|counter|company's\s+shares|fund|scheme|it)\b"),
    ("rate_conviction", "rating", r"\b(?:high|top|strong)[\s-]conviction\b"),
    # personal advice
    ("adv_allocate", "personal_advice", r"\ballocat\w*\s+(?:\d+(?:\.\d+)?\s*%|a\s+(?:small|large|part|portion))[^.]{0,20}?\bof\s+(?:your|the|their)\s+(?:portfolio|corpus|savings|money|net\s+worth)\b"),
    ("adv_your_money", "personal_advice", r"\b(?:put|park|invest|move|shift|keep|add|deploy|channel)\s+(?:all\s+|some\s+|part\s+|a\s+portion\s+|a\s+part\s+)?(?:of\s+)?(?:your|their)\s+(?:money|savings|funds|capital|corpus|portfolio|bonus|surplus)\b"),
    ("adv_suitable", "personal_advice", r"\b(?:suitable|right|ideal|perfect|good|great|best|apt)\s+(?:[\w-]+\s+){0,2}?for\s+(?:you\b|your\b|investors\s+like\s+you|conservative\s+investors|aggressive\s+investors|retirees|beginners|first[\s-]time\s+investors|long[\s-]term\s+investors)"),
    ("adv_if_you", "personal_advice", r"\bif\s+you\s+(?:are|have|want|hold|own|plan|seek|need)\b[^.]*?\b(?:buy|sell|hold|exit|switch|invest|add|redeem|consider|accumulate|book)\b"),
    ("adv_add_to_portfolio", "personal_advice", r"\b(?:add|include|keep)\s+(?:it|this|the\s+stock|the\s+fund|this\s+fund|this\s+stock|these\s+shares|the\s+scheme)\s+(?:to|in)\s+(?:your|the|a)\s+(?:portfolio|watchlist|core\s+holdings)\b"),
    ("adv_invest_now", "personal_advice", r"\binvest\s+(?:now|today|immediately|regularly|via\s+sip|through\s+sip|in\s+(?:this|the)\s+(?:fund|stock|scheme|company))\b|\bstart\s+an?\s+sips?\b"),
    ("adv_portfolio_of", "personal_advice", r"\b(?:your|their)\s+(?:portfolio|holdings?)\s+(?:should|must|needs?\s+to|ought\s+to)\b"),
    # fund actions
    ("fund_switch", "fund_action", r"\bswitch(?:ing)?\s+(?:over\s+)?(?:to|into|from|out\s+of)\s+(?:[\w-]+\s+){0,3}?(?:plans?|funds?|schemes?|options?|etfs?)\b|^[\W_]*(?:switch(?:ing)?|shift|redeem)\b"),
    ("fund_exit", "fund_action", r"\b(?:exit|redeem|get\s+out\s+of|move\s+out\s+of|pull\s+out\s+of|cash\s+out\s+of|withdraw\s+from)\s+(?:the|this|these|your|such)?\s*(?:fund|scheme|plan|stock|position|sip|holding|unit)s?\b"),
    ("fund_sip", "fund_action", r"\b(?:stop|pause|continue|keep|increase|step[\s-]up|discontinue|top[\s-]up)\s+(?:your\s+|the\s+|their\s+)?sips?\b"),
    ("fund_move_money", "fund_action", r"\bmove\s+(?:your|the|their)\s+(?:money|investment|corpus|units)\b"),
]
_COMPILED = [(rid, cat, re.compile(p, re.IGNORECASE)) for rid, cat, p in RULES]
_ABBREV = re.compile(r"\b(?:rs|no|vs|e\.g|i\.e|approx|ltd|inc|co|mr|ms|dr|st)\.$", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    rule: str
    category: str
    sentence: str
    match: str


@dataclass
class GuardResult:
    text: str  # sanitized text + disclaimer ('' when blocked)
    status: Literal["clean", "redacted", "blocked"]
    removed: list[Violation] = field(default_factory=list)
    llm_checked: bool = False
    reason: str | None = None


def split_sentences(text: str) -> list[str]:
    """Sentences and list items, keeping 'Rs. 500', 'e.g.' and decimals intact."""
    out = []
    for block in re.split(r"\n+", text):
        start = 0
        for m in re.finditer(r"[.!?](?=\s+|$)", block):
            if _ABBREV.search(block[start:m.end()]):
                continue
            out.append(block[start:m.end()].strip())
            start = m.end()
        out.append(block[start:].strip())
    return [s for s in out if s]


def check(text: str) -> list[Violation]:
    """Rule layer: every sentence that matches any rule (first matching rule reported)."""
    out = []
    for sentence in split_sentences(text):
        plain = re.sub(r"[*_`#>]+", "", sentence)  # markdown emphasis must not hide a match
        for rid, cat, rx in _COMPILED:
            if m := rx.search(plain):
                out.append(Violation(rid, cat, sentence, m.group(0)))
                break
    return out


class LLMVerdict(BaseModel):
    """Indexes of sentences that recommend, forecast prices or returns, rate, or give personal advice."""
    flagged: list[int] = Field(description="0-based indexes of sentences to remove")
    reasons: list[str] = Field(description="one short reason per flagged index, same order")


LLM_SYSTEM = (
    "You are a compliance checker for an Indian investment-research website that is not SEBI-registered. "
    "Text must only describe facts. Flag a sentence if it, directly or by implication, recommends buying, selling, "
    "holding, switching or exiting; gives or implies a target price, fair value or price forecast; promises or "
    "forecasts returns; states a rating or a bullish/bearish stance; or gives advice personal to the reader. "
    "Do not flag factual restatements of past events, reported results, management guidance attributed to "
    "management, or terms like exit load or downside capture. Reply with the indexes of sentences to remove."
)


@dataclass
class ManyResult:
    texts: list[str | None]  # sanitized text per input (no disclaimer); None when nothing in it survived
    removed: list[Violation] = field(default_factory=list)
    llm_checked: bool = False
    blocked: str | None = None  # set when the LLM pass failed: nothing may be published


def guard_many(texts: list[str], use_llm: bool = True, provider: Provider | None = None) -> ManyResult:
    """Rules on each text, then one LLM pass over every remaining sentence of all texts (one call per report)."""
    removed: list[Violation] = []
    kept: list[list[str]] = []
    for text in texts:
        found = check(text)
        removed += found
        bad = {v.sentence for v in found}
        kept.append([s for s in split_sentences(text) if s not in bad])
    llm_checked = False
    flat = [(i, j) for i, sentences in enumerate(kept) for j in range(len(sentences))]
    if use_llm and flat:
        numbered = "\n".join(f"[{n}] {kept[i][j]}" for n, (i, j) in enumerate(flat))
        try:
            verdict = call_json("specialist", LLM_SYSTEM, numbered, LLMVerdict, provider=provider).value
        except LLMError as e:
            return ManyResult(texts=[None] * len(texts), removed=removed, blocked=f"LLM compliance check failed: {e}")
        reasons = dict(zip(verdict.flagged, verdict.reasons))
        flagged = {flat[n] for n in verdict.flagged if 0 <= n < len(flat)}
        removed += [Violation("llm", "llm", kept[i][j], reasons.get(flat.index((i, j)), "")) for i, j in sorted(flagged)]
        kept = [[s for j, s in enumerate(sentences) if (i, j) not in flagged] for i, sentences in enumerate(kept)]
        llm_checked = True
    return ManyResult(texts=[" ".join(s) if s else None for s in kept], removed=removed, llm_checked=llm_checked)


def guard(text: str, use_llm: bool = True, provider: Provider | None = None) -> GuardResult:
    """Rule pass, then (optionally) an LLM pass over the remaining sentences; returns sanitized text + disclaimer."""
    r = guard_many([text], use_llm, provider)
    if r.blocked:
        return GuardResult(text="", status="blocked", removed=r.removed, reason=r.blocked)
    if r.texts[0] is None:
        return GuardResult(text="", status="blocked", removed=r.removed, llm_checked=r.llm_checked,
                           reason="nothing left after removing non-compliant sentences")
    return GuardResult(text=f"{r.texts[0]}\n\n{DISCLAIMER}", status="redacted" if r.removed else "clean", removed=r.removed,
                       llm_checked=r.llm_checked)
