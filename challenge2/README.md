# Challenge 2 — order-header ↔ order-line matcher

Procurement lost the link between order headers and order lines. This tool
reconnects them: for every header, find the lines whose prices sum **exactly**
to its `total_price` and whose count equals its `total_lines`. Each line is
used by at most one order; orphans are reported, never guessed away.

## Run

```bash
python3 solve.py                          # shipped Challenge 2 CSVs (docs/)
python3 solve.py headers.csv lines.csv    # your own
python3 solve.py -o out.json              # default: matched_orders.json
python3 test_solve.py                     # test suite (stdlib unittest)
```

`matched_orders.json` from the shipped CSVs is committed as the answer.

## What the data says

The shipped instance is fully conserved: the header totals (558.12) equal the
sum of all 13 line prices, and 1+2+1+4+5 = 13 lines, so every header finds a
complete matching and every line is used exactly once. Some combinations are
ambiguous (e.g. order 2, 85.85 over 2 lines) — the solver returns one valid
assignment deterministically.

## Design notes (worth defending)

- **Money is integer cents end-to-end.** Prices are parsed with
  `decimal.Decimal × 100`; sums are compared as ints. Float addition makes
  `0.10 + 0.20 != 0.30` — an exactness bug that would silently drop valid
  matches. Output converts back to floats only at the JSON boundary.
  (Assumption per the README: two decimal places.)
- **Complete-backtracking search, pruned three ways.** Exact subset-sum with
  a cardinality constraint is NP-hard; the search is exhaustive but the
  pruning keeps realistic instances instant:
  1. *Prefix-bound pruning* inside a header's k-combination search — with
     available prices sorted, the cheapest/most-expensive m remaining picks
     are prefix sums, so a branch dies the moment the target leaves
     `[min, max]` of what is reachable.
  2. *Cross-header feasibility pruning* — before enumerating a header's
     combinations, every still-unsolved header (and their aggregate
     total/count) is checked against the currently unclaimed lines; a branch
     that already starved a future order is cut immediately.
  3. *Hardest-first ordering* — headers sorted by descending `total_lines`
     (ties keep file order): high-cardinality sum constraints are the
     rarest and prune the most.
- **A complete matching is demanded first.** The first search forbids
  dropping a header, so a solvable instance can never come back partial —
  reaching the end of the search without satisfying every header forces
  backtracking rather than a premature `success`. Only when no complete
  matching exists does a second pass allow skipping a header (only when its
  combinations are exhausted); unmatched headers and lines are then reported
  on stderr. A skipped-out partial result is the first deterministic answer,
  not a provably maximal one — if maximizing coverage of unsatisfiable
  instances mattered, that would be an ILP/branch-and-bound refinement.
- **A node budget for dense instances.** The search is exact but NP-hard;
  the cliff is steep and measured: 20 orders / 120 lines solve in 0.1 s,
  25 orders / 150 lines take 20 s, and dense mixed-size instances thrash
  beyond that. `--node-budget N` aborts the search deterministically after
  N search-tree steps (the budget is ticked inside the combination lookup
  too — every branch of it counts, not just header selections). A run that
  gives up returns valid matches plus reported orphans, never invented
  ones. At genuinely adversarial scale this problem is CP-SAT/ILP territory
  (OR-Tools), not exhaustive DFS — that's the honest boundary of a
  dependency-free solver.
- **Deterministic output.** Lines are enumerated in stable price order with
  duplicate values collapsed, so the same input always yields the same
  matching; the README explicitly accepts any valid combination.
- **Honesty at the edges:** an unsatisfiable header and orphan lines are
  excluded from the output and reported, and every shipped result is
  re-verified (sum = `total_price`, count = `total_lines`, no line reused)
  before the JSON is written.

## Test suite (`test_solve.py`, stdlib unittest)

README example reproduced exactly; the shipped CSVs (all 5 orders, exact
cents, no orphans, every line used once); cross-header backtracking (a
greedy combo for order 1 starves order 2 — the search must backtrack);
orphan lines; an unsatisfiable header dropped without poisoning the run;
no float drift (`0.10 + 0.20 = 0.30`); duplicate prices under a count
constraint; `k` exceeding the available lines; a 20-order/120-line
constructed instance at the exhaustive working scale (~0.1 s); a dense
mixed-size instance solved under `--node-budget` (valid partials, full
accounting, deterministic); CLI end-to-end.

## Known limits

- Worst case is exponential (the problem is NP-hard); without a budget a
  pathological, deliberately adversarial instance (many near-miss sums)
  could still run long. `--node-budget N` is the deterministic cap, at the
  cost of possibly returning fewer matches than exist.
- Prices are assumed positive with two decimals (per the challenge README);
  a negative or malformed price fails fast at parse time.