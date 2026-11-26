"""Phát hiện tên/domain nhái tổ chức tín dụng (S2-EC16, S2-EC19 homoglyph).

LOOKALIKE chỉ là TÍN HIỆU; tự nó không kéo phán quyết lên ĐỎ (xem ``matrix._spoof_signals``).
Mọi thay đổi ở module này phải giữ xanh bộ hồi quy ``tests/verdict/lookalike_cases.py``.

Thuật toán tất định trên token (bỏ dấu tiếng Việt, NFKC, bỏ ký tự vô hình):

Mỗi tên của pháp nhân (tên ngắn, alias, tên pháp nhân nếu có) sinh 2 khóa: dạng nén đầy đủ
(``"MB Bank"`` → ``mbbank``) và dạng nén lõi (bỏ từ lành: ngân hàng, tài chính, công ty, Việt
Nam… → ``mb``).

1. ``EXACT_NAME``: dạng nén của tên dò = khóa đầy đủ ("Viet com bank" → Vietcombank).
2. ``EXACT_CORE``: lõi tên dò trùng lõi một tên (không phân biệt thứ tự: "Credit FE"), chỉ khi
   duy nhất 1 pháp nhân. Pháp nhân là FinCo: không nhận nếu tên dò có "ngân hàng"/"bank"; nếu
   tên ngắn FinCo chứa từ tài chính bị bỏ (Shinhan Finance, Toyota Finance…) thì tên dò phải có
   "tài chính"/"finance" ("Toyota Việt Nam" không phải Toyota Finance).
3. Chứa khóa (>= 3 ký tự) khớp TRỌN ranh giới token ở cả 2 đầu (đuôi số được phép:
   "HomeCredit88") và còn token lạ → ``LOOKALIKE`` ("Home Credit Official", "Viet Credit Pro",
   "MBBank 24h"). Không nhái: "Lotteria", "Acbuy", "Bidvest" (không trọn token); chuỗi có hình
   thức pháp nhân (công ty, TNHH, TMCP…) mà registry không có tên pháp nhân để đối chiếu → ``NONE``;
   lõi là thương hiệu tập đoàn (Toyota, Lotte, Mirae Asset…) chỉ nhái khi token lạ là từ vay /
   app / số ("Shinhan Vay Nhanh"), không phải "Toyota Motor", "Mirae Asset Securities".
4. Khớp mờ lõi (>= 5 ký tự, cùng ký tự đầu): JW + Levenshtein chuẩn hóa / 2 > 0.78 VÀ khoảng
   cách sửa <= 1 (<= 2 nếu khóa >= 10 ký tự); khóa <= 6 ký tự chỉ nhận thêm/bớt 1 ký tự
   ("TPBankk"), không nhận thay ký tự ("VNBank" ≠ VPBank).
5. Không khớp → chạy lại 1–4 trên dạng quy đồng hình (Cyrillic, 0→o, 1/l→i, 3→e, 4→a, 5→s); khớp
   → ``LOOKALIKE`` ("H0me Credit", "B1DV", "Shlnhan"). Tên chứa ký tự vô hình mà khớp EXACT →
   ``LOOKALIKE`` (che giấu có chủ đích).

Khóa lõi < 3 ký tự ("mb", "fe") chỉ dùng cho so khớp nguyên vẹn (bước 1–2), không bao giờ cho
bước 3 ("MB Shinsei", "HD Bank", "FE Pay" không bị gán nhái).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Final

from rapidfuzz.distance import JaroWinkler, Levenshtein

from app.core.schemas.common import LegalCategory
from app.verdict.registry import LenderRecord, LenderRegistry

__all__ = [
    "LOOKALIKE_THRESHOLD",
    "BrandMatch",
    "MatchStatus",
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
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k",
        "м": "m", "т": "t", "н": "h", "в": "b", "і": "i", "ј": "j", "ѕ": "s",
        "0": "o", "1": "i", "l": "i", "3": "e", "4": "a", "5": "s",
    }
)  # fmt: skip

# Từ chung chung "lành": hình thức pháp lý / ngành / địa danh.
_BENIGN: Final = frozenset(
    {
        "cong", "ty", "tnhh", "mtv", "mot", "thanh", "vien", "trach", "nhiem", "huu", "han",
        "co", "phan", "cp", "tmcp", "tm", "jsc", "ltd", "limited", "company", "group",
        "tai", "chinh", "tin", "dung", "tieu", "ngan", "hang", "bank", "finance", "financial",
        "services", "service", "viet", "nam", "vietnam", "vn", "the", "va",
    }
)  # fmt: skip

# Dấu hiệu "chuỗi là tên pháp nhân" (không có trong registry → không đủ căn cứ nói là nhái).
_LEGAL_FORM: Final = frozenset({"tnhh", "mtv", "tmcp", "jsc", "ltd", "limited"})

# Từ vay / app / quảng cáo hay ghép vào thương hiệu tập đoàn để nhái.
_LENDING_WORDS: Final = frozenset(
    {
        "vay", "tien", "nhanh", "online", "app", "official", "pro", "plus", "care", "ho", "tro",
        "hotro", "quoc", "te", "express", "support", "cskh", "credit",
        "loan", "cash", "money", "pay", "digital", "so", "mobile", "vip", "uu", "dai", "sieu",
        "toc", "gap", "24h", "giai", "ngan",
    }
)  # fmt: skip

_PHRASES: Final = (
    ("thuong mai co phan", "tmcp"),
    ("thuong mai", "tm"),
    ("cong thuong", "congthuong"),  # "Công Thương" là tên riêng
    ("tp ho chi minh", "thanh pho ho chi minh"),
    ("tp hcm", "thanh pho ho chi minh"),
    ("tphcm", "thanh pho ho chi minh"),
)

_MIN_KEY_LEN: Final = 3
_MIN_FUZZY_LEN: Final = 5


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    LOOKALIKE = "LOOKALIKE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class BrandMatch:
    status: MatchStatus
    record: LenderRecord | None = None
    score: float = 0.0
    # EXACT_NAME | EXACT_CORE | EXACT_DOMAIN | EXACT_PACKAGE | CORE_WITH_FOREIGN_TOKENS | FUZZY |
    # HOMOGLYPH | INVISIBLE_CHARS | TOKEN_CONTAINMENT (domain)
    method: str | None = None
    matched_name: str | None = None  # tên chuẩn của registry đã khớp (để biết có phải legal name)


NO_MATCH: Final = BrandMatch(MatchStatus.NONE)


def _strip_invisible(text: str) -> tuple[str, bool]:
    kept = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return kept, kept != text


def normalize_text(text: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, NFKC, bỏ ký tự vô hình, gộp khoảng trắng; giữ chữ số."""
    text, _ = _strip_invisible(text)
    decomposed = unicodedata.normalize("NFKD", text.replace("Đ", "D").replace("đ", "d"))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = unicodedata.normalize("NFKC", stripped).casefold()
    return " ".join(_TOKEN_RE.findall(lowered))


