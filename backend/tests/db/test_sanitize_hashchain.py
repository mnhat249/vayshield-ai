"""Test thuần Python (không DB): sanitize, validator JSON, hash-chain, runner."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from app.db_schema.hashchain import (
    GENESIS_HASH,
    ChainEntry,
    compute_entry_hash,
    format_ts,
    verify_chain,
)
from app.db_schema.migrate import MIGRATIONS_DIR, checksum, list_migrations, to_asyncpg_dsn
from app.db_schema.sanitize import (
    MaskedExtractionJson,
    json_depth,
    sanitize_json,
    strip_control_chars,
)


class _Doc(BaseModel):
    data: MaskedExtractionJson


def test_ec_db26_strip_null_byte_keeps_newline_tab() -> None:
    assert strip_control_chars("a\x00b\x01c\nd\te\x1f") == "abc\nd\te"
    assert sanitize_json({"k\x00": ["x\x00y", {"n": "\x00"}]}) == {"k": ["xy", {"n": ""}]}


def test_ec_db18_depth_5_ok_depth_6_rejected() -> None:
    ok = {"a": {"b": {"c": {"d": {"e": 1}}}}}  # 5 cấp
    assert json_depth(ok) == 5
    assert _Doc(data=ok).data == ok
    with pytest.raises(ValidationError):
        _Doc(data={"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}})


def test_ec_db18_depth_50_rejected() -> None:
    deep: dict[str, object] = {}
    cur = deep
    for _ in range(50):
        nxt: dict[str, object] = {}
        cur["x"] = nxt
        cur = nxt
    with pytest.raises(ValidationError):
        _Doc(data=deep)


def test_ec_db18_size_64kb_rejected() -> None:
    with pytest.raises(ValidationError):
        _Doc(data={"blob": "x" * 70_000})
    assert _Doc(data={"blob": "x" * 60_000}).data["blob"] == "x" * 60_000


def test_ec_db26_validator_strips_null_in_json() -> None:
    assert _Doc(data={"t": "a\x00b"}).data == {"t": "ab"}


def _chain(n: int) -> list[ChainEntry]:
    out: list[ChainEntry] = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        ts = datetime(2026, 11, 25, 1, 2, 3, 456789 + i, tzinfo=UTC)
        target = UUID(int=i)
        h = compute_entry_hash(prev, "ops1", "UNSEAL_MULE_FOR_SIMO", target, ts)
        out.append(ChainEntry(i, "ops1", "UNSEAL_MULE_FOR_SIMO", target, prev, h, ts))
        prev = h
    return out


def test_hash_chain_continuous_prev_hash() -> None:
    chain = _chain(5)
    assert chain[0].prev_entry_hash == GENESIS_HASH
    for a, b in pairwise(chain):
        assert b.prev_entry_hash == a.entry_hash
    assert verify_chain(chain) is None


def test_ec_db20_tamper_detected_by_verifier() -> None:
    chain = _chain(5)
    chain[2] = replace(chain[2], actor_id="attacker")
    assert verify_chain(chain) == 3
    chain = _chain(5)
    del chain[1]  # xóa 1 mục → đứt liên kết
    assert verify_chain(chain) == 3


def test_format_ts_micro_utc() -> None:
    ts = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert format_ts(ts) == "2026-01-02T03:04:05.000006Z"


def test_runner_lists_migrations_in_order_and_checksum_stable() -> None:
    files = list_migrations()
    assert files and files[0].name == "0001_core_schema.sql"
    assert [f.name for f in files] == sorted(f.name for f in files)
    assert checksum(files[0]) == checksum(files[0])
    assert MIGRATIONS_DIR.is_dir()
    assert to_asyncpg_dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    assert list_migrations(Path(MIGRATIONS_DIR) / "nonexistent") == []


def test_ec_db18_size_measured_like_jsonb_text_many_keys() -> None:
    """Nhiều khóa sát ngưỡng: compact < 64KB nhưng dạng jsonb >= 64KB phải bị từ chối."""
    n = 4000
    while True:
        many = {f"k{i:05d}": 1 for i in range(n)}
        jsonb_len = len(json.dumps(many, separators=(", ", ": ")).encode())
        compact_len = len(json.dumps(many, separators=(",", ":")).encode())
        if jsonb_len >= 65536:
            break
        n += 20
    assert compact_len < 65536 <= jsonb_len
    with pytest.raises(ValidationError):
        _Doc(data=many)
    just_under = {f"k{i:05d}": 1 for i in range(n - 40)}
    assert _Doc(data=just_under).data == just_under


def test_hash_rejects_pipe() -> None:
    with pytest.raises(ValueError):
        compute_entry_hash(GENESIS_HASH, "a|b", "X", UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC))
