"""Fixture cho test ma trận phán quyết (registry GIẢ, không phải seed T08)."""

from __future__ import annotations

import pytest

from app.verdict import (
    AprInterval,
    InMemoryLenderRegistry,
    LenderMatch,
    Provenance,
    VerdictInput,
    VerdictObject,
    decide_verdict,
    match_lender,
)
from tests.verdict._helpers import ALL_RECORDS, Decide


@pytest.fixture
def registry() -> InMemoryLenderRegistry:
    return InMemoryLenderRegistry(ALL_RECORDS)


@pytest.fixture
def decide(registry: InMemoryLenderRegistry) -> Decide:
    """``decide(inp, interval, provenance=None)`` → match qua registry rồi ``decide_verdict``."""

    def _run(
        inp: VerdictInput,
        interval: AprInterval,
        provenance: Provenance | None = None,
        match: LenderMatch | None = None,
    ) -> VerdictObject:
        prov = provenance or Provenance()
        m = match if match is not None else match_lender(inp, prov, registry)
        return decide_verdict(inp, interval, m, prov)

    return _run