def _expand(tokens: Sequence[str]) -> tuple[str, ...]:
    text = " " + " ".join(tokens) + " "
    for src, dst in _PHRASES:
        text = text.replace(f" {src} ", f" {dst} ")
    return tuple(text.split())


def _tokens(text: str, *, skeleton: bool) -> tuple[str, ...]:
    base = normalize_text(text)
    if skeleton:
        base = base.translate(_HOMOGLYPHS)
    return _expand(base.split())


def _core(tokens: Sequence[str]) -> tuple[str, ...]:
    return tuple(t for t in tokens if t not in _BENIGN)


def _has_bigram(tokens: Sequence[str], a: str, b: str) -> bool:
    return any(x == a and y == b for x, y in pairwise(tokens))


def _bank_marker(tokens: Sequence[str]) -> bool:
    return "bank" in tokens or _has_bigram(tokens, "ngan", "hang")


def _finance_marker(tokens: Sequence[str]) -> bool:
    return "finance" in tokens or "financial" in tokens or _has_bigram(tokens, "tai", "chinh")


def _legal_form(tokens: Sequence[str]) -> bool:
    return any(t in _LEGAL_FORM for t in tokens) or _has_bigram(tokens, "cong", "ty")


def _score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return (JaroWinkler.normalized_similarity(a, b) + Levenshtein.normalized_similarity(a, b)) / 2


def brand_similarity(a: str, b: str) -> float:
    """Độ tương đồng [0, 1] giữa lõi thương hiệu của 2 tên: (JW + Levenshtein)/2."""
    ca = "".join(_core(_tokens(a, skeleton=True)))
    cb = "".join(_core(_tokens(b, skeleton=True)))
    return _score(ca, cb)


