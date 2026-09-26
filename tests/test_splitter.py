"""Unit tests for the core splitting algorithm.

Standard-library only: runs with ``python3 -m unittest tests.test_splitter``
as well as under pytest.
"""

import random
import unittest
from types import SimpleNamespace as P

from app.splitter import _triple, settle_balances, split_fares


def ride(pid, board, alight):
    return P(id=pid, board=board, alight=alight)


class SplitFaresTests(unittest.TestCase):
    def test_even_split_single_segment(self):
        totals, details = split_fares([90], [ride("a", 0, 1), ride("b", 0, 1)])
        self.assertEqual(totals, {"a": 45, "b": 45})
        self.assertEqual(details[0]["shares"], {"a": 45, "b": 45})

    def test_remainder_tie_broken_by_id(self):
        # 10 / 3 -> base 3, remainder 1; all deficits equal, id "a" wins.
        totals, _ = split_fares([10], [ride("c", 0, 1), ride("a", 0, 1), ride("b", 0, 1)])
        self.assertEqual(totals, {"a": 4, "b": 3, "c": 3})

    def test_deficit_beats_id_order(self):
        # seg0: a,b,c share 10 -> a=4 (id tiebreak), b=3, c=3.
        # seg1: a,b share 7 -> base 3 rem 1; b's deficit (5/6) > a's (-1/6),
        # so b gets the extra cent despite "a" < "b".
        passengers = [ride("a", 0, 2), ride("b", 0, 2), ride("c", 0, 1)]
        totals, details = split_fares([10, 7], passengers)
        self.assertEqual(totals, {"a": 7, "b": 7, "c": 3})
        self.assertEqual(details[1]["shares"], {"a": 3, "b": 4})

    def test_remainder_rotates_over_segments(self):
        passengers = [ride("a", 0, 3), ride("b", 0, 3), ride("c", 0, 3)]
        totals, details = split_fares([100, 100, 100], passengers)
        self.assertEqual(totals, {"a": 100, "b": 100, "c": 100})
        self.assertEqual([d["shares"] for d in details], [
            {"a": 34, "b": 33, "c": 33},
            {"a": 33, "b": 34, "c": 33},
            {"a": 33, "b": 33, "c": 34},
        ])

    def test_passengers_only_pay_while_on_board(self):
        passengers = [ride("alice", 0, 3), ride("bob", 1, 3), ride("carol", 0, 1)]
        totals, details = split_fares([100, 251, 80], passengers)
        # seg1: alice and bob tie on deficit -> alice (id) gets the odd cent.
        self.assertEqual(totals, {"alice": 216, "bob": 165, "carol": 50})
        self.assertEqual(details[0]["passengers"], ["alice", "carol"])
        self.assertEqual(details[1]["shares"], {"alice": 126, "bob": 125})
        self.assertEqual(sum(totals.values()), 431)

    def test_zero_cost_empty_segment_is_allowed(self):
        totals, details = split_fares([0, 10], [ride("a", 1, 2)])
        self.assertEqual(totals, {"a": 10})
        self.assertEqual(details[0], {"index": 0, "cost": 0, "passengers": [], "shares": {}})

    def test_integer_ids_sort_numerically_on_ties(self):
        # id 9 < id 10 numerically, so 9 wins the tie (string order would
        # pick "10" first).
        totals, _ = split_fares([10], [ride(10, 0, 1), ride(9, 0, 1), ride(11, 0, 1)])
        self.assertEqual(totals, {9: 4, 10: 3, 11: 3})


