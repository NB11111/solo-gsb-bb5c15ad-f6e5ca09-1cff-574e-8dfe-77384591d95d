"""Core fare-splitting algorithm.

Pure standard library (exact ``fractions.Fraction`` arithmetic), so it can be
unit-tested without any web dependencies and stays free of float rounding
errors. Money amounts are integer cents; theoretical shares are exact
fractions.
"""

from __future__ import annotations

from fractions import Fraction
from functools import lru_cache
from typing import Dict, List, Tuple


def tie_key(passenger_id) -> tuple:
    """Total, input-order-independent order used ONLY to break deficit ties.

    Integer ids compare numerically and sort before string ids, which compare
    lexicographically. Because every ranking ends with this key, permuting the
    passengers in the request never changes the outcome.
    """
    if isinstance(passenger_id, bool):  # bool is an int subclass; keep it sane
        return (1, str(passenger_id))
    if isinstance(passenger_id, int):
        return (0, passenger_id)
    return (1, str(passenger_id))


def split_fares(segment_costs: List[int], passengers) -> Tuple[Dict, List[dict]]:
    """Split per-segment costs between passengers, segment by segment.

    ``passengers`` items must expose ``id``, ``board`` and ``alight``
    attributes; a passenger occupies segments ``board <= i < alight``.

    Per segment the cost is shared equally between everyone on board. When the
    cost does not divide evenly, the leftover cents (``cost % n``) go one each
    to the passengers with the largest *deficit* — their cumulative exact
    theoretical share minus what they have actually been allocated so far —
    with ties broken by :func:`tie_key`.

    Returns ``(totals, details)``: ``totals`` maps passenger id -> allocated
    cents (inserted in canonical id order); ``details`` is a per-segment list
    of dicts with keys ``index``, ``cost``, ``passengers`` and ``shares``.
    """
    allocated = {p.id: 0 for p in passengers}
    theoretical = {p.id: Fraction(0) for p in passengers}
    details: List[dict] = []

    for index, cost in enumerate(segment_costs):
        onboard = sorted(
            (p.id for p in passengers if p.board <= index < p.alight),
            key=tie_key,
        )
        if not onboard:
            # Only reachable for zero-cost segments; charged empty segments
            # are rejected during request validation (HTTP 422).
            details.append({"index": index, "cost": cost, "passengers": [], "shares": {}})
            continue

        base, remainder = divmod(cost, len(onboard))
        share = Fraction(cost, len(onboard))
        for pid in onboard:
            theoretical[pid] += share

        # Deficit if everyone on board received only the base amount: how far
        # the passenger's actual allocation would lag their cumulative
        # theoretical share. (Adding the current segment's share and base to
        # every onboard passenger shifts all deficits by the same constant,
        # so the ranking is identical to using pre-segment state.)
        deficits = {pid: theoretical[pid] - (allocated[pid] + base) for pid in onboard}
        ranked = sorted(onboard, key=lambda pid: (-deficits[pid], tie_key(pid)))
        extra = set(ranked[:remainder])

        shares = {pid: base + (1 if pid in extra else 0) for pid in onboard}
        for pid, amount in shares.items():
            allocated[pid] += amount

        details.append(
            {"index": index, "cost": cost, "passengers": onboard, "shares": shares}
        )

    totals = {pid: allocated[pid] for pid in sorted(allocated, key=tie_key)}
    return totals, details


# --- settlement: turn balances (paid - owed) into a minimal set of transfers


def _triple(transfer):
    """Comparable identity of one transfer: ``(from_key, to_key, amount)``."""
    frm, to, amount = transfer[:3]
    return (tie_key(frm), tie_key(to), amount)


def settle_balances(balances: Dict) -> List[Tuple]:
    """Settle integer ``balances`` (id -> paid - owed cents) with transfers.

    Every balance must be an integer; positive participants overpaid,
    negative participants underpaid, and the values sum to zero. Returns a
    list of ``(from, to, amount)`` tuples where ``from`` owes ``amount`` cents
    to ``to``; zero-balance participants never appear.

    For at most 12 non-zero balances the result has the globally minimal
    number of transfers (found with an exact subset partition + bridge-cut
    dynamic program). When several minima exist, the sequence of
    ``(from, to, amount)`` triples — compared in the canonical passenger-id
    order defined by :func:`tie_key` — is lexicographically minimal, which
    makes the result independent of the dict's input order. Beyond 12
    non-zero balances a deterministic greedy settlement (no worse than
    ``n - 1`` transfers) is used instead.
    """
    nonzero = [
        (pid, balances[pid])
        for pid in sorted(balances, key=tie_key)
        if balances[pid] != 0
    ]
    if sum(amount for _, amount in nonzero) != 0:
        raise ValueError("balances must sum to zero")
    if len(nonzero) > 12:
        return _greedy_transfers(nonzero)
    return _optimal_transfers(nonzero)


def _greedy_transfers(nonzero: List[Tuple]) -> List[Tuple]:
    """Canonical greedy fallback: smallest debtor pays smallest creditor.

    Always terminates within ``n - 1`` transfers; used only when too many
    non-zero balances exist for the exact solver to be worthwhile.
    """
    state = {pid: amount for pid, amount in nonzero}
    transfers: List[Tuple] = []
    while state:
        debtor = min((pid for pid in state if state[pid] < 0), key=tie_key)
        creditor = min((pid for pid in state if state[pid] > 0), key=tie_key)
        amount = min(-state[debtor], state[creditor])
        transfers.append((debtor, creditor, amount))
        state[debtor] += amount
        state[creditor] -= amount
        if state[debtor] == 0:
            del state[debtor]
        if creditor in state and state[creditor] == 0:
            del state[creditor]
    return sorted(transfers, key=_triple)


