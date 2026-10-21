#!/usr/bin/env python3
"""Challenge 2 — reconnect order headers with their order lines.

Procurement lost the header <-> line links. Given `order_headers.csv`
(id, total_price, total_lines) and `order_lines.csv` (description, price),
find for every header the set of lines whose prices sum exactly to its
total_price and whose count equals its total_lines. Each line belongs to at
most one order; lines belonging to no header are reported, not guessed.

Algorithm
---------
Exact money arithmetic in integer cents (Decimal x 100), then a complete
backtracking search over headers with three prunings:

1. Prefix-bound pruning inside each header's k-combination search: with the
   available prices sorted ascending, the cheapest/most expensive m remaining
   picks are prefix sums, so a branch dies the moment the target falls
   outside [min, max] of what is still reachable.
2. Global feasibility pruning between headers: before enumerating a header's
   combinations, check every still-unsolved header (and their aggregate
   total/count) against the currently unclaimed lines. A branch that has
   already starved a future order is cut immediately.
3. Hardest-first header ordering (largest total_lines first): high-cardinality
   sum constraints are the rarest and prune the most; ties keep file order.

A complete matching is demanded first: every header must be matched before
any header may be dropped, so a solvable instance never produces a partial
answer. Only after that search fails does a second pass run with skipping
allowed (a header is dropped only when its combinations are exhausted) —
such a partial answer is the first one found in deterministic search order,
not a provably maximal one. If several complete assignments are valid, the
first one in deterministic search order is returned (the problem accepts any
valid combination).

Exact subset-sum is NP-hard, so worst case is exponential — these CSVs are
interview-scale and solve instantly; the pruning keeps realistic sizes
(tens of headers, hundreds of lines) fast.

Usage
-----
    python solve.py                        # the shipped Challenge 2 CSVs
    python solve.py HEADERS.CSV LINES.CSV  # or your own
    python solve.py ... -o out.json        # default: matched_orders.json
    python solve.py ... --node-budget N   # stop after N search nodes (dense
                                          # instances; may then return
                                          # partial results)

Output follows the README schema: a JSON list of
{id, total_price, total_lines, order_lines}, written to the output file and
stdout; unmatched headers/lines go to stderr as warnings.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path


def to_cents(text: str) -> int:
    """Parse a money string into integer cents (no float is ever involved)."""
    return int(Decimal(text.strip()).quantize(Decimal("0.01")).scaleb(2))


def load_csvs(headers_path: str, lines_path: str):
    """Read both CSVs; prices come back as integer cents."""
    headers = []
    with open(headers_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            headers.append(
                {
                    "id": row["id"].strip(),
                    "total_price": to_cents(row["total_price"]),
                    "total_lines": int(row["total_lines"]),
                }
            )
    lines = []
    with open(lines_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lines.append(
                {
                    "description": row["description"],
                    "price": to_cents(row["price"]),
                }
            )
    return headers, lines


def _prefix_sums(vals):
    p = [0]
    for v in vals:
        p.append(p[-1] + v)
    return p


def _combos(vals, target, k, tick=None):
    """Generate index-tuples of length k into ascending `vals` summing to
    `target`, lazily so the caller can stop after a few.

    Multiset-deduplicated (a duplicate value never restarts a branch) and
    pruned: once the m cheapest remaining picks overshoot `target`, or the m
    most expensive undershoot, the branch is dead. Every internal DFS step
    calls `tick()` first, which may abort the walk (used to enforce the
    search-wide node budget).
    """
    n = len(vals)
    p = _prefix_sums(vals)
    if k > n:
        return
    chosen = []

    def rec(start, need, m):
        if tick:
            tick()
        if m == 0:
            if need == 0:
                yield tuple(chosen)
            return
        for i in range(start, n - m + 1):
            if p[i + m] - p[i] > need:  # m cheapest from i already overshoot
                break
            if p[n] - p[n - m] < need:  # even the m largest undershoot
                return
            if i > start and vals[i] == vals[i - 1]:
                continue  # same value; the branch was already explored
            chosen.append(i)
            yield from rec(i + 1, need - vals[i], m - 1)
            chosen.pop()

    yield from rec(0, target, k)


class _BudgetExhausted(Exception):
    """Internal: search stopped after spending its node budget."""


def _rest_feasible(headers, rest, vals):
    """Can every unsolved header in `rest` still be satisfied from `vals`?

    Necessary conditions only (per-header and aggregate count/sum windows) —
    cheap, and they cut branches that already starved a future order.
    """
    n = len(vals)
    if n == 0:
        return not rest
    p = _prefix_sums(vals)

    def within(target, k):
        if k > n:
            return False
        return p[k] <= target <= p[n] - p[n - k]

    for i in rest:
        if not within(headers[i]["total_price"], headers[i]["total_lines"]):
            return False
    k_all = sum(headers[i]["total_lines"] for i in rest)
    t_all = sum(headers[i]["total_price"] for i in rest)
    return within(t_all, k_all)


def match(headers, lines, node_budget=None):
    """Match headers to lines.

    Returns (matched, unmatched_headers, unmatched_lines) where `matched` is
    a list of {"id", "total_price", "total_lines", "line_ids"} in header file
    order; `headers`/`lines` carry prices as integer cents.

    `node_budget` stops the search after that many DFS steps (None =
    exhaustive). One step is any search-tree decision: choosing a header or
    extending/closing one of its combinations. Exact subset-sum with a
    cardinality constraint is NP-hard, so dense instances can hit the
    exponential cliff; with a budget the search gives up deterministically —
    unmatched headers are then reported, never guessed.
    """
    prices = [l["price"] for l in lines]
    n = len(prices)
    order = sorted(range(len(headers)), key=lambda i: (-headers[i]["total_lines"], i))

    def search(allow_skip):
        """DFS over headers; returns (used, chosen_for) or (None, None).

        allow_skip=False demands a complete matching — the skip branch is
        off, so a dead end always backtracks. Only if that fails (no complete
        matching exists) does a second pass run with skips allowed, taken
        solely when a header's combinations are exhausted.
        """
        used = [False] * n
        chosen_for = [None] * len(headers)
        state = {"nodes": 0}

        def tick():
            state["nodes"] += 1
            if node_budget is not None and state["nodes"] > node_budget:
                raise _BudgetExhausted

        def available():
            av = sorted((prices[i], i) for i in range(n) if not used[i])
            return [p for p, _ in av], [i for _, i in av]

        def rec(oi):
            tick()
            if oi == len(order):
                return True
            hi = order[oi]
            k = headers[hi]["total_lines"]
            target = headers[hi]["total_price"]
            vals, idxs = available()
            # Matching this header can only shrink availability, so if some
            # unsolved header is already unsatisfiable, enumerating combos of
            # this one is wasted work.
            feasible = _rest_feasible(headers, order[oi:], vals)
            if feasible:
                for pos in _combos(vals, target, k, tick):
                    line_ids = [idxs[p] for p in pos]
                    for lid in line_ids:
                        used[lid] = True
                    chosen_for[hi] = line_ids
                    if rec(oi + 1):
                        return True
                    chosen_for[hi] = None
                    for lid in line_ids:
                        used[lid] = False
            return rec(oi + 1) if (allow_skip and not feasible) else False

        try:
            ok = rec(0)
        except _BudgetExhausted:
            return None, None
        return (used, chosen_for) if ok else (None, None)

    found = search(allow_skip=False)  # prefer a complete matching
    if found[1] is None:
        found = search(allow_skip=True)
    used, chosen_for = found
    if chosen_for is None:
        return [], list(headers), list(lines)

    matched, unmatched_headers = [], []
    for hi, h in enumerate(headers):
        if chosen_for[hi] is None:
            unmatched_headers.append(h)
            continue
        matched.append(
            {
                "id": h["id"],
                "total_price": h["total_price"],
                "total_lines": h["total_lines"],
                "line_ids": chosen_for[hi],
            }
        )
    matched_lines = {lid for m in matched for lid in m["line_ids"]}
    unmatched_lines = [l for i, l in enumerate(lines) if i not in matched_lines]

    # Defense in depth: the search is complete, but never ship an order whose
    # own arithmetic is wrong.
    for m in matched:
        got = sum(prices[i] for i in m["line_ids"])
        assert got == m["total_price"], f"order {m['id']}: sum {got} != {m['total_price']}"
        assert len(m["line_ids"]) == m["total_lines"], f"order {m['id']}: line count mismatch"
    assert len(matched_lines) == sum(1 for b in used if b), "line double-use"

    return matched, unmatched_headers, unmatched_lines


def to_output(matched, lines):
    """Render matches in the README schema, with prices as decimal floats."""
    out = []
    for m in matched:
        out.append(
            {
                "id": m["id"],
                "total_price": m["total_price"] / 100,
                "total_lines": m["total_lines"],
                "order_lines": [
                    {
                        "description": lines[i]["description"],
                        "price": lines[i]["price"] / 100,
                    }
                    for i in sorted(m["line_ids"])
                ],
            }
        )
    return out


def main(argv=None):
    here = Path(__file__).resolve().parent
    shipped = here.parent / "docs" / "interview-developer-main" / "Challenge 2"
    ap = argparse.ArgumentParser(description="Challenge 2: match order headers with order lines")
    ap.add_argument("headers", nargs="?", default=str(shipped / "order_headers.csv"))
    ap.add_argument("lines", nargs="?", default=str(shipped / "order_lines.csv"))
    ap.add_argument("-o", "--out", default=str(here / "matched_orders.json"))
    ap.add_argument(
        "--node-budget",
        type=int,
        default=None,
        help="stop the search after N nodes (default: exhaustive)",
    )
    args = ap.parse_args(argv)

    headers, lines = load_csvs(args.headers, args.lines)
    matched, unmatched_headers, unmatched_lines = match(headers, lines, args.node_budget)
    for h in unmatched_headers:
        print(
            f"warning: header {h['id']} ({h['total_price']/100:.2f} over "
            f"{h['total_lines']} lines) has no valid combination — excluded",
            file=sys.stderr,
        )
    if unmatched_lines:
        print(
            f"warning: {len(unmatched_lines)} line(s) belong to no order: "
            + "; ".join(f"{l['description']} ({l['price']/100:.2f})" for l in unmatched_lines),
            file=sys.stderr,
        )

    out = to_output(matched, lines)
    text = json.dumps(out, indent=4, ensure_ascii=False)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())