class PropertyTests(unittest.TestCase):
    def test_conservation_invariance_and_fairness(self):
        rng = random.Random(20260919)
        for _ in range(500):
            n_seg = rng.randint(1, 8)
            segments = [rng.randint(0, 999) for _ in range(n_seg)]
            passengers = [ride("anchor", 0, n_seg)]  # covers every segment
            for i in range(rng.randint(0, 5)):
                board = rng.randint(0, n_seg - 1)
                alight = rng.randint(board + 1, n_seg)
                passengers.append(ride(f"p{i}", board, alight))

            totals, details = split_fares(segments, passengers)

            # conservation: per segment and overall
            for seg, cost in zip(details, segments):
                self.assertEqual(sum(seg["shares"].values()), cost)
                n = len(seg["passengers"])
                if n:
                    base, rem = divmod(cost, n)
                    # every share is the equal split or the equal split + 1,
                    # and exactly `rem` passengers get the extra cent
                    self.assertEqual(sorted(seg["shares"].values()),
                                     [base] * (n - rem) + [base + 1] * rem)
            self.assertEqual(sum(totals.values()), sum(segments))

            # input-order invariance: shuffling passengers changes nothing
            shuffled = passengers[:]
            rng.shuffle(shuffled)
            totals2, details2 = split_fares(segments, shuffled)
            self.assertEqual(totals, totals2)
            self.assertEqual(details, details2)


class SettleBalancesTests(unittest.TestCase):
    def _check(self, balances, transfers):
        net = {pid: 0 for pid in balances}
        for frm, to, amount in transfers:
            self.assertGreater(amount, 0)
            net[frm] -= amount
            net[to] += amount
        self.assertEqual(net, {pid: b for pid, b in balances.items()})

    def test_empty_and_all_zero(self):
        self.assertEqual(settle_balances({}), [])
        self.assertEqual(settle_balances({"a": 0, "b": 0}), [])

    def test_single_pair(self):
        balances = {"a": -2, "b": 2}
        transfers = settle_balances(balances)
        self.assertEqual(transfers, [("a", "b", 2)])
        self._check(balances, transfers)

    def test_zero_balances_never_appear(self):
        balances = {"a": -3, "b": 0, "c": 3, "d": 0}
        transfers = settle_balances(balances)
        self.assertEqual(transfers, [("a", "c", 3)])
        self._check(balances, transfers)

    def test_zero_sum_groups_collapse_transfers(self):
        # (-5,-1,+5,+1) splits into two zero-sum groups: d1<->c2 (5)
        # and d2<->c1 (1), needing only 2 transfers instead of the greedy 3.
        balances = {"d1": -5, "d2": -1, "c1": 1, "c2": 5}
        transfers = settle_balances(balances)
        self.assertEqual(transfers, [("d1", "c2", 5), ("d2", "c1", 1)])
        self.assertEqual(len(transfers), 2)
        self._check(balances, transfers)

    def test_tie_broken_by_canonical_id_order(self):
        # One atomic zero-sum group; smallest debtor d1 must pay smallest
        # creditor c1, and the smallest feasible bridge flow is 1.
        balances = {"d1": -6, "d2": -3, "c1": 4, "c2": 5}
        transfers = settle_balances(balances)
        self.assertEqual(
            transfers,
            [("d1", "c1", 1), ("d1", "c2", 5), ("d2", "c1", 3)],
        )
        self._check(balances, transfers)

    def test_integer_ids_compare_numerically(self):
        # 9 sorts before 10 numerically (string order would differ).
        balances = {9: -1, 10: 1}
        self.assertEqual(settle_balances(balances), [(9, 10, 1)])
        balances = {"a": -3, 2: 1, "b": 2}  # ints before strings
        transfers = settle_balances(balances)
        self.assertEqual(transfers, [("a", 2, 1), ("a", "b", 2)])
        self._check(balances, transfers)

    def test_input_order_does_not_matter(self):
        balances = {"a": -6, "b": -3, "c": 4, "d": 5}
        expected = settle_balances(balances)
        for order in [reversed(list(balances)), list("cbad"), list("dbca")]:
            self.assertEqual(
                settle_balances({pid: balances[pid] for pid in order}),
                expected,
            )

    def test_unbalanced_input_is_rejected(self):
        with self.assertRaises(ValueError):
            settle_balances({"a": -2, "b": 1})

    def test_greedy_fallback_beyond_12_nonzero(self):
        # 13 non-zero balances: solver switches to the canonical greedy path;
        # the result must still settle every balance and stay <= n-1 edges.
        balances = {f"d{i}": -2 for i in range(7)}
        balances.update({f"c{i}": 2 for i in range(6)})
        balances["c0"] = 4
        self.assertEqual(len([b for b in balances.values() if b]), 13)
        transfers = settle_balances(balances)
        self.assertLessEqual(len(transfers), 12)
        self._check(balances, transfers)
        self.assertEqual(transfers, sorted(transfers, key=_triple))

    def test_optimum_and_lex_uniqueness_against_brute_force(self):
        rng = random.Random(20260919)
        for _ in range(100):
            n = rng.randint(2, 6)
            nd = rng.randint(1, n - 1)
            debtors = [rng.randint(1, 20) for _ in range(nd)]
            creditors = [rng.randint(1, 20) for _ in range(n - nd)]
            debtors[0] += sum(creditors) - sum(debtors)
            values = [-v for v in debtors] + creditors
            rng.shuffle(values)
            balances = {f"p{i}": values[i] for i in range(n)}
            got = tuple(settle_balances(balances))
            expected = _brute_force_best(balances)
            self.assertEqual(got, expected, balances)


