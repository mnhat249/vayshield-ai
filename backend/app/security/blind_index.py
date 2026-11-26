"""Blind index HMAC-SHA256, pepper độc lập + cửa sổ 2 pepper khi xoay (File 4 §5.1, EC-DB08).

- ``PHONE``: ``HMAC_SHA256(PEPPER_B2B_PHONE, e164)`` — SĐT luôn chuẩn hóa E.164 trước (EC-DB10).
  Đối tác B2B tính cùng công thức (bộ test vector chung), nên KHÔNG thêm tiền tố vào thông điệp.
- ``MULE_ACCOUNT``: ``HMAC_SHA256(PEPPER_MULE_ACCOUNT, "BankBIN:AccountNo")`` đã chuẩn hóa.

Xoay pepper ``v1 → v2``: pepper hiện tại = ``PEPPER_*`` (version ``PEPPER_*_VERSION``), pepper cũ =
``PEPPER_*_PREV`` (version - 1). Tra cứu dùng ``WHERE col IN (:current, :previous)``
(``BlindIndexLookup.candidates``) và nâng cấp lười bản ghi cũ khi truy cập (``needs_rehash``).
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.core.config import Settings, get_settings
from app.security.keys import SecurityConfigError, secret_bytes
from app.security.phone import normalize_phone_e164

_MIN_PEPPER_LEN: Final = 32
_BANK_BIN_RE: Final = re.compile(r"\d{6,8}")
_ACCOUNT_RE: Final = re.compile(r"[0-9A-Z]{4,32}")
_ACCOUNT_SEPARATORS_RE: Final = re.compile(r"[\s.\-]")


class BlindIndexKind(StrEnum):
    PHONE = "PHONE"
    MULE_ACCOUNT = "MULE_ACCOUNT"


class BlindIndexInputError(ValueError):
    """Giá trị đầu vào không chuẩn hóa được. Không chứa giá trị đầu vào."""


def normalize_mule_account(bank_bin: str, account_no: str) -> str:
    """Chuẩn hóa ``BankBIN:AccountNo`` (bỏ khoảng trắng/dấu chấm/gạch, chữ hoa)."""
    bin_clean = bank_bin.strip()
    acct = _ACCOUNT_SEPARATORS_RE.sub("", account_no.strip()).upper()
    if not _BANK_BIN_RE.fullmatch(bin_clean):
        raise BlindIndexInputError("BIN ngân hàng không hợp lệ")
    if not _ACCOUNT_RE.fullmatch(acct):
        raise BlindIndexInputError("số tài khoản không hợp lệ")
    return f"{bin_clean}:{acct}"


def _canonical(kind: BlindIndexKind, value: str) -> str:
    if kind is BlindIndexKind.PHONE:
        return normalize_phone_e164(value)
    if ":" not in value:
        raise BlindIndexInputError("MULE_ACCOUNT phải có dạng BankBIN:AccountNo")
    bank_bin, account_no = value.split(":", 1)
    return normalize_mule_account(bank_bin, account_no)


def _hmac_hex(pepper: bytes, message: str) -> str:
    return hmac.new(pepper, message.encode("utf-8"), "sha256").hexdigest()


@dataclass(frozen=True, slots=True)
class BlindIndexLookup:
    """Kết quả băm: giá trị để GHI (``current``) và danh sách để TRA (``candidates``)."""

    current: str
    current_version: int
    previous: str | None
    previous_version: int | None

    @property
    def candidates(self) -> tuple[str, ...]:
        """Dùng cho ``WHERE col IN (...)`` — hiện tại trước, cũ sau (EC-DB08)."""
        return (self.current,) if self.previous is None else (self.current, self.previous)

    def needs_rehash(self, stored_hmac: str, stored_version: int) -> bool:
        """True nếu bản ghi đang lưu theo pepper cũ → cập nhật sang ``current`` (lazy re-hash)."""
        if stored_version == self.current_version and hmac.compare_digest(
            stored_hmac, self.current
        ):
            return False
        return self.previous is not None and hmac.compare_digest(stored_hmac, self.previous)

    def matches(self, stored_hmac: str) -> bool:
        """So khớp hằng thời gian với mọi pepper đang hiệu lực."""
        ok = hmac.compare_digest(stored_hmac, self.current)
        if self.previous is not None:
            ok = hmac.compare_digest(stored_hmac, self.previous) or ok
        return ok


class BlindIndexer:
    """Giữ pepper trong bộ nhớ; ``repr`` không lộ pepper."""

    __slots__ = ("_peppers",)

    def __init__(self, peppers: dict[BlindIndexKind, tuple[bytes, int, bytes | None]]) -> None:
        for current, version, previous in peppers.values():
            if len(current) < _MIN_PEPPER_LEN or (
                previous is not None and len(previous) < _MIN_PEPPER_LEN
            ):
                raise ValueError("pepper quá ngắn")
            if version < 1:
                raise ValueError("pepper_version phải >= 1")
        self._peppers = dict(peppers)

    def __repr__(self) -> str:
        kinds = ",".join(sorted(k.value for k in self._peppers))
        return f"BlindIndexer(kinds=[{kinds}])"

    __str__ = __repr__

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> BlindIndexer:
        cfg = settings or get_settings()
        table = {
            BlindIndexKind.PHONE: (
                "PEPPER_B2B_PHONE",
                cfg.pepper_b2b_phone,
                cfg.pepper_b2b_phone_prev,
                cfg.pepper_b2b_phone_version,
            ),
            BlindIndexKind.MULE_ACCOUNT: (
                "PEPPER_MULE_ACCOUNT",
                cfg.pepper_mule_account,
                cfg.pepper_mule_account_prev,
                cfg.pepper_mule_account_version,
            ),
        }
        peppers: dict[BlindIndexKind, tuple[bytes, int, bytes | None]] = {}
        for kind, (env, current, previous, version) in table.items():
            if current is None:
                continue
            cur = secret_bytes(current, env, _MIN_PEPPER_LEN)
            prev = (
                secret_bytes(previous, f"{env}_PREV", _MIN_PEPPER_LEN)
                if previous is not None
                else None
            )
            if prev is not None and hmac.compare_digest(prev, cur):
                prev = None
            if prev is not None and version < 2:
                raise SecurityConfigError(f"{env}_VERSION phải >= 2 khi có {env}_PREV")
            peppers[kind] = (cur, version, prev)
        return cls(peppers)

    def lookup(self, kind: BlindIndexKind, value: str) -> BlindIndexLookup:
        entry = self._peppers.get(kind)
        if entry is None:
            raise SecurityConfigError(f"Chưa cấu hình pepper cho {kind.value}")
        current, version, previous = entry
        canonical = _canonical(kind, value)
        return BlindIndexLookup(
            current=_hmac_hex(current, canonical),
            current_version=version,
            previous=_hmac_hex(previous, canonical) if previous is not None else None,
            previous_version=version - 1 if previous is not None else None,
        )

    def index(self, kind: BlindIndexKind, value: str) -> str:
        """Giá trị blind index để GHI (luôn theo pepper hiện tại)."""
        return self.lookup(kind, value).current


def blind_index(kind: BlindIndexKind, value: str, *, settings: Settings | None = None) -> str:
    """HMAC hex 64 ký tự theo pepper hiện tại (dùng khi ghi)."""
    return BlindIndexer.from_settings(settings).index(kind, value)


def blind_index_lookup(
    kind: BlindIndexKind, value: str, *, settings: Settings | None = None
) -> BlindIndexLookup:
    """Mọi HMAC ứng viên để tra cứu trong cửa sổ xoay pepper (EC-DB08)."""
    return BlindIndexer.from_settings(settings).lookup(kind, value)
