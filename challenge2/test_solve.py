"""Tests for Challenge 2's order matcher — run with `python test_solve.py`."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import solve  # noqa: E402

SHIPPED = HERE.parent / "docs" / "interview-developer-main" / "Challenge 2"


def h(id, total, k):
    return {"id": str(id), "total_price": solve.to_cents(str(total)), "total_lines": k}


def l(name, price):
    return {"description": name, "price": solve.to_cents(str(price))}


def by_id(matched):
    return {m["id"]: m for m in matched}


class TestReadmeExample(unittest.TestCase):
    """The example from the challenge README must reproduce exactly."""

    def test_example(self):
        headers = [h(1, 100, 2), h(2, 200, 2)]
        lines = [l("Item A", 50), l("Item B", 50), l("Item C", 150), l("Item D", 50)]
        matched, uh, ul = solve.match(headers, lines)
        self.assertEqual(uh, [])
        self.assertEqual(ul, [])
        got = solve.to_output(matched, lines)
        expected = [
            {
                "id": "1",
                "total_price": 100,
                "total_lines": 2,
                "order_lines": [
                    {"description": "Item A", "price": 50.0},
                    {"description": "Item B", "price": 50.0},
                ],
            },
            {
                "id": "2",
                "total_price": 200,
                "total_lines": 2,
                "order_lines": [
                    {"description": "Item C", "price": 150.0},
                    {"description": "Item D", "price": 50.0},
                ],
            },
        ]
        self.assertEqual(got, expected)


class TestShippedFixture(unittest.TestCase):
    """The real Challenge 2 CSVs: every header must find its lines."""

    def setUp(self):
        self.headers, self.lines = solve.load_csvs(
            SHIPPED / "order_headers.csv", SHIPPED / "order_lines.csv"
        )

    def test_all_headers_match_with_exact_arithmetic(self):
        matched, uh, ul = solve.match(self.headers, self.lines)
        self.assertEqual([x["id"] for x in uh], [], f"unmatched headers: {uh}")
        self.assertEqual(ul, [], f"unmatched lines: {ul}")
        # every line used exactly once, every order internally consistent
        self.assertEqual(sum(m["total_lines"] for m in matched), len(self.lines))
        for m in matched:
            ssum = sum(self.lines[i]["price"] for i in m["line_ids"])
            self.assertEqual(ssum, m["total_price"])
            self.assertEqual(len(m["line_ids"]), m["total_lines"])
        self.assertEqual(len({i for m in matched for i in m["line_ids"]}), len(self.lines))


class TestSearchBehaviour(unittest.TestCase):
    def test_backtracking_across_headers(self):
        # Order 1's lexicographically first combo (10 + 90) starves order 2,
        # which needs 90 + 40 exactly — the search must backtrack to 50 + 50.
        headers = [h(1, 100, 2), h(2, 130, 2)]
        lines = [l("a", 10), l("b", 40), l("c", 50), l("d", 50), l("e", 60), l("f", 90)]
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual(uh, [])
        got = by_id(matched)
        self.assertEqual(
            [lines[i]["description"] for i in got["2"]["line_ids"]], ["b", "f"]
        )
        self.assertEqual(
            sorted(lines[i]["description"] for i in got["1"]["line_ids"]), ["c", "d"]
        )

    def test_leftover_lines_reported(self):
        headers = [h(1, 50, 1)]
        lines = [l("used", 50), l("orphan", 70)]
        matched, uh, ul = solve.match(headers, lines)
        self.assertEqual(uh, [])
        self.assertEqual([x["description"] for x in ul], ["orphan"])

    def test_unmatchable_header_dropped_but_search_continues(self):
        # Total 0.33 over 2 lines is unreachable in cents; header 2 must still
        # be matched instead of the failure poisoning the whole run.
        headers = [h(1, 0.33, 2), h(2, 100, 1)]
        lines = [l("a", 100), l("b", 11), l("c", 21)]
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual([m["id"] for m in matched], ["2"])
        self.assertEqual([x["id"] for x in uh], ["1"])

    def test_cent_exactness_no_float_drift(self):
        # 0.1 + 0.2 == 0.30000000000000004 in floats; the matcher works in cents.
        headers = [h(1, 0.30, 2)]
        lines = [l("a", 0.10), l("b", 0.20)]
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual(uh, [])
        self.assertEqual(len(matched[0]["line_ids"]), 2)

    def test_duplicate_prices_dont_confuse_counts(self):
        # 150 can only be 50 + 100 here; the count constraint rules out
        # doubling up on the two 50s.
        headers = [h(1, 150, 2)]
        lines = [l("a", 50), l("b", 50), l("c", 100)]
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual(uh, [])
        self.assertEqual(len(matched[0]["line_ids"]), 2)

    def test_k_exceeds_available_lines(self):
        headers = [h(1, 60, 3)]
        lines = [l("a", 20), l("b", 40)]
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual(matched, [])
        self.assertEqual([x["id"] for x in uh], ["1"])


class TestScale(unittest.TestCase):
    @staticmethod
    def built(n_orders, n_lines, sizes, seed=42):
        rng = random.Random(seed)
        lines = [l(f"line {i}", rng.randint(100, 99000) / 100) for i in range(n_lines)]
        headers, take = [], 0
        for i in range(n_orders):
            k = rng.choice(sizes)
            take += k
            if take > n_lines:
                break
            total = sum(lines[take - j - 1]["price"] for j in range(k))
            headers.append(h(i, total / 100, k))
        return headers, lines

    def test_constructed_instance_solves_fast(self):
        # 20 orders over a 120-line pool, built so a complete matching exists;
        # measured fast (<0.5 s) on exhaustive search — guards against
        # accidental exponential blowups at the documented working scale.
        # (Larger/denser instances are budget territory — next test.)
        headers, lines = self.built(20, 120, [1, 2, 3])
        matched, uh, _ = solve.match(headers, lines)
        self.assertEqual(uh, [])
        self.assertEqual(len(matched), len(headers))

    def test_node_budget_bounds_work_and_reports_honestly(self):
        # A mixed-size dense instance where exhaustive search thrashes; the
        # node-budget knob must terminate quickly and return valid partials.
        headers, lines = self.built(12, 120, [2, 5, 8, 10])
        matched, uh, ul = solve.match(headers, lines, node_budget=1000)
        prices = [x["price"] for x in lines]
        claimed = [i for m in matched for i in m["line_ids"]]
        self.assertEqual(len(claimed), len(set(claimed)))
        for m in matched:
            self.assertEqual(sum(prices[i] for i in m["line_ids"]), m["total_price"])
            self.assertEqual(len(m["line_ids"]), m["total_lines"])
        self.assertEqual(
            len(matched) + len(uh), len(headers), "every header accounted for"
        )
        # deterministic: same budget, same answer
        again, uh2, _ = solve.match(headers, lines, node_budget=1000)
        self.assertEqual([m["line_ids"] for m in again], [m["line_ids"] for m in matched])


class TestCli(unittest.TestCase):
    def test_cli_writes_expected_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            rc = solve.main(
                [
                    str(SHIPPED / "order_headers.csv"),
                    str(SHIPPED / "order_lines.csv"),
                    "-o",
                    str(out),
                ]
            )
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text())
            self.assertEqual(len(data), 5)
            for order in data:
                self.assertEqual(
                    round(sum(x["price"] for x in order["order_lines"]), 2),
                    order["total_price"],
                )
                self.assertEqual(len(order["order_lines"]), order["total_lines"])


if __name__ == "__main__":
    unittest.main(verbosity=2)