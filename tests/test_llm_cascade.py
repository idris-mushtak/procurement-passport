"""Unit tests for the softmax, thresholds and cascade. No network, ever.

The cascade is where money and correctness meet: too eager to escalate and the
run costs 10x, too reluctant and it guesses a pass on an ambiguous clause.
These tests pin both edges.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prep.llm import config as C          # noqa: E402
from prep.llm import core                 # noqa: E402
from prep.llm.core import parse_json, softmax  # noqa: E402


# ------------------------------------------------------------------ softmax
def test_softmax_sums_to_one():
    assert sum(softmax([50, 30, 20])) == pytest.approx(1.0)


def test_softmax_is_monotonic():
    p_yes, p_no, p_unclear = softmax([90, 10, 5])
    assert p_yes > p_no > p_unclear


def test_softmax_temperature_sharpens():
    sharp = softmax([80, 20, 10], temp=5)
    flat = softmax([80, 20, 10], temp=100)
    assert sharp[0] > flat[0]


def test_softmax_handles_all_equal():
    probs = softmax([40, 40, 40])
    assert probs == pytest.approx([1 / 3, 1 / 3, 1 / 3])


# ------------------------------------------------------------- json recovery
@pytest.mark.parametrize("raw,expected", [
    ('{"p": 80}', {"p": 80}),
    ('```json\n{"p": 80}\n```', {"p": 80}),
    ('Sure! {"p": 80}', {"p": 80}),
    ("not json at all", None),
])
def test_parse_json_recovers(raw, expected):
    assert parse_json(raw) == expected


# -------------------------------------------------------------- the cascade
def _with_scores(yes: float, no: float, unclear: float):
    """Patch the micro-scorer so the cascade sees fixed scores."""
    scores = {"yes": yes, "no": no, "unclear": unclear}
    order = list(core.MICRO.keys())

    def fake(question, requirement, reference):
        for k in order:
            if core.MICRO[k] == question:
                return scores[k]
        raise AssertionError(f"unexpected question {question!r}")
    return patch.object(core, "_micro", side_effect=fake)


def test_confident_yes_passes_on_small_tier():
    with _with_scores(95, 2, 3):
        out = core.judge("ISO 27001 required", "ISO 27001 certificate held")
    assert out["decision"] == "pass"
    assert out["tier_used"] == "small"
    assert out["p_yes"] >= C.T_PASS


def test_confident_no_is_a_gap_on_small_tier():
    with _with_scores(2, 95, 3):
        out = core.judge("ISO 27001 required", "no certificate")
    assert out["decision"] == "gap"
    assert out["tier_used"] == "small"


def test_ambiguous_escalates_to_smart():
    called = {}

    def fake_run(task, tier, payload, fn, cacheable=True):
        called["task"] = task
        return {"answer": "yes", "confidence": 0.95}, False

    with _with_scores(45, 40, 40), patch.object(core, "_run", side_effect=fake_run):
        out = core.judge("comparable experience", "some related work")
    assert called["task"] == "judge.smart"
    assert out["tier_used"] == "smart"
    assert out["decision"] == "pass"


def test_smart_low_confidence_becomes_review_not_pass():
    """The load-bearing rule: never guess a pass on an ambiguous clause."""
    def fake_run(task, tier, payload, fn, cacheable=True):
        return {"answer": "yes", "confidence": 0.4}, False

    with _with_scores(45, 40, 40), patch.object(core, "_run", side_effect=fake_run):
        out = core.judge("comparable experience", "some related work")
    assert out["decision"] == "review"


def test_smart_unclear_becomes_review():
    def fake_run(task, tier, payload, fn, cacheable=True):
        return {"answer": "unclear", "confidence": 0.99}, False

    with _with_scores(45, 40, 40), patch.object(core, "_run", side_effect=fake_run):
        out = core.judge("comparable experience", "some related work")
    assert out["decision"] == "review"


def test_disable_smart_downgrades_to_review_without_escalating():
    def explode(*a, **k):
        raise AssertionError("must not escalate when DISABLE_SMART is set")

    with _with_scores(45, 40, 40), \
            patch.object(C, "DISABLE_SMART", True), \
            patch.object(core.C, "DISABLE_SMART", True), \
            patch.object(core, "_run", side_effect=explode):
        out = core.judge("comparable experience", "some related work")
    assert out["decision"] == "review"
    assert out["tier_used"] == "small"


# ---------------------------------------------------------------- costing
def test_cost_is_per_million_tokens():
    assert C.cost_usd("Qwen/Qwen3-30B-A3B-Instruct-2507", 1_000_000, 0) == pytest.approx(0.10)
    assert C.cost_usd("Qwen/Qwen3-30B-A3B-Instruct-2507", 0, 1_000_000) == pytest.approx(0.30)


def test_unknown_model_costs_zero_rather_than_crashing():
    assert C.cost_usd("who/knows", 1000, 1000) == 0.0
