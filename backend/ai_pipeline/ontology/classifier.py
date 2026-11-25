# ruff: noqa: E501  (thông điệp tiếng Việt dài)
"""classify_response: luật tất định trước, LLM tùy chọn sau (File 3 §3.2–§3.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ai_pipeline.ontology.axes import is_evasive, match_axes
from ai_pipeline.ontology.challenge import CHOICE_PERSONAL, CHOICE_REVERSAL, CHOICE_VERIFY
from ai_pipeline.ontology.text import normalize
from app.core.schemas.common import ONTOLOGY_AXIS_NAMES, OntologyAction, OntologyAxis
from app.core.schemas.shield import DetectedAxis, SensorSnapshot

HESITATION_THRESHOLD_MS = 1800  # C7: ngập ngừng > 1.8s
SILENCE_THRESHOLD_MS = 2500  # EC-AI13: im lặng 2.5s
FIRST_HOUR_S = 3600
REOPEN_THRESHOLD = 2
CALM_COUNTDOWN_S = 15  # S2-EC22
C7_LOCK_EXTENSION_S = 20

HOTLINE_HINT_VI = (
    "Hình ảnh và giọng nói trên cuộc gọi video hoàn toàn có thể làm giả bằng AI — "
    "Chỉ gọi lại số Hotline chính thức!"
)

_ACTIVE_CALL = {"ACTIVE_CELLULAR", "ACTIVE_VOIP"}

_AXIS_MESSAGES: dict[OntologyAxis, tuple[str, str]] = {
    OntologyAxis.C1: (
        "Mọi khoản vay hợp pháp chỉ tất toán trong mục Hợp đồng của app chính thức. "
        "Tuyệt đối không chuyển khoản vào số tài khoản người khác đọc qua điện thoại.",
        "Dừng lại. Khoản vay hợp pháp chỉ tất toán trong ứng dụng chính thức, không chuyển khoản cho người lạ.",
    ),
    OntologyAxis.C2: (
        "Đây là kịch bản lừa đảo chiếm đoạt kép: không có lệnh treo hay lỗi hồ sơ nào cần nạp tiền để sửa. "
        "Hệ thống đang báo khẩn cho người thân.",
        "Cảnh báo lừa đảo. Không ai yêu cầu bạn chuyển tiền để sửa lỗi tài khoản. Hãy dừng ngay.",
    ),
    OntologyAxis.C3: (
        "CIC là Trung tâm Thông tin Tín dụng Quốc gia của NHNN, không có dịch vụ thu tiền để nâng điểm tín dụng.",
        "Không có dịch vụ nâng điểm tín dụng bằng cách chuyển tiền. Hãy dừng giao dịch.",
    ),
    OntologyAxis.C4: (
        "Không có tài khoản kỹ thuật, bảo lãnh hay giám sát nào cần bạn chuyển tiền vào. "
        "Đây là mẫu tài khoản trung gian của kẻ lừa đảo.",
        "Tài khoản trung gian là dấu hiệu lừa đảo. Đừng chuyển tiền.",
    ),
    OntologyAxis.C5: (
        "Áp lực phải chuyển tiền trong vài phút là thủ đoạn. Không ai có quyền phạt hay báo công an qua điện thoại.",
        "Hãy bình tĩnh. Đe dọa phạt hay báo công an để ép chuyển tiền là lừa đảo.",
    ),
    OntologyAxis.C6: (
        "Khoản vay hợp pháp không thu phí bảo hiểm, thuế hay phí hồ sơ ngoài luồng sau khi giải ngân.",
        "Thu phí ngoài luồng sau giải ngân là lừa đảo. Đừng chuyển thêm tiền.",
    ),
    OntologyAxis.C7: (
        "Bạn đang trong cuộc gọi và trả lời rất ngắn hoặc ngập ngừng. Đây có thể là hành vi bị mớm lời. "
        "Khóa bình tĩnh được gia hạn 20 giây, hãy gọi người thân để kiểm chứng.",
        "Có thể ai đó đang nhắc bài cho bạn. Hãy chờ 20 giây và gọi người thân kiểm chứng.",
    ),
    OntologyAxis.C8: (
        "Rút tiền mặt từ ví trả sau theo lời môi giới sẽ mất trắng hạn mức và bị khóa ví vĩnh viễn.",
        "Quét mã khống để rút tiền từ ví trả sau là bẫy. Hãy dừng lại.",
    ),
    OntologyAxis.C9: (
        "Đứng tên vay hộ nghĩa là bạn gánh 100% nghĩa vụ trả nợ và nợ xấu CIC nhóm 5.",
        "Đứng tên vay hộ người khác khiến bạn phải trả nợ. Đừng chuyển tiền cho họ.",
    ),
    OntologyAxis.C10: (
        "Bộ Công an cảnh báo: cơ quan điều tra không yêu cầu chuyển tiền vào tài khoản tạm giữ qua điện thoại.",
        "Công an không bao giờ yêu cầu chuyển tiền để chứng minh trong sạch. Đây là giả danh.",
    ),
}

_PASS_MESSAGE = ("Chưa phát hiện dấu hiệu bất thường. Hãy luôn kiểm tra kỹ người nhận tiền.", "")


class LlmIntentClassifier(Protocol):
    """Giao diện LLM tùy chọn; chỉ được gọi khi luật không bắt được trục nào."""

    def classify(self, text: str) -> list[DetectedAxis]: ...


@dataclass(frozen=True)
class OntologyResult:
    """Khớp các trường của `EvaluateResponseResult` (T12 nối vào route)."""

    action: OntologyAction
    detected_axes: list[DetectedAxis] = field(default_factory=list)
    computed_scam_prob: float = 0.0
    message_vi: str = ""
    tts_script_vi: str = ""
    callback_hotline_hint_vi: str | None = None
    escalate_recommended: bool = False
    countdown_s: int = 0
    lock_extension_s: int = 0
    used_llm: bool = False


def _axis(axis: OntologyAxis, confidence: float) -> DetectedAxis:
    return DetectedAxis(axis=axis, name=ONTOLOGY_AXIS_NAMES[axis], confidence=round(confidence, 2))


def _c7_confidence(text_evasive: bool, hesitated: bool, reopen_ok: bool, first_hour: bool) -> float:
    base = 0.6 + (0.1 if text_evasive else 0.0) + (0.1 if hesitated else 0.0)
    if reopen_ok and first_hour:
        base += 0.1
    return min(base, 0.9)


def classify_response(
    *,
    text: str | None = None,
    choice_id: str | None = None,
    sensors: SensorSnapshot,
    llm: LlmIntentClassifier | None = None,
) -> OntologyResult:
    """Phân loại câu trả lời/lựa chọn thành trục C1–C10 + hành động RED / ORANGE_RED / PASS."""
    norm = normalize(text) if text else ""
    in_call = sensors.audio_call_state.value in _ACTIVE_CALL
    is_voip = sensors.audio_call_state.value == "ACTIVE_VOIP"

    axes: dict[OntologyAxis, float] = {}
    for hit in match_axes(norm):
        axes[hit.axis] = max(axes.get(hit.axis, 0.0), hit.confidence)

    # Lựa chọn 1 chạm: hai bẫy phản mớm lời khớp 100%.
    if choice_id == CHOICE_REVERSAL:
        axes[OntologyAxis.C1] = 1.0
    elif choice_id == CHOICE_VERIFY:
        axes[OntologyAxis.C2] = 1.0
        axes[OntologyAxis.C4] = 1.0

    used_llm = False
    if not axes and norm and llm is not None:
        for d in llm.classify(norm):
            if d.axis != OntologyAxis.C7:
                axes[d.axis] = max(axes.get(d.axis, 0.0), d.confidence)
                used_llm = True

    # C7: cần cuộc gọi đang diễn ra (S2-EC22, EC-AI13).
    hesitation = sensors.hesitation_ms or 0
    hesitated = hesitation > HESITATION_THRESHOLD_MS
    silent = not norm and choice_id is None and hesitation >= SILENCE_THRESHOLD_MS
    generic_choice = choice_id == CHOICE_PERSONAL
    evasive = is_evasive(norm) or generic_choice
    no_input = not norm and choice_id is None
    if (
        in_call
        and not (axes.keys() - {OntologyAxis.C7})
        and (evasive or hesitated or silent or no_input)
    ):
        secs = sensors.seconds_since_disbursement
        first_hour = secs is not None and secs <= FIRST_HOUR_S
        reopen_ok = sensors.bank_app_reopen_count >= REOPEN_THRESHOLD
        # Đang gọi thì KHÔNG cho mở khóa bằng câu trả lời chung chung (S2-EC22).
        axes[OntologyAxis.C7] = _c7_confidence(evasive, hesitated or silent, reopen_ok, first_hour)
    elif in_call and hesitated and axes:
        axes[OntologyAxis.C7] = _c7_confidence(evasive, True, False, False)

    red_axes = [a for a in axes if a != OntologyAxis.C7]
    if red_axes:
        action = OntologyAction.RED
    elif OntologyAxis.C7 in axes:
        action = OntologyAction.ORANGE_RED
    else:
        action = OntologyAction.PASS

    detected = [_axis(a, c) for a, c in sorted(axes.items(), key=lambda kv: -kv[1])]
    prob = 0.0
    if action == OntologyAction.RED:
        prob = max(0.9, max(axes[a] for a in red_axes))
    elif action == OntologyAction.ORANGE_RED:
        prob = axes[OntologyAxis.C7]

    # S2-EC47: cuộc gọi VoIP/video có dấu hiệu -> nhắc gọi lại hotline chính thức.
    mentions_video = any(k in norm for k in ("video", "zalo", "deepfake", "facetime"))
    hint = (
        HOTLINE_HINT_VI if (action != OntologyAction.PASS and (is_voip or mentions_video)) else None
    )

    if action == OntologyAction.PASS:
        message, tts = _PASS_MESSAGE
    else:
        primary = red_axes[0] if red_axes else OntologyAxis.C7
        if red_axes:
            primary = max(red_axes, key=lambda a: axes[a])
        message, tts = _AXIS_MESSAGES[primary]
        if hint:
            message = f"{message} {hint}"
            tts = f"{tts} {hint}"

    return OntologyResult(
        action=action,
        detected_axes=detected,
        computed_scam_prob=round(min(prob, 1.0), 2),
        message_vi=message,
        tts_script_vi=tts,
        callback_hotline_hint_vi=hint,
        escalate_recommended=action == OntologyAction.RED,
        countdown_s=CALM_COUNTDOWN_S if action != OntologyAction.PASS else 0,
        lock_extension_s=C7_LOCK_EXTENSION_S if OntologyAxis.C7 in axes else 0,
        used_llm=used_llm,
    )
