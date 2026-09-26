"""FastAPI application exposing the carpool fare-splitting API."""

from __future__ import annotations

from fastapi import FastAPI

from .models import (
    SegmentSplit,
    SettleRequest,
    SettleResponse,
    SplitRequest,
    SplitResponse,
    Transfer,
)
from .splitter import settle_balances, split_fares

app = FastAPI(
    title="Carpool Fare Splitter",
    version="1.0.0",
    summary="Split a carpool trip's per-segment costs fairly between passengers.",
)


@app.get("/", include_in_schema=False)
def root():
    return {"service": "carpool-fare-splitter", "docs": "/docs", "health": "/healthz"}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.post(
    "/split",
    response_model=SplitResponse,
    responses={422: {"description": "Invalid trip (bad interval, out-of-range "
                                     "station, duplicate id, empty-load charged "
                                     "segment, malformed payload, ...)"}},
)
def split(request: SplitRequest) -> SplitResponse:
    totals, details = split_fares(request.segments, request.passengers)
    return SplitResponse(
        total_cost=sum(request.segments),
        totals={str(pid): amount for pid, amount in totals.items()},
        segments=[
            SegmentSplit(
                index=d["index"],
                cost=d["cost"],
                passengers=d["passengers"],
                shares={str(pid): amount for pid, amount in d["shares"].items()},
            )
            for d in details
        ],
    )


@app.post(
    "/settle",
    response_model=SettleResponse,
    responses={422: {"description": "Invalid trip (bad interval, out-of-range "
                                     "station, duplicate id, empty-load charged "
                                     "segment, malformed payload), or invalid "
                                     "payments (missing/duplicate/unknown id, "
                                     "negative or float amount, totals that do "
                                     "not match the trip cost, ...)"}},
)
def settle(request: SettleRequest) -> SettleResponse:
    owed, _ = split_fares(request.segments, request.passengers)
    paid_by_str = {str(p.id): p.amount for p in request.payments}
    balances = {
        pid: paid_by_str[str(pid)] - amount for pid, amount in owed.items()
    }
    transfers = settle_balances(balances)
    return SettleResponse(
        owed={str(pid): amount for pid, amount in owed.items()},
        paid={str(pid): paid_by_str[str(pid)] for pid in owed},
        balance={str(pid): balances[pid] for pid in owed},
        transfers=[
            Transfer(**{"from": frm, "to": to, "amount": amount})
            for frm, to, amount in transfers
        ],
    )
