"""Request/response schemas and request validation.

All domain-rule violations (invalid intervals, out-of-range stations,
duplicate ids, empty-load charged segments, ...) are raised as
``ValueError`` inside Pydantic validators, so FastAPI turns them into a
uniform HTTP 422 response.
"""

from __future__ import annotations

from typing import Dict, List, Union

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator

PassengerId = Union[str, int]


class Passenger(BaseModel):
    """One passenger's ride, expressed as station indices.

    A trip with ``N`` segments has stations ``0..N``; a passenger who boards
    at ``board`` and alights at ``alight`` occupies segments
    ``board, board+1, ..., alight-1``.
    """

    model_config = ConfigDict(extra="forbid")

    id: PassengerId = Field(description="unique passenger id (string or integer)")
    board: NonNegativeInt = Field(description="station index where the passenger boards")
    alight: NonNegativeInt = Field(description="station index where the passenger alights")


class Candidate(Passenger):
    """A passenger asking to be admitted, plus a personal fare cap.

    Boarding/alighting rules are identical to :class:`Passenger`; ``max_fare``
    is the most (integer cents) the candidate is willing to owe in total after
    the fare is re-split over the combined passenger list.
    """

    max_fare: NonNegativeInt = Field(
        description="maximum total cents the candidate is willing to pay; must be >= 0"
    )


class SplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: List[NonNegativeInt] = Field(
        min_length=1,
        description="integer cost of each segment in cents, in travel order",
    )
    passengers: List[Passenger] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_trip(self) -> "SplitRequest":
        n = len(self.segments)

        seen = set()
        for p in self.passengers:
            key = str(p.id)
            if key in seen:
                raise ValueError(f"duplicate passenger id: {p.id!r}")
            seen.add(key)
            if p.board >= p.alight:
                raise ValueError(
                    f"passenger {p.id!r}: invalid interval board={p.board}, "
                    f"alight={p.alight} (board must be < alight)"
                )
            if p.alight > n:
                raise ValueError(
                    f"passenger {p.id!r}: alight station {p.alight} is out of range; "
                    f"a trip with {n} segment(s) has stations 0..{n}"
                )

        for i, cost in enumerate(self.segments):
            if cost > 0 and not any(p.board <= i < p.alight for p in self.passengers):
                raise ValueError(
                    f"segment {i} has cost {cost} but no passengers on board "
                    f"(empty-load charged segment)"
                )
        return self


class AdmitRequest(SplitRequest):
    """A split trip plus a seat ``capacity`` and up to 12 candidates.

    The endpoint chooses the best admissible subset of the candidates; see
    :func:`app.splitter.admit_candidates`.
    """

    capacity: PositiveInt = Field(
        description="uniform per-segment seat cap; the existing passengers "
                    "themselves must not exceed it on any segment"
    )
    candidates: List[Candidate] = Field(
        default_factory=list,
        max_length=12,
        description="up to 12 candidate passengers, each with a max_fare cap",
    )

    @model_validator(mode="after")
    def _check_admission(self) -> "AdmitRequest":
        n = len(self.segments)

        existing = {str(p.id) for p in self.passengers}
        seen = set()
        for c in self.candidates:
            key = str(c.id)
            if key in seen:
                raise ValueError(f"duplicate candidate id: {c.id!r}")
            if key in existing:
                raise ValueError(
                    f"candidate id {c.id!r} is already an existing passenger"
                )
            seen.add(key)
            if c.board >= c.alight:
                raise ValueError(
                    f"candidate {c.id!r}: invalid interval board={c.board}, "
                    f"alight={c.alight} (board must be < alight)"
                )
            if c.alight > n:
                raise ValueError(
                    f"candidate {c.id!r}: alight station {c.alight} is out of range; "
                    f"a trip with {n} segment(s) has stations 0..{n}"
                )

        for i in range(n):
            load = sum(p.board <= i < p.alight for p in self.passengers)
            if load > self.capacity:
                raise ValueError(
                    f"segment {i} already carries {load} existing passenger(s), "
                    f"exceeding the seat capacity of {self.capacity}"
                )
        return self


class SegmentSplit(BaseModel):
    """How one segment's cost was divided."""

    index: int = Field(description="segment index, 0-based, in travel order")
    cost: NonNegativeInt = Field(description="segment cost in cents")
    passengers: List[PassengerId] = Field(description="ids on board during this segment")
    shares: Dict[str, NonNegativeInt] = Field(
        description="cents charged to each on-board passenger; sums to cost"
    )


class SplitResponse(BaseModel):
    total_cost: NonNegativeInt = Field(description="sum of all segment costs in cents")
    totals: Dict[str, NonNegativeInt] = Field(
        description="cents charged to each passenger over the whole trip; sums to total_cost"
    )
    segments: List[SegmentSplit] = Field(description="per-segment split detail")


class AdmitResponse(SplitResponse):
    admitted: List[PassengerId] = Field(
        description="ids of the chosen candidate passengers, ordered in canonical "
                    "passenger-id order (integers first, numerically; strings then, "
                    "lexicographically); empty when no candidate could be admitted"
    )


class Payment(BaseModel):
    """One amount already paid, in cents — exactly one per passenger."""

    model_config = ConfigDict(extra="forbid")

    id: PassengerId = Field(description="id of the paying passenger (string or integer)")
    amount: NonNegativeInt = Field(description="cents already paid by this passenger; must be >= 0")


class SettleRequest(SplitRequest):
    """A split request plus one non-negative payment record per passenger."""

    payments: List[Payment] = Field(
        min_length=0,
        description="one paid amount (integer cents) per passenger, no more and no less",
    )

    @model_validator(mode="after")
    def _check_payments(self) -> "SettleRequest":
        passenger_ids = {str(p.id): p.id for p in self.passengers}
        paid: Dict[str, int] = {}
        for payment in self.payments:
            key = str(payment.id)
            if key in paid:
                raise ValueError(f"duplicate payment id: {payment.id!r}")
            if key not in passenger_ids:
                raise ValueError(
                    f"payment for unknown passenger id: {payment.id!r} "
                    f"(no such passenger in the trip)"
                )
            paid[key] = payment.amount

        missing = [passenger_ids[k] for k in passenger_ids if k not in paid]
        if missing:
            raise ValueError(
                f"missing payment for passenger id(s): "
                f"{', '.join(repr(i) for i in missing)}"
            )

        total_paid = sum(paid.values())
        total_cost = sum(self.segments)
        if total_paid != total_cost:
            raise ValueError(
                f"payments total {total_paid} cents but the trip costs "
                f"{total_cost} cents (paid amounts must cover the trip exactly)"
            )
        return self


class Transfer(BaseModel):
    """One debt-settling payment in cents."""

    model_config = ConfigDict(populate_by_name=True)

    from_: PassengerId = Field(
        alias="from",
        description="id of the passenger who owes money (negative balance)",
    )
    to: PassengerId = Field(
        description="id of the passenger who receives money (positive balance)",
    )
    amount: PositiveInt = Field(description="cents transferred; always > 0")


class SettleResponse(BaseModel):
    owed: Dict[str, NonNegativeInt] = Field(
        description="cents each passenger should pay (same as /split totals)"
    )
    paid: Dict[str, NonNegativeInt] = Field(
        description="cents each passenger actually paid, per the request payments"
    )
    balance: Dict[str, int] = Field(
        description="paid - owed per passenger; negative means the passenger owes money"
    )
    transfers: List[Transfer] = Field(
        description="minimum set of payments that settles every balance, "
                    "ordered by (from, to, amount) in canonical passenger-id order"
    )
