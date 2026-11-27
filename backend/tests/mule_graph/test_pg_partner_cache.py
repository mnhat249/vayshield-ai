"""SqlB2BRepository.get_partner_by_key: kiểm dạng khóa + cache bí mật ngắn (không cần PG thật)."""

from __future__ import annotations

from uuid import uuid4

from app.mule_graph.models import PartnerRecord
from app.mule_graph.repository_pg import SqlB2BRepository
from app.security.envelope import EnvelopeCipher
from tests.security._helpers import KEK_A

GOOD = "vsp_live_abcdef123456"
NONE = "vsp_none_abcdef123456"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _repo(clock: _Clock, loaded: list[str], ttl: float = 120.0) -> SqlB2BRepository:
    repo = SqlB2BRepository(
        None,  # type: ignore[arg-type]
        EnvelopeCipher(KEK_A),
        secret_cache_ttl_s=ttl,
        monotonic=clock,
    )

    async def load(prefix: str) -> PartnerRecord | None:
        loaded.append(prefix)
        return None if prefix.startswith("vsp_none") else PartnerRecord(uuid4(), b"s" * 32)

    repo._load_partner = load  # type: ignore[method-assign]
    return repo


async def test_malformed_partner_key_rejected_without_db() -> None:
    loaded: list[str] = []
    repo = _repo(_Clock(), loaded)
    for bad in ("short", "vsp live key with spaces!!", "x" * 200, "vsp_'; DROP TABLE--xx"):
        assert await repo.get_partner_by_key(bad) is None
    assert loaded == []


async def test_secret_cached_then_expires() -> None:
    clock = _Clock()
    loaded: list[str] = []
    repo = _repo(clock, loaded)
    a = await repo.get_partner_by_key(GOOD)
    b = await repo.get_partner_by_key(GOOD)
    assert a is b and len(loaded) == 1
    clock.t += 121
    await repo.get_partner_by_key(GOOD)
    assert len(loaded) == 2


async def test_unknown_partner_not_cached_and_ttl_zero_disables_cache() -> None:
    clock = _Clock()
    loaded: list[str] = []
    repo = _repo(clock, loaded)
    assert await repo.get_partner_by_key(NONE) is None
    assert await repo.get_partner_by_key(NONE) is None
    assert len(loaded) == 2
    loaded2: list[str] = []
    nocache = _repo(clock, loaded2, ttl=0)
    await nocache.get_partner_by_key(GOOD)
    await nocache.get_partner_by_key(GOOD)
    assert len(loaded2) == 2
