"""Dữ liệu mock đúng contract cho giai đoạn M0 (Nhóm 2 dùng ngay qua MSW/Scalar).

Lấy từ ví dụ trong đặc tả (File 4 §4.2) khi có. Mọi giá trị là TỔNG HỢP — không PII thật.
Route stub trả các object này; test contract kiểm chúng hợp lệ theo ``contracts/``.
Kèm ``SAMPLE_REQUESTS``: request hợp lệ mẫu cho từng operationId (dùng trong test + docs).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from app.core.errors import ErrorCode
from app.core.schemas import (
    audits,
    b2b,
    devices,
    family,
    ops,
    policies,
    realtime,
    sandbox,
    shield,
    sse,
    triage,
)
from app.core.schemas.common import (
    AmountBucketTier,
    CitedClause,
    HsmState,
    KernelSignal,
    LegalCategory,
    OntologyAction,
    OntologyAxis,
    Severity,
    VerdictCode,
    VerdictSummary,
)

MOCK_NOW: Final = datetime(2026, 11, 25, 1, 6, 40, tzinfo=UTC)
AUDIT_ID: Final = UUID("019361a8-7c10-7000-8000-112233445566")
DEVICE_ID: Final = UUID("019361a0-0000-7000-8000-000000000001")
SESSION_ID: Final = UUID("019361a1-0000-7000-8000-000000000002")
ESCALATION_ID: Final = UUID("019361a2-0000-7000-8000-000000000003")
LINK_ID: Final = UUID("019361a3-0000-7000-8000-000000000004")
RESCUE_CARD_ID: Final = UUID("019361a4-0000-7000-8000-000000000005")
SHIELD_ID: Final = UUID("019361aa-9100-7123-9a00-abcdef012345")
CLUSTER_ID: Final = UUID("019361a5-0000-7000-8000-000000000006")
MULE_NODE_A: Final = UUID("019361a6-0000-7000-8000-00000000000a")
MULE_NODE_B: Final = UUID("019361a6-0000-7000-8000-00000000000b")
DOSSIER_ID: Final = UUID("019361a7-0000-7000-8000-000000000007")
EVENT_ID: Final = UUID("019361a9-0000-7000-8000-000000000009")
CONNECTION_ID: Final = UUID("019361ab-0000-7000-8000-00000000000c")

_CITED: Final = [
    CitedClause(
        code="BLHS_2015_D174",
        title="Điều 174 Bộ luật Hình sự - Tội lừa đảo chiếm đoạt tài sản",
    ),
    CitedClause(
        code="TT_43_2016_NHNN",
        title="Quy định minh bạch cho vay tiêu dùng của Công ty Tài chính",
    ),
]

# ------------------------------------------------------------ SSE Trạm 2 ---

SSE_SANITIZED: Final = sse.AuditSanitized(
    audit_id=AUDIT_ID, image_count=2, blur_ok=True, elapsed_ms=112
)
SSE_VIETQR: Final = sse.AuditVietqrDecoded(
    found=True,
    bank_bin="970407",
    masked_account="1903****8899",
    mule_quorum_hit=True,
    preset_amount_vnd=2_000_000,
)
SSE_FIELDS: Final = sse.AuditFieldsExtracted(
    claimed_lender="Home Credit",
    nominal_principal_vnd=20_000_000,
    net_disbursed_vnd=0,
    upfront_fee_demanded=True,
    upfront_fee_amount_vnd=2_000_000,
    upfront_reason="Phí mở băng sai số tài khoản",
    confidence=0.97,
)
SSE_VERDICT: Final = sse.AuditVerdictComputed(
    verdict_code=VerdictCode.V_RED_BRAND_SPOOF,
    severity=Severity.CRITICAL_RED,
    kernel_signal=KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM,
    apr_vn_365=None,
    ear_xirr=None,
    lost_workdays=6.25,
    official_hotline_verify="1900 633 633",
    contradictions=[
        "Ảnh mang logo Home Credit nhưng đòi chuyển 2.000.000đ phí mở băng vào tài khoản "
        "cá nhân 1903****8899"
    ],
)
SSE_LEGAL: Final = sse.AuditLegalExplained(
    headline_vi="DỪNG NGAY! Đây là bẫy mạo danh Home Credit thu phí mở khóa giả",
    tts_voice_script=(
        "Cô chú dừng lại ngay! Công ty Home Credit thật không bao giờ bắt chuyển 2 triệu đồng "
        "để mở khóa tài khoản bị treo. Số tài khoản trên mã QR này đã bị cảnh báo lừa đảo!"
    ),
    cited_clauses=_CITED,
)
CONTRACT_STREAM_EVENTS: Final[list[tuple[str, Any]]] = [
    ("audit.sanitized", SSE_SANITIZED),
    ("audit.vietqr_decoded", SSE_VIETQR),
    ("audit.fields_extracted", SSE_FIELDS),
    ("audit.verdict_computed", SSE_VERDICT),
    ("audit.legal_explained", SSE_LEGAL),
]
SSE_PING: Final = sse.Ping(elapsed_ms=2500)
SSE_ERROR: Final = sse.AuditError(
    code=ErrorCode.UPSTREAM_TIMEOUT,
    message_vi="AI đọc ảnh quá chậm. Bạn nhập nhanh 3 con số để hệ thống tính ngay.",
    trace_id="0" * 32,
    retryable=True,
    fallback=sse.FallbackMode.MANUAL_3_FIELD_FORM,
)
SSE_OPS_SIGNAL: Final = sse.OpsThreatSignal(
    signal_id=EVENT_ID,
    kind="QUORUM_REACHED",
    cluster_id=CLUSTER_ID,
    mule_node_id=MULE_NODE_A,
    bank_bin="970407",
    masked_account_display="1903****8899",
    severity_level="CRITICAL_WAVE",
    occurred_at=MOCK_NOW,
    summary_vi="Tài khoản 1903****8899 đạt quorum 3 thiết bị độc lập trong 6 giờ",
)

VERDICT_SPOOF: Final = VerdictSummary(
    **SSE_VERDICT.model_dump(),
    headline_vi=SSE_LEGAL.headline_vi,
    tts_voice_script=SSE_LEGAL.tts_voice_script,
    cited_clauses=_CITED,
)
VERDICT_USURY: Final = VerdictSummary(
    verdict_code=VerdictCode.V_RED_CRIMINAL_USURY,
    severity=Severity.RED,
    apr_vn_365=501.0,
    apr_min_bound=501.0,
    apr_max_bound=501.0,
    ear_xirr=6568.5,
    lost_workdays=10.94,
    headline_vi="Lãi thực 501%/năm — gấp hơn 25 lần trần 20% của Bộ luật Dân sự",
    tts_voice_script=(
        "Cô chú cẩn thận! Nhận tám triệu rưỡi mà phải trả mười hai triệu sau ba mươi ngày, "
        "lãi thực là năm trăm lẻ một phần trăm một năm."
    ),
    cited_clauses=[
        CitedClause(code="BLDS_2015_D468", title="Điều 468 Bộ luật Dân sự - Lãi suất"),
        CitedClause(code="BLHS_2015_D201", title="Điều 201 Bộ luật Hình sự - Cho vay lãi nặng"),
    ],
)

# ---------------------------------------------------------------- REST ---

DEVICE_REGISTER: Final = devices.DeviceRegisterResponse(
    device_id=DEVICE_ID,
    policy_version=1,
    daily_wage_vnd=320_000,
    phone_optin_expires_at=MOCK_NOW + timedelta(days=90),
    server_epoch=devices.SignedEpochOffset(
        server_time_ms=int(MOCK_NOW.timestamp() * 1000),
        key_id="policy-k1",
        signature_b64="TU9DS19TSUdOQVRVUkVfT05MWQ==",
    ),
)
DEVICE_WIPE: Final = devices.DeviceWipeResponse(
    status="WIPED", wiped_at=MOCK_NOW, family_links_revoked=1, mule_edges_tombstoned=2
)

POLICY_BUNDLE: Final = policies.PolicyBundle(
    policy_version=1,
    issued_at=MOCK_NOW,
    expires_at=MOCK_NOW + timedelta(days=7),
    algorithm="Ed25519",
    key_id="policy-k1",
    payload=policies.PolicyPayload(
        lender_whitelist=[
            policies.WhitelistedLender(
                entity_code="FINCO_HOMECREDIT",
                official_short_name="Home Credit",
                legal_category=LegalCategory.LICENSED_FINCO,
                official_hotlines=["1900 633 633"],
                official_domains=["homecredit.vn"],
                android_packages=[policies.AndroidPackageSig(pkg="vn.homecredit.hcvn")],
            )
        ],
        banking_app_packages=["com.VCB", "com.mbmobile", "vn.com.techcombank.bb.app"],
        dangerous_permission_combos=[
            ["android.permission.READ_CONTACTS", "android.permission.READ_MEDIA_IMAGES"]
        ],
        thresholds=policies.PolicyThresholds(
            p_scam_watch=0.35,
            p_scam_cooling_off=0.75,
            cooling_off_seconds=15,
            shield_duration_hours=24,
            smurf_window_minutes=15,
        ),
        challenge_templates=[
            policies.ChallengeTemplate(
                challenge_id="CH_PURPOSE_V1",
                prompt_vi=(
                    "Bạn vừa nhận khoản tiền giải ngân mới. Giao dịch chuyển tiền lúc này dùng "
                    "để làm thủ tục gì?"
                ),
                choices=[
                    policies.ChallengeChoiceTemplate(
                        choice_id="CHOICE_LOAN_REVERSAL",
                        label_vi="Hoàn trả / Hủy khoản vay vừa nhận theo hướng dẫn của nhân viên",
                    ),
                    policies.ChallengeChoiceTemplate(
                        choice_id="CHOICE_ACCOUNT_UNLOCK",
                        label_vi="Xác minh tài khoản / Mở khóa lỗi hồ sơ để nhận lại tiền",
                    ),
                    policies.ChallengeChoiceTemplate(
                        choice_id="CHOICE_PERSONAL_SPENDING",
                        label_vi="Mua sắm / Trả tiền sinh hoạt cá nhân",
                    ),
                ],
            )
        ],
    ),
    signature_b64="TU9DS19TSUdOQVRVUkVfT05MWQ==",
)

APK_VERIFY: Final = audits.ApkVerifyResponse(
    audit_id=AUDIT_ID,
    apk_verdict=audits.ApkVerdict.LOOKALIKE_SPOOF,
    severity=Severity.CRITICAL_RED,
    matched_lender=audits.MatchedLender(
        entity_code="FINCO_HOMECREDIT",
        official_short_name="Home Credit",
        legal_category=LegalCategory.LICENSED_FINCO,
        official_hotlines=["1900 633 633"],
    ),
    lookalike_of="Home Credit",
    dangerous_permissions=["android.permission.READ_CONTACTS"],
    reasons_vi=["Tên ứng dụng giống Home Credit nhưng chữ ký APK không khớp bản chính thức"],
    recommend_contract_scan=True,
)

VOICE_TERMS: Final = audits.VoiceTermsResponse(
    audit_id=AUDIT_ID,
    transcript_masked="vay mười củ, thực nhận tám củ rưỡi, ba mươi ngày trả mười hai củ",
    normalized_terms=audits.NormalizedTerms(
        nominal_principal_vnd=10_000_000,
        net_disbursed_vnd=8_500_000,
        total_repayment_vnd=12_000_000,
        tenure_days=30,
        upfront_fee_amount_vnd=0,
    ),
    slang_tokens=[
        audits.SlangToken(raw="mười củ", value_vnd=10_000_000),
        audits.SlangToken(raw="tám củ rưỡi", value_vnd=8_500_000),
    ],
    needs_confirmation=True,
    verdict=VERDICT_USURY,
)

RECALCULATE: Final = audits.RecalculateResponse(
    audit_id=AUDIT_ID, verdict=VERDICT_USURY, computed_in_ms=0.8, llm_calls=0
)

SHIELD_ARM: Final = shield.ShieldArmResponse(
    session_id=SESSION_ID,
    fiber_key="default",
    hsm_state=HsmState.S4_SHIELD_ARMED_24H,
    shield_expires_at=MOCK_NOW + timedelta(hours=24),
    risk_log_odds=-3.5,
    deduplicated=False,
)

EVALUATE_RESPONSE: Final = shield.EvaluateResponseResult(
    session_id=SESSION_ID,
    action=OntologyAction.RED,
    detected_axes=[
        shield.DetectedAxis(axis=OntologyAxis.C1, name="LOAN_REVERSAL_CANCELLATION", confidence=1.0)
    ],
    computed_scam_prob=0.957,
    next_hsm_state=HsmState.S7_ESCALATED_SCAM_CONFIRMED,
    message_vi=(
        "Mọi khoản vay hợp pháp chỉ tất toán trong ứng dụng chính thức. Không ai được yêu cầu "
        "bạn chuyển tiền ra ngoài để hủy khoản vay."
    ),
    tts_script_vi="Cô chú dừng lại! Hủy khoản vay không bao giờ phải chuyển tiền cho nhân viên.",
    callback_hotline_hint_vi="Hãy cúp máy và tự gọi tổng đài chính thức in trên hợp đồng.",
    escalate_recommended=True,
    follow_up_challenge=None,
)

ESCALATE: Final = shield.EscalateResponse(
    escalation_id=ESCALATION_ID,
    status="PENDING_ACK",
    channels_attempted=[shield.EscalationChannel.WEBSOCKET, shield.EscalationChannel.WEB_PUSH],
    guardians_targeted=1,
    ack_deadline_at=MOCK_NOW + timedelta(seconds=10),
)

PAIR_INIT: Final = family.PairInitResponse(
    link_id=LINK_ID,
    pairing_code="K7M2QX",
    qr_payload="vayshield://pair?code=K7M2QX",
    expires_at=MOCK_NOW + timedelta(minutes=10),
    max_fail_attempts=3,
)
PAIR_CONFIRM: Final = family.PairConfirmResponse(
    link_id=LINK_ID,
    status="ACTIVE",
    guardian_alias="Con trai",
    borrower_display_label="Mẹ",
    guardian_session_token="gst_mock_only_not_a_real_token",
    guardian_session_expires_at=MOCK_NOW + timedelta(days=180),
)
RESCUE_CARD: Final = family.SendRescueCardResponse(
    rescue_card_id=RESCUE_CARD_ID,
    delivered_via=[shield.EscalationChannel.WEBSOCKET],
    queued=False,
    sent_at=MOCK_NOW,
)

WS_TICKET: Final = realtime.WsTicketResponse(
    ticket="wst_mock_only_not_a_real_ticket",
    role=realtime.RealtimeRole.GUARDIAN,
    expires_at=MOCK_NOW + timedelta(seconds=45),
    ttl_seconds=45,
    ws_path="/ws/v1/guardian",
)

GOLDEN_HOUR: Final = triage.GoldenHourDossierResponse(
    dossier_id=DOSSIER_ID,
    minutes_since_transfer=12,
    golden_hour_remaining_minutes=48,
    bank_freeze_hotlines=[
        triage.BankFreezeHotline(
            bank_bin="970407",
            bank_short_name="Techcombank",
            hotline="1800 588 822",
            verified=False,
        )
    ],
    police_hotline="113",
    steps_vi=[
        "Gọi ngay tổng đài ngân hàng của bạn để yêu cầu phong tỏa/tra soát giao dịch.",
        "Không chuyển thêm bất kỳ khoản nào để 'lấy lại tiền'.",
        "Mang đơn trình báo in sẵn đến công an phường/xã gần nhất.",
    ],
    police_report_draft_vi="ĐƠN TRÌNH BÁO (bản nháp mô phỏng) ...",
    pdf_available=True,
)

TTS: Final = triage.TtsResponse(
    normalized_text_vi="Cô chú dừng lại ngay! Hai triệu đồng ...",
    engine="CLIENT_FALLBACK",
)

B2B_SHIELD: Final = b2b.DisbursementShieldResponse(
    shield_id=SHIELD_ID,
    status=b2b.B2BShieldStatus.ARMED_ON_DEVICE,
    shield_expires_at=datetime(2026, 11, 26, 1, 6, 40, tzinfo=UTC),
    pre_simo_active_wave_warning=b2b.PreSimoWaveWarning(
        impersonation_wave_active=True, active_clusters_targeting_brand=2
    ),
)

MULE_CLUSTERS: Final = ops.MuleClusterPage(
    items=[
        ops.MuleCluster(
            cluster_id=CLUSTER_ID,
            campaign_label="[Dữ liệu Mô phỏng Thực tiễn] Mạo danh Home Credit thu phí mở băng",
            impersonated_lender="Home Credit",
            dominant_scam_trap=ops.ScamTrap.TRAP_1_ADVANCE_FEE,
            severity_level=ops.SeverityLevel.CRITICAL_WAVE,
            is_simulated_seed=True,
            first_seen_at=MOCK_NOW - timedelta(days=2),
            last_seen_at=MOCK_NOW,
            nodes=[
                ops.MuleNode(
                    node_id=MULE_NODE_A,
                    bank_bin="970407",
                    masked_account_display="1903****8899",
                    distinct_device_quorum=3,
                    threat_confidence=0.91,
                    simo_dossier_status=ops.SimoDossierStatus.QUORUM_REACHED,
                    is_dynamic_va_range=False,
                ),
                ops.MuleNode(
                    node_id=MULE_NODE_B,
                    bank_bin="970422",
                    masked_account_display="963888****02",
                    distinct_device_quorum=2,
                    threat_confidence=0.78,
                    simo_dossier_status=ops.SimoDossierStatus.OBSERVING,
                    is_dynamic_va_range=True,
                ),
            ],
            links=[
                ops.MuleLink(
                    source_node_id=MULE_NODE_A,
                    target_node_id=MULE_NODE_B,
                    relation="SHARED_SCAM_SCRIPT",
                )
            ],
        )
    ],
    next_cursor=None,
)

SIMO_DOSSIER: Final = ops.SimoDossier(
    dossier_id=DOSSIER_ID,
    mule_node_id=MULE_NODE_A,
    bank_bin="970407",
    masked_account_display="1903****8899",
    account_holder_masked="NGUYEN V** B",
    simo_dossier_status=ops.SimoDossierStatus.QUORUM_REACHED,
    distinct_device_quorum=3,
    cluster_label=MULE_CLUSTERS.items[0].campaign_label,
    evidence=[
        ops.DossierEvidence(
            channel=ops.EvidenceChannel.VIETQR_CRYPTO_DECODED,
            reported_at=MOCK_NOW - timedelta(hours=3),
            masked_snippet_vi="QR đòi 2.000.000đ 'phí mở băng' về TK 1903****8899",
        )
    ],
    advisory_draft_vi="Cảnh báo sớm: làn sóng mạo danh Home Credit thu phí mở băng.",
    generated_at=MOCK_NOW,
)

OPS_TOKEN: Final = ops.OpsTokenResponse(
    access_token="mock.ops.access-token",
    token_type="bearer",
    expires_in=900,
    refresh_token="mock-ops-refresh-token",
    refresh_expires_in=30 * 24 * 3600,
    roles=["FRAUD_ANALYST"],
)

SANDBOX_SESSION: Final = sandbox.SandboxSessionCreateResponse(
    sandbox_session_token="sbx_mock_only_not_a_real_token",
    sandbox_namespace="sbx_019361ac",
    tier=sandbox.SandboxTier.PUBLIC,
    expires_at=MOCK_NOW + timedelta(hours=2),
    daily_budget_remaining_usd=2.0,
)
SIMULATE_EVENT: Final = sandbox.SimulateEventResponse(
    event_id=EVENT_ID,
    sandbox_namespace="sbx_019361ac",
    accepted=True,
    resulting_hsm_state=HsmState.S4_SHIELD_ARMED_24H,
    risk_log_odds=-1.2,
    scam_probability=0.23,
    side_effects=["Khiên 24h đã bật cho fiber 'default'"],
)

# ------------------------------------------------------------- WS frames ---

WS_SERVER_FRAMES: Final[dict[str, Any]] = {
    "SERVER_HELLO": realtime.ServerHello(
        connection_id=CONNECTION_ID,
        role=realtime.RealtimeRole.GUARDIAN,
        heartbeat_interval_s=20,
        ack_timeout_s=10,
    ),
    "EVENT_EMERGENCY_ALERT": realtime.EmergencyAlert(
        escalation_id=ESCALATION_ID,
        borrower_display_label="Mẹ",
        hsm_state=HsmState.S7_ESCALATED_SCAM_CONFIRMED,
        detected_axes=[OntologyAxis.C1],
        headline_vi="Mẹ đang bị dụ chuyển tiền ngay sau khi nhận khoản vay!",
        body_vi="Hãy gọi cho Mẹ ngay để ngăn giao dịch.",
        raised_at=MOCK_NOW,
    ),
    "EVENT_ESCALATION_ACKNOWLEDGED": realtime.EscalationAcknowledged(
        escalation_id=ESCALATION_ID,
        guardian_alias="Con trai",
        ack_latency_ms=3200,
        message_vi="Con trai đã nhận được cảnh báo và đang gọi cho bạn!",
    ),
    "EVENT_ESCALATION_FALLBACK": realtime.EscalationFallback(
        escalation_id=ESCALATION_ID,
        channel=shield.EscalationChannel.SMS,
        reason="ACK_TIMEOUT",
        message_vi="Đã gửi tin nhắn khẩn tới người thân.",
    ),
    "EVENT_RESCUE_CARD": realtime.RescueCardDelivered(
        rescue_card_id=RESCUE_CARD_ID,
        escalation_id=ESCALATION_ID,
        card_type=family.RescueCardType.STOP_TRANSFER_NOW,
        guardian_alias="Con trai",
        message_vi="Mẹ ơi dừng lại, đừng chuyển tiền! Con gọi ngay đây.",
        tts_script_vi="Mẹ ơi dừng lại, đừng chuyển tiền! Con gọi ngay đây.",
    ),
    "EVENT_ALERT_DIGEST": realtime.AlertDigest(
        stale_alert_count=2,
        oldest_raised_at=MOCK_NOW - timedelta(minutes=50),
        summary_vi="Có 2 cảnh báo cũ hơn 45 phút đã được gom lại.",
    ),
    "SERVER_PING": realtime.ServerPing(server_ts_ms=int(MOCK_NOW.timestamp() * 1000)),
    "SERVER_ERROR": realtime.ServerError(
        code="TICKET_INVALID_OR_CONSUMED", message_vi="Vé kết nối đã được dùng."
    ),
}
WS_CLIENT_FRAMES: Final[dict[str, Any]] = {
    "CLIENT_ALERT_ACK": realtime.ClientAlertAck(escalation_id=ESCALATION_ID),
    "CLIENT_RESCUE_CARD_ACK": realtime.ClientRescueCardAck(rescue_card_id=RESCUE_CARD_ID),
    "CLIENT_PONG": realtime.ClientPong(server_ts_ms=int(MOCK_NOW.timestamp() * 1000)),
}

# -------------------------------------------------- sample requests (test) ---

SAMPLE_PUBLIC_KEY_B64: Final = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEmockmockmockmockmockmockmockmockmockmockmock"
    "mockmockmockmockmockmockmockmockmockmockmockmock=="
)

SAMPLE_REQUESTS: Final[dict[str, dict[str, Any]]] = {
    "devicesRegister": {
        "public_key_spki_b64": SAMPLE_PUBLIC_KEY_B64,
        "platform_type": "WEB_PWA",
        "app_version": "1.0.0",
        "consents": {"contract_scan": True, "shield_monitoring": True, "family_link": False},
    },
    "auditsApkVerify": {
        "package_name": "vn.homecredit.fake",
        "signing_cert_sha256": "a" * 64,
        "requested_permissions": ["android.permission.READ_CONTACTS"],
    },
    "auditsRecalculate": {
        "nominal_principal_vnd": 12_000_000,
        "net_disbursed_vnd": 8_500_000,
        "total_repayment_vnd": 12_000_000,
        "tenure_days": 30,
    },
    "shieldArm": {
        "trigger_origin": "STATION_3_NOTIF",
        "amount_bucket_tier": AmountBucketTier.TIER_10M_30M.value,
        "client_event_id": "evt-0001-abcdef",
    },
    "shieldEvaluateResponse": {
        "session_id": str(SESSION_ID),
        "challenge_id": "CH_PURPOSE_V1",
        "choice_id": "CHOICE_LOAN_REVERSAL",
        "sensors": {"audio_call_state": "ACTIVE_CELLULAR"},
    },
    "shieldEscalate": {
        "session_id": str(SESSION_ID),
        "reason": "COERCION_DETECTED",
        "hsm_state": "S7_ESCALATED_SCAM_CONFIRMED",
        "detected_axes": ["C1"],
        "client_event_id": "evt-0002-abcdef",
        "occurred_at": MOCK_NOW.isoformat(),
    },
    "familyPairInit": {"guardian_alias": "Con trai", "borrower_display_label": "Mẹ"},
    "familyPairConfirm": {"pairing_code": "K7M2QX", "consent_accepted": True},
    "familySendRescueCard": {
        "escalation_id": str(ESCALATION_ID),
        "card_type": "STOP_TRANSFER_NOW",
    },
    "triageGoldenHourDossier": {
        "victim_bank_bin": "970407",
        "transferred_at": MOCK_NOW.isoformat(),
        "scam_channel": "PHONE_CALL",
    },
    "ttsSynthesizeVi": {"text_vi": "Cô chú dừng lại ngay! 2tr đồng"},
    "b2bDisbursementShield": {
        "borrower_phone_blind_hmac": (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        ),
        "amount_bucket_tier": "TIER_10M_30M",
        "product_type": "CASH_CONSUMER_LOAN",
        "shield_duration_hours": 24,
        "elevated_campaign_watch": True,
    },
    "opsAuthToken": {"grant_type": "judge_quick_login", "judge_access_code": "mock"},
    "sandboxCreateSession": {},
    "sandboxSimulateEvent": {"event_type": "BANK_DISBURSEMENT_NOTIF"},
}
