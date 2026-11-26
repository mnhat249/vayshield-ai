"""Phát hiện tên/domain nhái tổ chức tín dụng (S2-EC16, S2-EC19 homoglyph).

Thuật toán tất định:

1. Chuẩn hóa: NFKD bỏ dấu tiếng Việt, ``đ→d``, chữ thường, tách token chữ-số.
2. **Khớp chính xác** (``EXACT``): dạng nén (bỏ khoảng trắng) của toàn bộ tên trùng tên ngắn /
   tên pháp nhân / alias của 1 pháp nhân, hoặc host trùng (hay là subdomain của) domain chính thức.
3. Nếu không khớp chính xác với bất kỳ pháp nhân nào → **khớp mờ** trên "lõi thương hiệu" (bỏ từ
   chung chung: công ty, tài chính, vay nhanh, quốc tế…) sau khi quy ký tự đồng hình (Cyrillic,
   ``0→o``, ``1→i``): ``score = (JaroWinkler + Levenshtein chuẩn hóa) / 2``. ``score > 0.78`` (lõi
   >= 4 ký tự, độ dài lệch <= 20%, cùng ký tự đầu) hoặc lõi này chứa trọn lõi kia qua 1 token
   thương hiệu "mạnh" (vd "Tín dụng FE Việt" ⊂ "FE Credit") → ``LOOKALIKE``.

Trước bước 3: ``EXACT_CORE`` nếu lõi thương hiệu (không quy đồng hình) trùng đúng 1 pháp nhân và
phần còn lại chỉ là từ chung chung lành ("Home Credit Việt Nam", "Vietcombank VN"). Hậu tố đáng
ngờ ("Vay Nhanh", "Quốc Tế"…) chặn EXACT_CORE để vẫn bắt tên nhái.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from rapidfuzz.distance import JaroWinkler, Levenshtein

from app.verdict.registry import LenderRecord, LenderRegistry

__all__ = [
    "LOOKALIKE_THRESHOLD",
    "BrandMatch",
    "MatchStatus",
    "brand_core",
    "brand_similarity",
    "detect_lookalike_brand",
    "detect_lookalike_domain",
    "normalize_host",
    "normalize_text",
]

LOOKALIKE_THRESHOLD: Final = 0.78

_TOKEN_RE: Final = re.compile(r"[^\W_]+")

_HOMOGLYPHS: Final = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "к": "k",
        "м": "m",
        "т": "t",
        "н": "h",
        "в": "b",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "0": "o",
        "1": "i",
    }
)

# Từ chung chung "lành": hình thức pháp lý / địa danh / ngành. Tên thật thường thêm các từ này
# ("Home Credit Việt Nam", "Công ty Tài chính Home Credit") → vẫn là EXACT_CORE.
_BENIGN_GENERIC_TOKENS: Final = frozenset(
    {
        "cong", "ty", "tnhh", "mtv", "mot", "thanh", "vien", "trach", "nhiem", "huu", "han",
        "co", "phan", "cp", "tmcp", "thuong", "mai", "jsc", "ltd", "limited", "company", "group",
        "tai", "chinh", "tin", "dung", "tieu", "ngan", "hang", "bank", "finance", "financial",
        "viet", "nam", "vietnam", "vn", "the", "va",
    }
)  # fmt: skip

# Hậu tố "đáng ngờ" hay gặp ở tên nhái ("Shinhan Vay Nhanh", "HomeCredit Quốc Tế"): bị bỏ khi
# lấy lõi thương hiệu nhưng KHÔNG cho phép kết luận EXACT_CORE (S2-EC16).
_SUSPICIOUS_GENERIC_TOKENS: Final = frozenset(
    {
        "vay", "tien", "nhanh", "online", "app", "quoc", "te", "international", "dich", "vu",
        "so", "digital", "24h", "24", "7", "sieu", "toc", "gap", "ho", "tro", "uu", "dai",
    }
)  # fmt: skip

_GENERIC_TOKENS: Final = _BENIGN_GENERIC_TOKENS | _SUSPICIOUS_GENERIC_TOKENS

# Token thương hiệu "yếu" (quá phổ biến): trùng riêng các token này không đủ kết luận nhái.
_WEAK_BRAND_TOKENS: Final = frozenset(
    {
        "credit", "home", "pay", "money", "cash", "lend", "loan", "smart", "easy", "fast",
        "plus", "pro", "shop", "mobile", "card", "fin", "vi",
    }
)  # fmt: skip

_MIN_CORE_LEN: Final = 3
_MIN_FUZZY_LEN: Final = 4
_MIN_LEN_RATIO: Final = 0.8


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    LOOKALIKE = "LOOKALIKE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class BrandMatch:
    status: MatchStatus
    record: LenderRecord | None = None
    score: float = 0.0
    # EXACT_NAME | EXACT_CORE | EXACT_DOMAIN | EXACT_PACKAGE | FUZZY | TOKEN_CONTAINMENT
    method: str | None = None


NO_MATCH: Final = BrandMatch(MatchStatus.NONE)


def normalize_text(text: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, NFKC, gộp khoảng trắng; giữ nguyên chữ số."""
    decomposed = unicodedata.normalize("NFKD", text.replace("Đ", "D").replace("đ", "d"))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = unicodedata.normalize("NFKC", stripped).casefold()
    return " ".join(_TOKEN_RE.findall(lowered))


