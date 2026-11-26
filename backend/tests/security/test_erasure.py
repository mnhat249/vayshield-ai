"""Xóa dữ liệu theo Luật 91/2025 với bia mộ ẩn danh (EC-DB21)."""

from __future__ import annotations

import uuid
from typing import Any

import fakeredis
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.security.erasure import (
    ERASURE_ACTION,
    erase_device,
    erase_device_redis,
    tombstone_id_for,
)

DEV = uuid.UUID("019361a0-0000-7000-8000-0000000000aa")


def test_ec_db21_tombstone_is_unlinkable_and_random() -> None:
    a = tombstone_id_for(DEV)
    b = tombstone_id_for(DEV)
    assert a != b != DEV  # salt ngẫu nhiên bị hủy → không tính lại được từ device_id
    assert a.version == 8
    fixed = tombstone_id_for(DEV, salt=b"\x00" * 32)
    assert fixed == tombstone_id_for(DEV, salt=b"\x00" * 32)
    assert str(DEV) not in str(fixed)


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0][0]

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return [r[0] for r in self._rows]


class FakeConn:
    """Ghi lại thứ tự câu lệnh để kiểm luồng (Postgres thật: test @pg bên dưới)."""

    def __init__(self, *, exists: bool, edges: int, partners: list[str]) -> None:
        self.exists, self.edges, self.partners = exists, edges, partners
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT platform_type"):
            return _Result([("ANDROID_NATIVE",)] if self.exists else [])
        if sql.startswith("SELECT count(*)"):
            return _Result([(self.edges,)])
        if sql.startswith("SELECT id FROM fintech_partners"):
            return _Result([(p,) for p in self.partners])
        return _Result([])

    def index(self, prefix: str) -> int:
        return next(i for i, (s, _) in enumerate(self.calls) if s.startswith(prefix))


async def test_ec_db21_contributions_moved_to_tombstone_before_delete(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.hset(f"vs:shield:state:{DEV}", mapping={"state": "S2"})
    await redis.set(f"vs:idem:notif:{DEV}:abc", "1")
    await redis.set(f"vs:idem:notif:{DEV}:def", "1")
    await redis.set("vs:idem:notif:other-device:abc", "1")
    conn = FakeConn(exists=True, edges=3, partners=["p1", "p2"])
    receipt = await erase_device(conn, redis, DEV)  # type: ignore[arg-type]

    assert receipt.erased
    assert receipt.contributions_preserved == 3
    assert receipt.tombstone_id is not None and receipt.tombstone_id != DEV
    reassign = conn.index("UPDATE mule_incident_edges SET reporter_device_id")
    insert_tomb = conn.index("INSERT INTO anonymous_devices")
    delete = conn.index("DELETE FROM anonymous_devices")
    audit = conn.index("SELECT append_audit_chain")
    assert insert_tomb < reassign < delete < audit
    assert conn.calls[reassign][1]["tomb"] == receipt.tombstone_id
    # audit chain chỉ ghi id bia mộ, không ghi device_id thật
    assert conn.calls[audit][1]["target"] == receipt.tombstone_id
    assert conn.calls[audit][1]["action"] == ERASURE_ACTION
    assert all(str(DEV) not in str(v) for v in conn.calls[audit][1].values())
    # B2B RLS: đặt tenant cho từng partner rồi reset
    partner_sets = [p for s, p in conn.calls if "set_config" in s]
    assert [p["pid"] for p in partner_sets] == ["p1", "p2", ""]
    # Redis
    assert receipt.redis_keys_deleted == 3
    assert await redis.exists("vs:idem:notif:other-device:abc") == 1
    assert await redis.exists(f"vs:shield:state:{DEV}") == 0


async def test_ec_db21_device_without_contributions_no_tombstone_row() -> None:
    conn = FakeConn(exists=True, edges=0, partners=[])
    receipt = await erase_device(conn, None, DEV)  # type: ignore[arg-type]
    assert receipt.erased and receipt.tombstone_id is None
    assert not any(s.startswith("INSERT INTO anonymous_devices") for s, _ in conn.calls)
    assert any(s.startswith("DELETE FROM anonymous_devices") for s, _ in conn.calls)


async def test_ec_db21_unknown_device_is_noop() -> None:
    conn = FakeConn(exists=False, edges=0, partners=[])
    receipt = await erase_device(conn, None, DEV)  # type: ignore[arg-type]
    assert not receipt.erased
    assert len(conn.calls) == 1


async def test_ec_db21_redis_cleanup_idempotent(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await erase_device_redis(redis, DEV) == 0


@pytest.mark.pg
async def test_ec_db21_pg_quorum_preserved_after_erasure(
    pg: AsyncEngine, pg_admin: AsyncEngine
) -> None:
    """PostgreSQL thật: xóa 1 trong 2 thiết bị báo cáo → quorum vẫn 2 phiếu, thiết bị biến mất."""
    dev_a, dev_b, mule = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with pg_admin.begin() as c:
        for d in (dev_a, dev_b):
            await c.execute(
                text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:d, 'WEB_PWA')"),
                {"d": d},
            )
        await c.execute(
            text(
                "INSERT INTO mule_account_nodes (id, bank_bin, account_hmac_sha256,"
                " masked_account_display, account_cipher_envelope)"
                " VALUES (:m, '970436', :h, '****6789', '\\x00')"
            ),
            {"m": mule, "h": uuid.uuid4().hex + uuid.uuid4().hex},
        )
        for d in (dev_a, dev_b):
            await c.execute(
                text(
                    "INSERT INTO mule_incident_edges (mule_node_id, reporter_device_id,"
                    " evidence_channel) VALUES (:m, :d, 'VIETQR_CRYPTO_DECODED')"
                ),
                {"m": mule, "d": d},
            )
    async with pg.begin() as conn:
        receipt = await erase_device(conn, None, dev_a)
    assert receipt.erased and receipt.contributions_preserved == 1
    async with pg.connect() as conn:
        gone = await conn.execute(
            text("SELECT count(*) FROM anonymous_devices WHERE id = :d"), {"d": dev_a}
        )
        assert gone.scalar_one() == 0
        votes = await conn.execute(
            text(
                "SELECT count(DISTINCT reporter_device_id) FROM mule_incident_edges"
                " WHERE mule_node_id = :m"
            ),
            {"m": mule},
        )
        assert votes.scalar_one() == 2
    async with pg_admin.begin() as c:
        await c.execute(text("DELETE FROM mule_account_nodes WHERE id = :m"), {"m": mule})
        await c.execute(
            text("DELETE FROM anonymous_devices WHERE id IN (:b, :t)"),
            {"b": dev_b, "t": receipt.tombstone_id},
        )
