"""Lua nguyên tử ``atomic_smurf_and_risk_eval.lua`` (File 4 §3.2) — fakeredis(Lua) + Redis thật."""

from __future__ import annotations

import math

import pytest
from redis.asyncio import Redis

from app.shield_state.keys import load_lua, shield_state_key, smurf_window_key
from app.shield_state.risk import MINUTE_MS

NOW = 1_795_543_200_000


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


async def _run(
    r: Redis, sid: str, dev: str, now: int, eid: str, synergy: str = "0.0", *extra: str
) -> list[str | int]:
    script = r.register_script(load_lua("atomic_smurf_and_risk_eval.lua"))
    res: list[str | int] = await script(
        keys=[smurf_window_key(sid), shield_state_key(dev)], args=[now, eid, synergy, *extra]
    )
    return res


async def _check_smurf_sequence(r: Redis, sid: str, dev: str) -> None:
    await r.hset(shield_state_key(dev), mapping={"base_log_odds": "-0.7"})
    expected_counts = [1, 2, 3, 4]
    for i, n in enumerate(expected_counts):
        prob, count, total = await _run(r, sid, dev, NOW + i * MINUTE_MS, f"open-{i}")
        assert count == n
        assert float(total) == pytest.approx(-0.7 + 0.9 * (n - 1))
        assert float(prob) == pytest.approx(_sigmoid(-0.7 + 0.9 * (n - 1)), abs=1e-12)
    h = await r.hgetall(shield_state_key(dev))
    assert h["smurf_count"] == "4"
    assert float(h["last_prob"]) == pytest.approx(_sigmoid(-0.7 + 2.7), abs=1e-12)
    pttl = await r.pttl(smurf_window_key(sid))
    assert 0 < pttl <= 15 * MINUTE_MS


async def test_ec08_smurf_window_counts_reopens_fakeredis(redis: Redis) -> None:
    """S2-EC08: mỗi lần quay lại app NH trong 15 phút cộng +0.9."""
    await _check_smurf_sequence(redis, "sess-a", "dev-a")


async def test_ec08_window_slides_after_15_minutes(redis: Redis) -> None:
    await _run(redis, "s", "d", NOW, "o1")
    await _run(redis, "s", "d", NOW + 5 * MINUTE_MS, "o2")
    _, count, _ = await _run(redis, "s", "d", NOW + 20 * MINUTE_MS + 1, "o3")
    assert count == 1  # cả o1, o2 đã trượt khỏi cửa sổ


async def test_same_event_id_is_not_double_counted(redis: Redis) -> None:
    await _run(redis, "s", "d", NOW, "dup")
    _, count, _ = await _run(redis, "s", "d", NOW + 1000, "dup")
    assert count == 1


async def test_default_base_is_prior_and_state_hash_gets_ttl(redis: Redis) -> None:
    prob, count, total = await _run(redis, "s", "fresh", NOW, "o1")
    assert count == 1 and float(total) == pytest.approx(-3.5)
    assert float(prob) == pytest.approx(_sigmoid(-3.5))
    assert 0 < await redis.pttl(shield_state_key("fresh")) <= 24 * 3600 * 1000


async def test_disbursement_in_call_95_7_via_lua(redis: Redis) -> None:
    """Ví dụ spec qua Lua: nền −0.7 (prior + giải ngân) + Ψ 3.8 → 95.7%."""
    prob, _, _ = await _run(redis, "s", "d", NOW, "o1", "3.8", "0.0", "-0.7")
    assert round(float(prob) * 100, 1) == 95.7


async def test_extension_args_proj_and_fresh_base(redis: Redis) -> None:
    await redis.hset(shield_state_key("d"), mapping={"base_log_odds": "-3.5"})
    _, _, total = await _run(redis, "s", "d", NOW, "o1", "0.0", "4.5", "-0.7")
    assert float(total) == pytest.approx(3.8)
    # Lua không ghi đè base_log_odds (trường chỉ do CAS sở hữu — chống TOCTOU).
    assert await redis.hget(shield_state_key("d"), "base_log_odds") == "-3.5"


async def _check_version_guard(r: Redis, sid: str, dev: str) -> None:
    """Nền ARGV[5] tính từ phiên bản 3, nhưng CAS đã ghi phiên bản 4 (nền mới −0.7) chen giữa →
    Lua dùng nền mới trong HASH; phiên bản khớp → dùng ARGV[5]. Không ghi đè base_log_odds."""
    await r.hset(shield_state_key(dev), mapping={"base_log_odds": "-0.7", "version": "4"})
    _, _, stale = await _run(r, sid, dev, NOW, "o1", "0.0", "0.0", "-3.5", "86400000", "3")
    assert float(stale) == pytest.approx(-0.7)
    _, _, fresh = await _run(r, sid + "x", dev, NOW, "o1", "0.0", "0.0", "-3.5", "86400000", "4")
    assert float(fresh) == pytest.approx(-3.5)
    assert await r.hget(shield_state_key(dev), "base_log_odds") == "-0.7"


async def test_toctou_stale_base_ignored_when_version_moved(redis: Redis) -> None:
    await _check_version_guard(redis, "s", "d")


@pytest.mark.redis_real
async def test_ec08_smurf_lua_on_real_redis(real_redis: Redis, uid: str) -> None:
    """DoD: Lua chạy trên Redis 7 thật (CI có service redis)."""
    sid, dev = f"sess-{uid}", f"dev-{uid}"
    sid2, dev2 = f"sess2-{uid}", f"dev2-{uid}"
    try:
        await _check_smurf_sequence(real_redis, sid, dev)
        await _check_version_guard(real_redis, sid2, dev2)  # ARGV[7] stale/fresh trên Redis thật
    finally:
        await real_redis.delete(
            smurf_window_key(sid),
            shield_state_key(dev),
            smurf_window_key(sid2),
            smurf_window_key(sid2 + "x"),
            shield_state_key(dev2),
        )