def _compact(text: str) -> str:
    return normalize_text(text).replace(" ", "")


def _skeleton_tokens(text: str) -> tuple[str, ...]:
    return tuple(normalize_text(text).translate(_HOMOGLYPHS).split())


def brand_core(text: str) -> tuple[str, ...]:
    """Token thương hiệu sau khi quy đồng hình và bỏ từ chung chung."""
    return tuple(t for t in _skeleton_tokens(text) if t not in _GENERIC_TOKENS)


def _raw_core(text: str) -> tuple[str, ...]:
    """Lõi thương hiệu KHÔNG quy đồng hình (để "H0me Credit" không thành EXACT_CORE)."""
    return tuple(t for t in normalize_text(text).split() if t not in _GENERIC_TOKENS)


def _has_suspicious_suffix(text: str) -> bool:
    return any(t in _SUSPICIOUS_GENERIC_TOKENS for t in normalize_text(text).split())


def _score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return (JaroWinkler.normalized_similarity(a, b) + Levenshtein.normalized_similarity(a, b)) / 2


def brand_similarity(a: str, b: str) -> float:
    """Độ tương đồng [0, 1] giữa 2 tên trên lõi thương hiệu (JW + Levenshtein)/2."""
    return _score("".join(brand_core(a)), "".join(brand_core(b)))


def _contains(core_in: tuple[str, ...], core_ref: tuple[str, ...]) -> bool:
    """Lõi này chứa trọn lõi kia và phần chung có ít nhất 1 token thương hiệu "mạnh"."""
    if not core_in or not core_ref:
        return False
    a, b = set(core_in), set(core_ref)
    strong = (a & b) - _WEAK_BRAND_TOKENS
    return (a <= b or b <= a) and len("".join(strong)) >= 2


def _fuzzy_ok(a: str, b: str, threshold: float) -> tuple[bool, float]:
    """Khớp mờ chỉ khi đủ dài, độ dài tương đương và cùng ký tự đầu (tránh "MCredit" → FE
    Credit, "vietcom" → Vietcombank)."""
    score = _score(a, b)
    if min(len(a), len(b)) < _MIN_FUZZY_LEN or a[0] != b[0]:
        return False, score
    if min(len(a), len(b)) / max(len(a), len(b)) < _MIN_LEN_RATIO:
        return False, score
    return score > threshold, score


def _best_fuzzy(
    candidates: Iterable[tuple[LenderRecord, str]], probe: str, threshold: float
) -> BrandMatch:
    core_in = brand_core(probe)
    compact_in = "".join(core_in)
    if len(compact_in) < 2:
        return NO_MATCH
    best = NO_MATCH
    for record, name in candidates:
        core_ref = brand_core(name)
        compact_ref = "".join(core_ref)
        if not compact_ref:
            continue
        ok, score = _fuzzy_ok(compact_in, compact_ref, threshold)
        method = "FUZZY"
        if not ok:
            if not _contains(core_in, core_ref):
                continue
            method = "TOKEN_CONTAINMENT"
        if best.status is MatchStatus.NONE or score > best.score:
            best = BrandMatch(MatchStatus.LOOKALIKE, record, score, method)
    return best