def _optimal_transfers(nonzero: List[Tuple]) -> List[Tuple]:
    """Exact minimum-transfer settlement (``len(nonzero) <= 12``).

    Idea: view transfers as edges of a flow graph. A set of participants can
    be settled internally iff its balances sum to zero; hence the minimum
    number of transfers is ``n - k`` where ``k`` is the maximum number of
    non-empty zero-sum subsets partitioning the participants ("atomic"
    groups admit no smaller zero-sum subset and need ``size - 1`` transfers).

    The best partition is found by subset DP; inside each atomic group the
    lexicographically smallest transfer forest is built by recursively
    forcing a bridge from the smallest debtor to the smallest creditor and
    enumerating the cut (flow) across that bridge.
    """
    n = len(nonzero)
    if n == 0:
        return []
    amounts = [amount for _, amount in nonzero]
    full = (1 << n) - 1

    sums = [0] * (1 << n)
    for mask in range(1, 1 << n):
        bit = mask & -mask
        sums[mask] = sums[mask ^ bit] + amounts[bit.bit_length() - 1]

    @lru_cache(maxsize=None)
    def is_atomic(mask: int) -> bool:
        """True for a zero-sum mask with no proper non-empty zero-sum subset."""
        if sums[mask] != 0:
            return False
        rest = mask ^ (mask & -mask)
        if rest == 0:
            return True
        sub = (rest - 1) & rest
        while sub:
            # A zero-sum subset containing the low bit would leave a zero-sum
            # complement in `rest`, so subsets of `rest` suffice.
            if sums[sub] == 0 or sums[rest ^ sub] == 0:
                return False
            sub = (sub - 1) & rest
        return True

    @lru_cache(maxsize=None)
    def solve_state(canonical):
        """Lexicographically smallest minimal transfer tuple for one state.

        ``canonical`` is a tuple of ``(id, balance)`` pairs in canonical id
        order, summing to zero. The lex-min forest must contain an edge from
        the smallest debtor ``d`` to the smallest creditor ``c``; enumerate
        its flow ``t`` via the subset staying on d's side of the cut.
        """
        if len(canonical) <= 1:
            return ()
        debtors = sorted((pid for pid, b in canonical if b < 0), key=tie_key)
        creditors = sorted((pid for pid, b in canonical if b > 0), key=tie_key)
        if not debtors:
            return ()  # all balances zero: nothing to settle
        d_id, c_id = debtors[0], creditors[0]
        balance = dict(canonical)
        d_bal, c_bal = balance[d_id], balance[c_id]
        others = [pid for pid, _ in canonical if pid != d_id and pid != c_id]

        best = None
        for amask in range(1 << len(others)):
            # Side A keeps d; side B keeps c. A's net balance is -t, the
            # amount d pays across the bridge to c.
            side_a = {d_id: d_bal}
            for j, pid in enumerate(others):
                if amask & (1 << j):
                    side_a[pid] = balance[pid]
            t = -sum(side_a.values())
            if t <= 0 or t > -d_bal or t > c_bal:
                continue
            side_a[d_id] += t
            side_b = {c_id: c_bal - t}
            for j, pid in enumerate(others):
                if not (amask & (1 << j)):
                    side_b[pid] = balance[pid]
            state_a = tuple(sorted(
                ((pid, bal) for pid, bal in side_a.items() if bal != 0),
                key=lambda item: tie_key(item[0]),
            ))
            state_b = tuple(sorted(
                ((pid, bal) for pid, bal in side_b.items() if bal != 0),
                key=lambda item: tie_key(item[0]),
            ))
            candidate = tuple(sorted(
                ((d_id, c_id, t),) + solve_state(state_a) + solve_state(state_b),
                key=_triple,
            ))
            if best is None or _better(candidate, best):
                best = candidate
        return best

    def _better(candidate, best) -> bool:
        if len(candidate) != len(best):
            return len(candidate) < len(best)
        return [_triple(t) for t in candidate] < [_triple(t) for t in best]

    @lru_cache(maxsize=None)
    def partition(mask: int):
        """Best transfer tuple for the zero-sum subset ``mask``.

        The component holding its least set bit must be an atomic zero-sum
        subset; enumerate those and recurse on the remainder.
        """
        if mask == 0:
            return ()
        low = mask & -mask
        remaining = mask ^ low
        best = None
        sub = remaining
        while True:
            atom = low | sub
            if is_atomic(atom):
                group = tuple(sorted(
                    ((nonzero[i][0], amounts[i])
                     for i in range(n) if atom & (1 << i)),
                    key=lambda item: tie_key(item[0]),
                ))
                candidate = tuple(sorted(
                    solve_state(group) + partition(remaining ^ sub),
                    key=_triple,
                ))
                if best is None or _better(candidate, best):
                    best = candidate
            if sub == 0:
                break
            sub = (sub - 1) & remaining
        return best

    return [
        (frm, to, amount)
        for frm, to, amount in sorted(partition(full), key=_triple)
    ]
