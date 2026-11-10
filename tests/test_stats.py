import pytest

from eval.stats import against_gate, mcnemar, paired, wilson, wins_needed


def test_mcnemar_exact_values():
    assert mcnemar(0, 0) == 1.0                    # no disagreements: no evidence either way
    assert mcnemar(2, 0) == 0.5                    # 30 vs 28 right, made of 2 disagreements
    assert mcnemar(5, 0) == 0.0625
    assert mcnemar(6, 0) == 0.03125
    assert mcnemar(3, 3) == 1.0
    assert mcnemar(1, 7) == mcnemar(7, 1) == pytest.approx(0.0703125)


def test_wins_needed_at_five_percent():
    assert wins_needed() == 6


def test_wilson_interval():
    lo, hi = wilson(8, 40)                          # 20% wrong causes on 40 cases
    assert lo == pytest.approx(0.105, abs=0.001) and hi == pytest.approx(0.348, abs=0.001)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    assert wilson(0, 0) == (0.0, 1.0)


def test_against_gate():
    assert against_gate(8, 40, 0.05, "max") == "fails for sure"
    assert against_gate(2, 12, 0.10, "max") == "can't tell at this size"
    assert against_gate(18, 28, 0.75, "min") == "can't tell at this size"
    assert against_gate(0, 200, 0.05, "max") == "passes for sure"
    assert against_gate(5, 40, 0.75, "min") == "fails for sure"


def test_paired_counts_only_disagreements():
    def row(i, a, b):
        return {"id": i, "answers": {"a": {"outcome": a}, "b": {"outcome": b}}}
    rows = [row("1", "right", "right"), row("2", "right", "wrong"), row("3", "wrong", "wrong"),
            row("4", "right", "abstained"), row("5", "missed", "wrong")]
    p = paired(rows, "a", "b")
    assert p["a_only"] == ["2", "4"] and p["b_only"] == []
    assert p["both_right"] == 1 and p["both_not_right"] == 2 and p["same_outcome"] == 2
    assert p["p"] == 0.5
