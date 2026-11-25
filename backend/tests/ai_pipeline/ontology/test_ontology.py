"""T07: phân loại 10 trục C1-C10 + hỏi đáp bất đối xứng.

Edge case: S2-EC22, EC-AI12, EC-AI13, S2-EC47.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_pipeline.ontology import classify_response, generate_challenge
from ai_pipeline.ontology.challenge import CHOICE_PERSONAL, CHOICE_REVERSAL, CHOICE_VERIFY
from ai_pipeline.ontology.llm import FakeLlmIntentClassifier
from app.core.schemas.common import AudioCallState, OntologyAction, OntologyAxis
from app.core.schemas.shield import SensorSnapshot

NO_CALL = SensorSnapshot(audio_call_state=AudioCallState.NONE)
CALL = SensorSnapshot(audio_call_state=AudioCallState.ACTIVE_CELLULAR)

# >= 3 câu biến thể / trục, KHÔNG dùng cụm từ khóa gốc của đặc tả.
VARIANTS: dict[OntologyAxis, list[str]] = {
    OntologyAxis.C1: [
        "Tôi không muốn vay nữa nên gửi trả lại số tiền vừa về",
        "Tất toán sớm món này qua số tài khoản mà nhân viên đọc cho tôi",
        "Họ giải ngân nhầm cho tôi, phải hoàn lại số tiền vừa nhận",
    ],
    OntologyAxis.C2: [
        "Tôi nhập thiếu một số tài khoản nên phải chuyển tiền để sửa",
        "Tiền bị treo liên ngân hàng, cần giải tỏa lệnh",
        "Tên trên căn cước bị lệch nên phải nộp tiền kích hoạt lại",
    ],
    OntologyAxis.C3: [
        "Đẩy hạng tín nhiệm lên để được duyệt hồ sơ",
        "Chứng minh em có năng lực chi trả rồi họ trả lại liền",
        "Chuyển một khoản đối ứng rồi nhận về",
    ],
    OntologyAxis.C4: [
        "Chuyển vào két sắt điện tử của kho bạc",
        "Nộp vào số tài khoản bảo chứng do giám sát viên cấp",
        "Gửi vào ví trung gian để bảo lãnh hồ sơ",
    ],
    OntologyAxis.C5: [
        "Họ bảo nếu không chuyển liền trong 10 phút là gửi giấy về nhà máy",
        "Em sợ bị phạt nặng vì vi phạm điều khoản",
        "Gấp lắm, trễ là bị báo lên công an",
    ],
    OntologyAxis.C6: [
        "Phải đóng tiền bảo hiểm cho khoản này trước khi rút",
        "Nộp thuế thu nhập mới được rút tiền",
        "Lệ phí làm hồ sơ phát sinh sau khi tiền về",
    ],
    OntologyAxis.C8: [
        "Quét mã thanh toán giả để lấy tiền mặt từ hạn mức ví",
        "Người môi giới bảo dùng ví mua trước trả sau rồi đổi ra tiền",
        "Mua đơn hàng ảo ở cửa hàng đối tác để rút tiền",
    ],
    OntologyAxis.C9: [
        "Anh tuyển dụng nhờ em lấy tiền rồi gửi lại cho anh để được tiền công",
        "Sếp bảo mượn giấy tờ của em đi vay rồi đưa tiền cho sếp",
        "Bạn nhờ vay giùm rồi cho em hoa hồng",
    ],
    OntologyAxis.C10: [
        "Cơ quan điều tra bảo chuyển vào tài khoản tạm giữ để chứng minh trong sạch",
        "Viện kiểm sát gọi nói em dính vụ rửa tiền",
        "Cán bộ tòa án yêu cầu phong tỏa tiền của em",
    ],
}


@pytest.mark.parametrize(
    ("axis", "text"),
    [(axis, t) for axis, texts in VARIANTS.items() for t in texts],
)
def test_axis_variants_detected(axis: OntologyAxis, text: str) -> None:
    res = classify_response(text=text, sensors=NO_CALL)
    assert axis in {d.axis for d in res.detected_axes}, text
    assert res.action == OntologyAction.RED
    assert res.escalate_recommended is True
    assert res.computed_scam_prob >= 0.9


# C7: né tránh ngắn + cuộc gọi + 60 phút đầu + mở lại app >= 2 lần.
C7_SENSORS = SensorSnapshot(
    audio_call_state=AudioCallState.ACTIVE_CELLULAR,
    hesitation_ms=2200,
    bank_app_reopen_count=3,
    seconds_since_disbursement=900,
)


@pytest.mark.parametrize("text", ["việc riêng", "chuyển cho con", "trả tiền hàng", "mua đồ thôi"])
def test_c7_short_evasive_under_call(text: str) -> None:
    res = classify_response(text=text, sensors=C7_SENSORS)
    assert res.action == OntologyAction.ORANGE_RED
    assert [d.axis for d in res.detected_axes] == [OntologyAxis.C7]
    assert res.lock_extension_s == 20
    assert res.escalate_recommended is False


def test_c7_hesitation_over_1_8s_under_call_without_text() -> None:
    s = SensorSnapshot(audio_call_state=AudioCallState.ACTIVE_VOIP, hesitation_ms=1801)
    assert classify_response(text=None, sensors=s).action == OntologyAction.ORANGE_RED


def test_c7_hesitation_exactly_1_8s_is_not_hesitation() -> None:
    s = SensorSnapshot(
        audio_call_state=AudioCallState.ACTIVE_CELLULAR,
        hesitation_ms=1800,
    )
    res = classify_response(text="Em mua một chiếc ghế cho phòng khách của nhà em", sensors=s)
    assert res.action == OntologyAction.PASS


def test_c7_full_conditions_more_confident_than_partial() -> None:
    full = classify_response(text="việc riêng", sensors=C7_SENSORS)
    partial = classify_response(
        text="việc riêng", sensors=CALL.model_copy(update={"hesitation_ms": 2200})
    )
    assert full.computed_scam_prob > partial.computed_scam_prob


def test_c7_requires_call_short_answer_without_call_passes() -> None:
    assert classify_response(text="việc riêng", sensors=NO_CALL).action == OntologyAction.PASS
    s = NO_CALL.model_copy(update={"hesitation_ms": 5000})
    assert classify_response(text="việc riêng", sensors=s).action == OntologyAction.PASS


def test_s2_ec22_generic_choice_under_call_does_not_unlock() -> None:
    """S2-EC22: đang IN_CALL, chọn 'mua sắm/sinh hoạt' (kịch bản mớm lời) không được PASS."""
    res = classify_response(choice_id=CHOICE_PERSONAL, sensors=CALL)
    assert res.action == OntologyAction.ORANGE_RED
    assert res.countdown_s == 15
    assert OntologyAxis.C7 in {d.axis for d in res.detected_axes}


def test_s2_ec22_scripted_phrase_trả_nợ_tiền_hàng() -> None:
    res = classify_response(text="Tôi trả nợ tiền hàng cho bạn", sensors=CALL)
    assert res.action == OntologyAction.ORANGE_RED


def test_personal_choice_without_call_passes() -> None:
    assert classify_response(choice_id=CHOICE_PERSONAL, sensors=NO_CALL).action == (
        OntologyAction.PASS
    )


def test_trap_choices_are_red_100() -> None:
    r1 = classify_response(choice_id=CHOICE_REVERSAL, sensors=NO_CALL)
    assert r1.action == OntologyAction.RED
    assert r1.computed_scam_prob == 1.0
    assert r1.detected_axes[0].axis == OntologyAxis.C1
    r2 = classify_response(choice_id=CHOICE_VERIFY, sensors=NO_CALL)
    assert {d.axis for d in r2.detected_axes} == {OntologyAxis.C2, OntologyAxis.C4}


@pytest.mark.parametrize(
    "text",
    [
        "gửi khoản vay theo hướng dẫn của nhân viên",
        "ký khoản vay này đi",
        "quỷ khoản vay cho xong",
    ],
)
def test_ec_ai12_stt_confusion_huy_to_gui_ky_quy(text: str) -> None:
    res = classify_response(text=text, sensors=NO_CALL)
    assert OntologyAxis.C1 in {d.axis for d in res.detected_axes}


def test_ec_ai12_phonetic_confusion_doi_chung_and_treo() -> None:
    assert OntologyAxis.C3 in {
        d.axis
        for d in classify_response(text="chuyển dòng tiền đối chứng", sensors=NO_CALL).detected_axes
    }
    assert OntologyAxis.C2 in {
        d.axis
        for d in classify_response(text="lệnh trèo cần giải thoả", sensors=NO_CALL).detected_axes
    }


def test_ec_ai12_innocent_sentence_with_gui_is_not_flagged() -> None:
    res = classify_response(text="Tôi gửi tiền cho mẹ mua thuốc", sensors=NO_CALL)
    assert res.action == OntologyAction.PASS


@pytest.mark.parametrize("hesitation", [2500, 3000])
def test_ec_ai13_silence_2_5s_under_call(hesitation: int) -> None:
    s = SensorSnapshot(audio_call_state=AudioCallState.ACTIVE_CELLULAR, hesitation_ms=hesitation)
    res = classify_response(text="", sensors=s)
    assert res.action == OntologyAction.ORANGE_RED
    assert OntologyAxis.C7 in {d.axis for d in res.detected_axes}


def test_ec_ai13_silence_without_call_is_pass() -> None:
    s = SensorSnapshot(audio_call_state=AudioCallState.NONE, hesitation_ms=3000)
    assert classify_response(text="", sensors=s).action == OntologyAction.PASS


def test_s2_ec47_deepfake_voip_gives_callback_hotline_hint() -> None:
    s = SensorSnapshot(audio_call_state=AudioCallState.ACTIVE_VOIP)
    res = classify_response(
        text="Cán bộ gọi video Zalo bảo chuyển tiền chứng minh trong sạch", sensors=s
    )
    assert res.action == OntologyAction.RED
    assert res.callback_hotline_hint_vi is not None
    assert "Hotline chính thức" in res.callback_hotline_hint_vi
    assert "Hotline chính thức" in res.tts_script_vi


def test_s2_ec47_no_hint_for_cellular_plain_pass_and_non_video() -> None:
    assert classify_response(text="mua gạo", sensors=NO_CALL).callback_hotline_hint_vi is None
    res = classify_response(text="Họ bảo hoàn lại số tiền vừa về", sensors=NO_CALL)
    assert res.callback_hotline_hint_vi is None


def test_challenge_shuffled_per_session_but_deterministic() -> None:
    sid = uuid4()
    a, b = generate_challenge(sid), generate_challenge(sid)
    assert a == b
    orders = {tuple(c.choice_id for c in generate_challenge(uuid4()).choices) for _ in range(40)}
    assert len(orders) > 1
    assert {c for o in orders for c in o} == {CHOICE_REVERSAL, CHOICE_VERIFY, CHOICE_PERSONAL}
    assert len(a.choices) == 3
    assert a.challenge_id.startswith("ch_")


def test_challenge_labels_do_not_reveal_traps() -> None:
    labels = " ".join(c.label_vi for c in generate_challenge(uuid4()).choices).lower()
    assert "lừa đảo" not in labels and "scam" not in labels


def test_llm_only_consulted_when_rules_find_nothing() -> None:
    fake = FakeLlmIntentClassifier({"xyz la": OntologyAxis.C4})
    res = classify_response(text="Hoàn lại số tiền vừa về xyz la", sensors=NO_CALL, llm=fake)
    assert fake.calls == []
    assert res.used_llm is False
    res2 = classify_response(text="chuyển theo chỉ dẫn xyz la", sensors=NO_CALL, llm=fake)
    assert fake.calls and res2.used_llm
    assert res2.action == OntologyAction.RED


def test_llm_cannot_downgrade_rule_result() -> None:
    fake = FakeLlmIntentClassifier({})
    res = classify_response(text="Họ bảo hoàn lại số tiền vừa về", sensors=NO_CALL, llm=fake)
    assert res.action == OntologyAction.RED


def test_result_fields_match_contract_model() -> None:
    from app.core.schemas.shield import EvaluateResponseResult

    res = classify_response(text="Họ bảo hoàn lại số tiền vừa về", sensors=NO_CALL)
    EvaluateResponseResult(
        session_id=uuid4(),
        action=res.action,
        detected_axes=res.detected_axes,
        computed_scam_prob=res.computed_scam_prob,
        next_hsm_state="S5_CRITICAL_INTERCEPTION",
        message_vi=res.message_vi,
        tts_script_vi=res.tts_script_vi,
        callback_hotline_hint_vi=res.callback_hotline_hint_vi,
        escalate_recommended=res.escalate_recommended,
    )


@pytest.mark.parametrize(
    "text", ["Em mua gạo cho nhà", "Trả tiền điện tháng này", "Biếu bố mẹ ít tiền"]
)
def test_benign_text_passes_without_call(text: str) -> None:
    assert classify_response(text=text, sensors=NO_CALL).action == OntologyAction.PASS
