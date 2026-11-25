"""Hỏi đáp bất đối xứng, trộn thứ tự lựa chọn theo phiên (File 3 §3.2)."""

from __future__ import annotations

import hashlib
import random
from uuid import UUID

from app.core.schemas.shield import Challenge, ChallengeChoice

CHOICE_REVERSAL = "ch_reversal"  # bẫy -> C1
CHOICE_VERIFY = "ch_verify"  # bẫy -> C2/C4
CHOICE_PERSONAL = "ch_personal"  # kiểm tra chéo cảm biến -> C7

PROMPT_VI = (
    "Bạn vừa nhận khoản tiền giải ngân mới. Giao dịch chuyển tiền lúc này dùng để làm thủ tục gì?"
)

_LABELS: dict[str, str] = {
    CHOICE_REVERSAL: "Hoàn trả / Hủy khoản vay vừa nhận theo hướng dẫn của nhân viên",
    CHOICE_VERIFY: "Xác minh tài khoản / Mở khóa lỗi hồ sơ để nhận lại tiền",
    CHOICE_PERSONAL: "Mua sắm / Trả tiền sinh hoạt cá nhân (nhập hoặc nói ngắn gọn 3 chữ)",
}


def generate_challenge(session: UUID | str) -> Challenge:
    """Thứ tự lựa chọn tất định theo phiên nhưng khác nhau giữa các phiên."""
    digest = hashlib.sha256(f"vayshield-challenge:{session}".encode()).digest()
    rng = random.Random(int.from_bytes(digest, "big"))  # noqa: S311 - trộn UI, không phải mật mã
    ids = list(_LABELS)
    rng.shuffle(ids)
    return Challenge(
        challenge_id=f"ch_{digest.hex()[:16]}",
        prompt_vi=PROMPT_VI,
        choices=[ChallengeChoice(choice_id=i, label_vi=_LABELS[i]) for i in ids],
    )
