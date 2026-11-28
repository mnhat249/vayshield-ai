"""Upload an toàn, tác vụ CPU ngoài event loop, che PII, facts (EC-DB05/06/18/26, EC-OPS12/13)."""

from __future__ import annotations

import tempfile
import threading
from typing import Any

import httpx
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.clock import FakeClock
from app.core.errors import AppError
from app.services.audit_pipeline import ingest as ingest_mod
from app.services.audit_pipeline.facts import (
    LoanFacts,
    is_personal_account_holder,
    parse_stated_annual_rate,
)
from app.services.audit_pipeline.ingest import CpuPool, ingest_images, read_uploads
from app.services.audit_pipeline.masking import mask_account, mask_pii
from tests.ai_pipeline.vision.helpers import png_with_declared_size

from ._helpers import STREAM, image_part, jpeg, meta, signed_multipart

ALLOWED = frozenset({"image/jpeg", "image/webp"})


def _upload(data: bytes, mime: str = "image/jpeg") -> UploadFile:
    spool = tempfile.SpooledTemporaryFile(max_size=16)  # noqa: SIM115 - mô phỏng spool đĩa
    spool.write(data)
    spool.seek(0)
    return UploadFile(
        file=spool,  # type: ignore[arg-type]
        filename="x.jpg",
        headers=Headers({"content-type": mime}),
    )


async def test_ec_ops13_uploads_closed_on_success_and_on_error() -> None:
    ok = [_upload(b"abc"), _upload(b"def")]
    out = await read_uploads(ok, allowed=ALLOWED, max_bytes=10, field="body.images")
    assert out == [b"abc", b"def"]
    assert all(u.file.closed for u in ok)

    big = [_upload(b"x" * 11), _upload(b"y")]
    with pytest.raises(AppError) as exc:
        await read_uploads(big, allowed=ALLOWED, max_bytes=10, field="body.images")
    assert exc.value.status_code == 413
    assert all(u.file.closed for u in big)  # kể cả tệp chưa kịp đọc

    wrong = [_upload(b"<svg/>", "image/svg+xml")]
    with pytest.raises(AppError) as exc2:
        await read_uploads(wrong, allowed=ALLOWED, max_bytes=10, field="body.images")
    assert exc2.value.status_code == 415 and wrong[0].file.closed


async def test_ec_ops12_image_work_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = ingest_mod._clean_one

    def spy(raw: bytes) -> Any:
        seen.append(threading.get_ident())
        return real(raw)

    monkeypatch.setattr(ingest_mod, "_clean_one", spy)
    await ingest_images([jpeg(), jpeg()], CpuPool(2))
    assert seen and all(t != threading.get_ident() for t in seen)


async def test_ec_db05_pixel_bomb_and_corrupt_image_are_http_errors(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    bomb = png_with_declared_size(20_000, 20_000)
    # Content-Type khai JPEG nhưng nội dung PNG bom: magic-byte/pixel guard vẫn chặn (EC-DB05/06).
    r = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(bomb)],
        data={"metadata_json": meta()},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    fake_clock.advance(1)
    corrupt = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(b"\xff\xd8\xff" + b"\x00" * 64)],
        data={"metadata_json": meta()},
    )
    assert corrupt.status_code == 422
    assert corrupt.headers["content-type"].startswith("application/json")
    fake_clock.advance(1)
    html = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(b"<html><script>alert(1)</script></html>")],
        data={"metadata_json": meta()},
    )
    assert html.status_code == 415  # magic byte không phải ảnh (EC-DB06)


async def test_oversize_single_file_is_413(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    limit = env.services.settings.upload_max_image_bytes
    r = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(b"\xff\xd8\xff" + b"\x00" * limit)],
        data={"metadata_json": meta()},
    )
    assert r.status_code == 413


def test_masking_rules() -> None:
    assert mask_pii("gọi 0909123456 hoặc +84 909 123 456") == "gọi [SĐT] hoặc [SĐT]"
    assert mask_pii("mã OTP là 482913") == "mã OTP là [OTP]"
    assert mask_pii("STK 1903123498899 nhé") == "STK ***899 nhé"
    assert mask_pii("a@b.com") == "[EMAIL]"
    assert "20.000.000" in mask_pii("vay 20.000.000 đồng")
    assert "20000000000 đồng" in mask_pii("trả 20000000000 đồng")  # số tiền không bị che
    assert mask_pii("x\x00y\x07z") == "xyz"
    assert mask_account("1903123498899") == "1903****8899"
    assert mask_account("12345") == "***345"


def test_stated_rate_parser_is_deterministic_and_conservative() -> None:
    assert parse_stated_annual_rate("Lãi suất ưu đãi 1,2%/tháng") == pytest.approx(0.144)
    assert parse_stated_annual_rate("12% / năm") == pytest.approx(0.12)
    assert parse_stated_annual_rate("0.1% mỗi ngày") == pytest.approx(0.365)
    assert parse_stated_annual_rate("lãi thấp") is None
    assert parse_stated_annual_rate(None) is None
    assert parse_stated_annual_rate("5000%/tháng") is None  # đọc sai → không dùng


def test_personal_account_heuristic() -> None:
    assert is_personal_account_holder("NGUYEN VAN B") is True
    assert is_personal_account_holder("CÔNG TY TNHH ABC") is False
    assert is_personal_account_holder("Ngân hàng X") is False
    assert is_personal_account_holder(None) is None


def test_ec_db18_facts_json_is_shallow_and_bounded() -> None:
    from app.db_schema.sanitize import validate_masked_extraction
    from app.services.audit_pipeline.facts import FeeFact, RegimeFact

    facts = LoanFacts(
        fees=tuple(FeeFact(f"phí {i}", 1000 + i, 2000, True) for i in range(100)),
        regimes=tuple(RegimeFact(1, 1, 1000) for _ in range(50)),
        notes=tuple("ghi chú " * 100 for _ in range(50)),
    )
    blob = facts.to_json()
    validate_masked_extraction(blob)  # nông ≤ 5, < 64KB
    assert len(blob["terms"]["fees"]) <= 20 and len(blob["notes"]) <= 10
    assert LoanFacts.from_json(blob).fees[0].label == "phí 0"