def _fuzzy_ok(probe: str, ref: str, threshold: float) -> tuple[bool, float]:
    score = _score(probe, ref)
    if min(len(probe), len(ref)) < _MIN_FUZZY_LEN or probe[0] != ref[0]:
        return False, score
    dist = Levenshtein.distance(probe, ref)
    if dist != 1:  # đúng 1 lỗi gõ; 2 lỗi trở lên dễ trùng tên ngân hàng khác (Vietbank)
        return False, score
    if len(ref) <= 6 and len(probe) == len(ref):
        return False, score  # khóa ngắn: thay 1 ký tự dễ ra tên khác hợp lệ (VNBank / VPBank)
    return score > threshold, score


@dataclass(frozen=True, slots=True)
class _Key:
    record: LenderRecord
    name: str
    full: tuple[str, ...]
    core: tuple[str, ...]

    @property
    def full_key(self) -> str:
        return "".join(self.full)

    @property
    def core_key(self) -> str:
        return "".join(self.core)


def _needs_finance_marker(record: LenderRecord, *, skeleton: bool) -> bool:
    """FinCo có tên ngắn chứa từ tài chính bị bỏ khi lấy lõi (lõi = thương hiệu tập đoàn)."""
    if record.legal_category is not LegalCategory.LICENSED_FINCO:
        return False
    toks = _tokens(record.official_short_name, skeleton=skeleton)
    return _core(toks) != toks


def _keys(records: Sequence[LenderRecord], *, skeleton: bool) -> list[_Key]:
    out: list[_Key] = []
    for r in records:
        for n in r.names:
            if n and n.strip():
                toks = _tokens(n, skeleton=skeleton)
                out.append(_Key(r, n, toks, _core(toks)))
    return out


def _span(key: str, tokens: Sequence[str]) -> tuple[int, int, str] | None:
    """Tìm ``key`` khớp trọn ranh giới token: trả (token đầu, token cuối, phần số dư)."""
    starts: list[int] = []
    pos = 0
    for t in tokens:
        starts.append(pos)
        pos += len(t)
    hay = "".join(tokens)
    for i, start in enumerate(starts):
        if not hay.startswith(key, start):
            continue
        end = start + len(key)
        for j in range(i, len(tokens)):
            tok_end = starts[j] + len(tokens[j])
            if tok_end == end:
                return i, j, ""
            if tok_end > end:
                rest = hay[end:tok_end]
                if rest.isdigit():
                    return i, j, rest
                break
    return None


def _match_tokens(
    tokens: tuple[str, ...], keys: Sequence[_Key], threshold: float, *, skeleton: bool
) -> BrandMatch:
    if not tokens:
        return NO_MATCH
    compact = "".join(tokens)
    core = _core(tokens)
    bank = _bank_marker(tokens)
    finance = _finance_marker(tokens)

    # 1. Tên đầy đủ trùng khóa đầy đủ.
    for k in keys:
        if compact == k.full_key:
            return BrandMatch(MatchStatus.EXACT, k.record, 1.0, "EXACT_NAME", k.name)

    vocabs: dict[str, set[str]] = {}
    for k in keys:
        vocabs.setdefault(k.record.entity_code, set()).update((*k.full, k.full_key, k.core_key))

    # 2. Lõi trùng lõi (không phân biệt thứ tự), hoặc lõi tên dò chỉ gồm từ của chính pháp nhân
    # và phủ trọn lõi của 1 tên ("Ngân hàng Quốc tế VIB" khi registry có tên pháp nhân).
    if core:
        exact: dict[str, _Key] = {}
        for k in keys:
            if not k.core:
                continue
            same = sorted(core) == sorted(k.core) or "".join(core) == k.core_key
            covered = (
                len(k.core_key) >= _MIN_KEY_LEN
                and set(k.core) <= set(core)
                and set(core) <= vocabs[k.record.entity_code]
            )
            if not (same or covered):
                continue
            if k.record.legal_category is LegalCategory.LICENSED_FINCO and bank:
                continue
            if _needs_finance_marker(k.record, skeleton=skeleton) and not finance:
                continue
            exact.setdefault(k.record.entity_code, k)
        if len(exact) == 1:
            k = next(iter(exact.values()))
            return BrandMatch(MatchStatus.EXACT, k.record, 1.0, "EXACT_CORE", k.name)

    # 3. Chứa trọn 1 khóa + token lạ (token có trong chính tên của pháp nhân không tính là lạ).
    best: tuple[int, _Key, bool] | None = None
    for k in keys:
        vocab = vocabs[k.record.entity_code]
        for key, is_core in ((k.full_key, False), (k.core_key, True)):
            if len(key) < _MIN_KEY_LEN:
                continue
            hit = _span(key, tokens)
            if hit is None:
                continue
            i, j, digits = hit
            extras = [t for t in (*tokens[:i], *tokens[j + 1 :]) if t not in _BENIGN]
            extras = [t for t in extras if t not in vocab]
            if digits:
                extras.append(digits)
            if not extras or _legal_form(tokens):
                continue
            if (
                not _finance_marker(k.core if is_core else k.full)
                and _needs_finance_marker(k.record, skeleton=skeleton)
                and not finance
                and not any(t in _LENDING_WORDS or any(c.isdigit() for c in t) for t in extras)
            ):
                continue  # "Toyota Motor", "Mirae Asset Securities": công ty khác cùng tập đoàn
            if best is None or len(key) > best[0]:
                best = (len(key), k, True)
    if best is not None:
        k = best[1]
        return BrandMatch(MatchStatus.LOOKALIKE, k.record, 1.0, "CORE_WITH_FOREIGN_TOKENS", k.name)

    # 4. Khớp mờ trên lõi.
    probe = "".join(core)
    fuzzy = NO_MATCH
    if len(probe) >= _MIN_FUZZY_LEN:
        for k in keys:
            ok, score = _fuzzy_ok(probe, k.core_key, threshold)
            if ok and (fuzzy.status is MatchStatus.NONE or score > fuzzy.score):
                fuzzy = BrandMatch(MatchStatus.LOOKALIKE, k.record, score, "FUZZY", k.name)
    return fuzzy


