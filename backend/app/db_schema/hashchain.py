"""Hash-chain Python khớp hàm SQL ``audit_chain_entry_hash`` (EC-DB20)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class ChainEntry:
    seq_no: int
    actor_id: str
    action_type: str
    target_ref_id: UUID
    prev_entry_hash: str
    entry_hash: str
    created_at: datetime


def format_ts(ts: datetime) -> str:
    """UTC ISO với 6 chữ số micro, hậu tố Z (khớp ``to_char`` ở SQL)."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_entry_hash(
    prev_hash: str, actor_id: str, action_type: str, target_ref_id: UUID, created_at: datetime
) -> str:
    if "|" in actor_id or "|" in action_type:
        raise ValueError("actor_id/action_type không được chứa '|'")
    payload = "|".join(
        [prev_hash, actor_id, action_type, str(target_ref_id), format_ts(created_at)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(entries: Iterable[ChainEntry], *, prev_hash: str = GENESIS_HASH) -> int | None:
    """Trả ``seq_no`` đầu tiên bị đứt/giả mạo, hoặc ``None`` nếu chuỗi toàn vẹn."""
    expected_prev = prev_hash
    for e in sorted(entries, key=lambda x: x.seq_no):
        good = compute_entry_hash(
            e.prev_entry_hash, e.actor_id, e.action_type, e.target_ref_id, e.created_at
        )
        if e.prev_entry_hash != expected_prev or e.entry_hash != good:
            return e.seq_no
        expected_prev = e.entry_hash
    return None
