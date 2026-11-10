"""Small-sample statistics for the benchmark: is a rate clear of its gate, and is a gap between two versions real?

Standard library only. Two tools:
- wilson(k, n): a 95% interval for a rate such as 8 wrong of 40. Unlike "rate +/- 2 standard errors", it stays
  inside 0-100% and behaves at 0 of n.
- mcnemar(b, c): the exact two-sided McNemar test for two versions scored on the same cases. b and c are the
  cases only one version got right. Cases both got right or both got wrong carry no information about which
  version is better, so a gap of 30 vs 28 right, made of 2 disagreements, is judged on those 2 alone.
"""
import math

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    return (0.0 if k == 0 else lo), (1.0 if k == n else hi)     # exact at the ends, not 0.9999...


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value: the chance of a split at least this uneven if neither version is better."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wins_needed(alpha: float = 0.05) -> int:
    """The fewest disagreements one version must win, losing none, for the split to reach p < alpha."""
    n = 1
    while mcnemar(n, 0) >= alpha:
        n += 1
    return n


def against_gate(k: int, n: int, limit: float, side: str) -> str:
    """Where a rate's 95% interval sits against a gate limit.

    side "max": the rate must be at most the limit (wrong causes, false alarms).
    side "min": the rate must be at least the limit (incidents right).
    """
    if n == 0:
        return "no cases"
    lo, hi = wilson(k, n)
    good, bad = (hi <= limit, lo > limit) if side == "max" else (lo >= limit, hi < limit)
    if good:
        return "passes for sure"
    if bad:
        return "fails for sure"
    return "can't tell at this size"


def paired(rows: list[dict], a: str, b: str) -> dict:
    """Compare versions a and b on the same cases, with "right" as success.

    rows are the "cases" of a results file: each has an "id" and "answers"[version]["outcome"].
    """
    rows = [r for r in rows if a in r["answers"] and b in r["answers"]]
    ra = {r["id"]: r["answers"][a]["outcome"] == "right" for r in rows}
    rb = {r["id"]: r["answers"][b]["outcome"] == "right" for r in rows}
    a_only = [i for i in ra if ra[i] and not rb[i]]
    b_only = [i for i in ra if rb[i] and not ra[i]]
    same = sum(r["answers"][a]["outcome"] == r["answers"][b]["outcome"] for r in rows)
    return {"cases": len(rows), "same_outcome": same,
            "both_right": sum(ra[i] and rb[i] for i in ra), "both_not_right": sum(not ra[i] and not rb[i] for i in ra),
            "a_only": a_only, "b_only": b_only, "p": mcnemar(len(a_only), len(b_only))}