def detect_lookalike_brand(
    name: str | None, registry: LenderRegistry, threshold: float = LOOKALIKE_THRESHOLD
) -> BrandMatch:
    """So 1 tên (tên thương hiệu hoặc tên pháp nhân) với registry (S2-EC16). Xem docstring."""
    if not name or not name.strip():
        return NO_MATCH
    records = registry.active_records()
    _, had_invisible = _strip_invisible(name)
    raw = _match_tokens(
        _tokens(name, skeleton=False), _keys(records, skeleton=False), threshold, skeleton=False
    )
    if raw.status is MatchStatus.EXACT and had_invisible:
        return BrandMatch(
            MatchStatus.LOOKALIKE, raw.record, 1.0, "INVISIBLE_CHARS", raw.matched_name
        )
    if raw.status is not MatchStatus.NONE:
        return raw
    skel = _match_tokens(
        _tokens(name, skeleton=True), _keys(records, skeleton=True), threshold, skeleton=True
    )
    if skel.status is MatchStatus.NONE:
        return NO_MATCH
    return BrandMatch(
        MatchStatus.LOOKALIKE, skel.record, skel.score, "HOMOGLYPH", skel.matched_name
    )


def normalize_host(value: str) -> str:
    """``https://www.HomeCredit.vn/abc`` → ``homecredit.vn``."""
    host, _ = _strip_invisible(value.strip().casefold())
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = re.split(r"[/?#:]", host, maxsplit=1)[0]
    host = host.rstrip(".")
    return host.removeprefix("www.")


def _registrable_label(host: str) -> str:
    labels = [p for p in host.split(".") if p]
    if not labels:
        return ""
    suffixes = {"vn", "com", "net", "org", "info", "app", "online", "xyz", "top", "site", "io"}
    while len(labels) > 1 and labels[-1] in suffixes:
        labels.pop()
    return labels[-1]


def _skel(text: str) -> str:
    return "".join(_tokens(text, skeleton=True))


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
                return BrandMatch(MatchStatus.EXACT, record, 1.0, "EXACT_DOMAIN", official)
    labels = {_skel(p) for p in host.split(".") if p}
    labels.add(_skel(_registrable_label(host)))
    host_tokens = tuple(t for p in host.split(".") for t in _tokens(p, skeleton=True))
    best = NO_MATCH
    for record in records:
        for official in record.official_domains:
            ref = _skel(_registrable_label(normalize_host(official)))
            if len(ref) < _MIN_FUZZY_LEN:
                continue
            results = [_fuzzy_ok(label, ref, threshold) for label in labels if label]
            ok = any(r[0] for r in results)
            score = max((r[1] for r in results), default=0.0)
            method = "FUZZY"
            if not ok:
                # Thương hiệu nhét vào subdomain / ghép chữ: "homecredit.vn.vay-nhanh.xyz".
                if not any(_span(ref, (t,)) or t.startswith(ref) for t in host_tokens):
                    continue
                method = "TOKEN_CONTAINMENT"
            if best.status is MatchStatus.NONE or score > best.score:
                best = BrandMatch(MatchStatus.LOOKALIKE, record, score, method, official)
    return best