def _exact_core(name: str, records: Sequence[LenderRecord]) -> BrandMatch | None:
    """Lõi thương hiệu trùng hoàn toàn đúng 1 pháp nhân, phần khác chỉ là từ chung chung "lành"
    → ``EXACT_CORE``. Có hậu tố đáng ngờ hoặc lõi trùng nhiều pháp nhân → ``None``."""
    if _has_suspicious_suffix(name):
        return None
    core = "".join(_raw_core(name))
    if len(core) < _MIN_CORE_LEN:
        return None
    hits = {
        r.entity_code: r for r in records for n in r.names if n and "".join(_raw_core(n)) == core
    }
    if len(hits) != 1:
        return None
    return BrandMatch(MatchStatus.EXACT, next(iter(hits.values())), 1.0, "EXACT_CORE")


def detect_lookalike_brand(
    name: str | None, registry: LenderRegistry, threshold: float = LOOKALIKE_THRESHOLD
) -> BrandMatch:
    """So 1 tên (tên thương hiệu hoặc tên pháp nhân) với registry (S2-EC16).

    ``EXACT`` nếu trùng tên chuẩn/alias; ``LOOKALIKE`` nếu gần giống (> ``threshold``) mà không
    trùng; ``NONE`` nếu không liên quan.
    """
    if not name or not name.strip():
        return NO_MATCH
    records = registry.active_records()
    compact = _compact(name)
    for record in records:
        if any(compact == _compact(n) for n in record.names if n):
            return BrandMatch(MatchStatus.EXACT, record, 1.0, "EXACT_NAME")
    core_hit = _exact_core(name, records)
    if core_hit is not None:
        return core_hit
    pairs = [(r, n) for r in records for n in r.names if n]
    return _best_fuzzy(pairs, name, threshold)


def normalize_host(value: str) -> str:
    """``https://www.HomeCredit.vn/abc`` → ``homecredit.vn``."""
    host = value.strip().casefold()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = re.split(r"[/?#:]", host, maxsplit=1)[0]
    host = host.rstrip(".")
    return host.removeprefix("www.")


def _registrable_label(host: str) -> str:
    labels = [p for p in host.split(".") if p]
    if not labels:
        return ""
    # Bỏ đuôi tên miền phổ biến (.vn, .com.vn, .com, .net...).
    suffixes = {"vn", "com", "net", "org", "info", "app", "online", "xyz", "top", "site", "io"}
    while len(labels) > 1 and labels[-1] in suffixes:
        labels.pop()
    return labels[-1]


def detect_lookalike_domain(
    domain: str | None, registry: LenderRegistry, threshold: float = LOOKALIKE_THRESHOLD
) -> BrandMatch:
    """So host với ``official_domains``: trùng/subdomain → ``EXACT``; nhái → ``LOOKALIKE``."""
    if not domain or not domain.strip():
        return NO_MATCH
    host = normalize_host(domain)
    if not host:
        return NO_MATCH
    records = registry.active_records()
    for record in records:
        for official in record.official_domains:
            off = normalize_host(official)
            if off and (host == off or host.endswith("." + off)):
                return BrandMatch(MatchStatus.EXACT, record, 1.0, "EXACT_DOMAIN")
    labels = {"".join(_skeleton_tokens(p)) for p in host.split(".") if p}
    labels.add("".join(_skeleton_tokens(_registrable_label(host))))
    host_compact = "".join(_skeleton_tokens(host))
    best = NO_MATCH
    for record in records:
        for official in record.official_domains:
            ref = "".join(_skeleton_tokens(_registrable_label(normalize_host(official))))
            if len(ref) < 3:
                continue
            results = [_fuzzy_ok(label, ref, threshold) for label in labels if label]
            ok = any(r[0] for r in results)
            score = max((r[1] for r in results), default=0.0)
            method = "FUZZY"
            if not ok:
                # Thương hiệu bị nhét vào subdomain / ghép chữ: "homecredit.vn.vay-nhanh.xyz".
                if len(ref) < 5 or ref not in host_compact:
                    continue
                method = "TOKEN_CONTAINMENT"
            if best.status is MatchStatus.NONE or score > best.score:
                best = BrandMatch(MatchStatus.LOOKALIKE, record, score, method)
    return best
