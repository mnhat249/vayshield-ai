"""Cấp cứu Giờ Vàng S9 (File 2 S2-EC48, File 4 §4.1): tra BIN -> hotline phong tỏa + đơn trình báo.

Hàm thuần theo ``now`` truyền vào; không I/O, không log dữ liệu người dùng. Số tài khoản mồi do
client gửi luôn bị che lại phía server (Zero-PII) và chỉ giữ ký tự ``[A-Za-z0-9*]``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Final
from uuid import UUID

from app.core.ids import new_uuid7
from app.core.schemas.common import AmountBucketTier
from app.core.schemas.triage import (
    BankFreezeHotline,
    GoldenHourDossierRequest,
    GoldenHourDossierResponse,
    ScamChannel,
)
from app.triage.hotlines import BankHotline, BankHotlineRegistry

GOLDEN_HOUR_MINUTES: Final = 60
POLICE_HOTLINE: Final = "113"  # số khẩn cấp công an toàn quốc (thông tin công khai)
VN_TZ: Final = timezone(timedelta(hours=7))
MAX_MINUTES: Final = 10 * 365 * 24 * 60
_UNSAFE_RE: Final = re.compile(r"[^A-Za-z0-9]")
FUTURE_SKEW_S: Final = 5 * 60  # dung sai lệch đồng hồ thiết bị; xa hơn = mốc giờ không đáng tin

_CHANNEL_VI: Final[dict[ScamChannel, str]] = {
    ScamChannel.PHONE_CALL: "cuộc gọi điện thoại",
    ScamChannel.ZALO_OR_OTT: "ứng dụng nhắn tin (Zalo/OTT)",
    ScamChannel.SMS: "tin nhắn SMS",
    ScamChannel.FAKE_LOAN_APP: "ứng dụng vay tiền giả mạo",
    ScamChannel.SOCIAL_MEDIA: "mạng xã hội",
    ScamChannel.OTHER: "kênh khác",
}
_TIER_VI: Final[dict[AmountBucketTier, str]] = {
    AmountBucketTier.TIER_UNDER_3M: "dưới 3 triệu đồng",
    AmountBucketTier.TIER_3M_10M: "từ 3 đến 10 triệu đồng",
    AmountBucketTier.TIER_10M_30M: "từ 10 đến 30 triệu đồng",
    AmountBucketTier.TIER_30M_100M: "từ 30 đến 100 triệu đồng",
    AmountBucketTier.TIER_OVER_100M: "trên 100 triệu đồng",
}


def mask_untrusted_account(raw: str | None) -> str | None:
    """Che số tài khoản do client gửi: LUÔN che lại (kể cả chuỗi có ``*``, vì client có thể giấu số
    thật giữa các dấu ``*``), chỉ để lộ 3 ký tự cuối.
    Trả ``None`` nếu rỗng."""
    if raw is None:
        return None
    clean = _UNSAFE_RE.sub("", raw)
    if not clean:
        return None
    return "***" + clean[-3:]


def _aware(ts: datetime) -> datetime:
    """Giả định an toàn: thời điểm không kèm múi giờ được hiểu là giờ Việt Nam (UTC+7)."""
    return ts.replace(tzinfo=VN_TZ) if ts.tzinfo is None else ts


def _hotline_entry(registry: BankHotlineRegistry, bank_bin: str | None) -> BankFreezeHotline:
    found: BankHotline | None = registry.lookup(bank_bin) if bank_bin else None
    if found is not None and found.verified and found.hotline:
        # Chỉ hiển thị số đã được người kiểm chứng; mục chưa verified bị coi như chưa có số.
        return BankFreezeHotline(
            bank_bin=found.bank_bin,
            bank_short_name=found.short_name,
            hotline=found.hotline,
            verified=True,
        )
    name = (
        f"Ngân hàng mã BIN {bank_bin} (chưa có tổng đài đã kiểm chứng)"
        if bank_bin
        else "Ngân hàng của bạn (chưa rõ mã BIN)"
    )
    return BankFreezeHotline(bank_bin=bank_bin, bank_short_name=name, hotline="", verified=False)


def _steps(remaining: int, has_missing_hotline: bool, future_time: bool = False) -> list[str]:
    steps = [
        "Gọi ngay tổng đài ngân hàng của bạn để yêu cầu tạm khóa/tra soát giao dịch vừa chuyển.",
        "Không chuyển thêm bất kỳ khoản nào để 'lấy lại tiền' hay 'giải tỏa': đó là chiêu lừa "
        "đòi thêm tiền.",
        "Giữ nguyên tin nhắn, biên lai chuyển khoản, số tài khoản nhận; không xóa cuộc trò chuyện.",
        "Mang đơn trình báo in sẵn đến công an phường/xã gần nhất hoặc gọi 113.",
    ]
    if has_missing_hotline:
        steps.insert(
            1,
            "Nếu chưa có số tổng đài ở trên, hãy gọi số in ở mặt sau thẻ hoặc trong ứng dụng "
            "ngân hàng của bạn.",
        )
    if future_time:
        steps.insert(
            0,
            "Giờ chuyển tiền bạn khai nằm ở tương lai (đồng hồ máy có thể sai): đừng chờ, hãy "
            "liên hệ ngân hàng và công an ngay.",
        )
    elif remaining == 0:
        steps.insert(0, "Đã quá 1 giờ đầu nhưng vẫn cần liên hệ ngân hàng và công an ngay.")
    return steps


def build_report_draft(
    req: GoldenHourDossierRequest, victim: BankFreezeHotline, mule: BankFreezeHotline | None
) -> str:
    when = _aware(req.transferred_at).astimezone(VN_TZ).strftime("%H:%M ngày %d/%m/%Y")
    account = mask_untrusted_account(req.mule_masked_account) or "(chưa có)"
    amount = _TIER_VI[req.amount_bucket_tier] if req.amount_bucket_tier else "(chưa rõ)"
    mule_bank = mule.bank_short_name if mule else "(chưa rõ)"
    lines = [
        "ĐƠN TRÌNH BÁO (bản nháp do VayShield AI soạn, người dùng tự kiểm tra trước khi nộp)",
        "Kính gửi: Công an phường/xã nơi cư trú.",
        "Tôi là: [Họ tên] - CCCD: [số CCCD] - SĐT: [số điện thoại] (điền tay, hệ thống không lưu).",
        f"Tôi bị lừa chuyển tiền qua {_CHANNEL_VI[req.scam_channel]} lúc {when} (giờ Việt Nam).",
        f"Số tiền ước tính: {amount}.",
        f"Ngân hàng chuyển đi: {victim.bank_short_name}.",
        f"Ngân hàng/tài khoản nhận (đã che): {mule_bank} - {account}.",
        "Đề nghị cơ quan chức năng xác minh, phối hợp ngân hàng phong tỏa tài khoản nhận và xử lý "
        "theo quy định pháp luật.",
        "Tài liệu kèm theo: ảnh chụp biên lai/tin nhắn (do người dùng tự đính kèm).",
    ]
    return "\n".join(lines)


def build_golden_hour_dossier(
    req: GoldenHourDossierRequest,
    registry: BankHotlineRegistry,
    now: datetime,
    dossier_id: UUID | None = None,
) -> GoldenHourDossierResponse:
    now = now.astimezone(UTC)
    elapsed = (now - _aware(req.transferred_at)).total_seconds()
    # Lệch nhỏ về tương lai (đồng hồ máy) -> coi như vừa chuyển. Xa hơn dung sai thì mốc giờ không
    # đáng tin: KHÔNG hứa "còn đủ 60 phút", coi như hết thời gian chờ và giục liên hệ ngay.
    future_time = elapsed < -FUTURE_SKEW_S
    minutes = min(MAX_MINUTES, max(0, int(elapsed // 60)))
    remaining = 0 if future_time else max(0, GOLDEN_HOUR_MINUTES - minutes)
    victim = _hotline_entry(registry, req.victim_bank_bin)
    hotlines = [victim]
    mule: BankFreezeHotline | None = None
    if req.mule_bank_bin and req.mule_bank_bin != req.victim_bank_bin:
        mule = _hotline_entry(registry, req.mule_bank_bin)
        hotlines.append(mule)
    elif req.mule_bank_bin:
        mule = victim
    return GoldenHourDossierResponse(
        dossier_id=dossier_id or new_uuid7(),
        minutes_since_transfer=minutes,
        golden_hour_remaining_minutes=remaining,
        bank_freeze_hotlines=hotlines,
        police_hotline=POLICE_HOTLINE,
        steps_vi=_steps(remaining, any(not h.hotline for h in hotlines), future_time),
        police_report_draft_vi=build_report_draft(req, victim, mule),
        pdf_available=True,
    )