def _brute_force_best(balances):
    """Minimum-count, then lex-smallest, settlement by exhaustive DFS."""
    from app.splitter import _triple
    ids = list(balances)
    best = None

    def rec(net, edges):
        nonlocal best
        if best is not None and len(edges) > len(best):
            return
        if all(v == 0 for v in net.values()):
            seq = tuple(sorted(edges, key=_triple))
            if best is None or (len(seq), seq) < (len(best), best):
                best = seq
            return
        for frm in ids:
            if net[frm] >= 0:
                continue
            for to in ids:
                if net[to] <= 0:
                    continue
                amount = min(-net[frm], net[to])
                nxt = dict(net)
                nxt[frm] += amount
                nxt[to] -= amount
                rec(nxt, edges + [(frm, to, amount)])

    rec(dict(balances), [])
    return best


class SettleOptimalityPropertyTests(unittest.TestCase):
    @staticmethod
    def _min_transfer_count(values):
        """Independent reference: n - (maximum zero-sum subset partition)."""
        n = len(values)
        full = (1 << n) - 1
        sums = [0] * (1 << n)
        for mask in range(1, 1 << n):
            bit = mask & -mask
            sums[mask] = sums[mask ^ bit] + values[bit.bit_length() - 1]
        zero_sums = frozenset(mask for mask in range(1, 1 << n) if sums[mask] == 0)

        groups = [-1] * (1 << n)  # maximum number of zero-sum groups in mask

        def g(mask):
            if mask == 0:
                return 0
            if groups[mask] < 0:
                best = 0
                sub = mask
                while sub:
                    if sub in zero_sums:
                        best = max(best, 1 + g(mask ^ sub))
                    sub = (sub - 1) & mask
                groups[mask] = best
            return groups[mask]

        return n - g(full)

    def test_transfer_count_is_globally_minimal(self):
        rng = random.Random(99173)
        for _ in range(60):
            n = rng.randint(2, 12)
            nd = rng.randint(1, n - 1)
            debtors = [rng.randint(1, 30) for _ in range(nd)]
            creditors = [rng.randint(1, 30) for _ in range(n - nd)]
            debtors[0] += sum(creditors) - sum(debtors)
            values = [-v for v in debtors] + creditors
            rng.shuffle(values)
            balances = {f"p{i}": values[i] for i in range(n)}
            transfers = settle_balances(balances)
            self.assertEqual(len(transfers), self._min_transfer_count(values), balances)
            # settlement is sound and the sequence is canonically ordered
            net = {pid: 0 for pid in balances}
            for frm, to, amount in transfers:
                net[frm] -= amount
                net[to] += amount
            self.assertEqual(net, balances)
            self.assertEqual(transfers, sorted(transfers, key=_triple))


if __name__ == "__main__":
    unittest.main()
