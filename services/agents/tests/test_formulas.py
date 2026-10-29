from services.agents import formulas, registry


def test_describe_strips_parameters():
    assert formulas.describe("M2", "item:revenue_ops") == formulas.FORMULAS[("M2", "item")]
    assert formulas.describe("M4", "llm_tone:m4-v1") == formulas.FORMULAS[("M4", "llm_tone")]
    assert formulas.describe("M2", "nonexistent") is None
    assert formulas.describe("MF2", "cagr_3") != formulas.describe("M2", "cagr_3")  # same id, different agent


def test_catalogue_covers_only_real_agents():
    assert {a for a, _ in formulas.FORMULAS} == set(registry.AGENTS)
    assert all(text.endswith(".") and len(text) > 20 for text in formulas.FORMULAS.values())
