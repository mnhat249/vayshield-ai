"""3 thí nghiệm đối chứng (File 3 §6.2). Số liệu chỉ từ lần chạy thật của code dưới đây.

Ablation 1 ở chế độ fake: baseline "Pure LLM math" là MÔ PHỎNG tất định (công thức ngây thơ mà
LLM hay dùng), KHÔNG phải kết quả của LLM thật. Chỉ chế độ LIVE (có duyệt) mới đo LLM thật.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.clock import FakeClock
from app.core.config import AppEnv, Settings
from app.core.schemas.ops import EvidenceChannel, SimoDossierStatus
from app.mule_graph.repository import InMemoryRepository
from app.mule_graph.service import MuleGraphService
from app.security.blind_index import BlindIndexer
from app.security.envelope import EnvelopeCipher

from bench_core import Metric, Sla
from suites import ContractResult, classify_sample, coercion_rates

# --------------------------------------------------------------------------- Ablation 1


def naive_flat_apr_pct(cashflows: Sequence[dict[str, int]]) -> float:
    """Baseline mô phỏng: lãi phẳng trên gốc ban đầu = (tổng trả - nhận)/nhận * 365/ngày cuối.
    Sai kinh điển với trả góp (bỏ qua dư nợ giảm dần)."""
    flows = sorted(cashflows, key=lambda f: f["day"])
    received = flows[0]["amount_vnd"]
    repaid = -sum(f["amount_vnd"] for f in flows[1:])
    days = flows[-1]["day"]
    return (repaid - received) / received * 365.0 / days * 100.0


def _rel_err_pct(got: float, want: float) -> float:
    return abs(got - want) / want * 100.0 if want else float("nan")


def ablation1(samples: Sequence[dict[str, Any]], results: Sequence[ContractResult]) -> list[Metric]:
    by_id = {r.sample_id: r for r in results}
    flat: list[float] = []
    vs: list[float] = []
    for s in samples:
        exp = s["expected_apr_vn_365"]
        r = by_id.get(s["id"])
        if exp is None or exp <= 0 or len(s["cashflows"]) <= 2 or r is None or r.apr_pct is None:
            continue  # chỉ ca trả góp nhiều kỳ có APR chuẩn
        naive = naive_flat_apr_pct(s["cashflows"])
        flat.append(_rel_err_pct(naive, exp))
        vs.append(_rel_err_pct(r.apr_pct, exp))
    ds = "Ablation1"
    sim = "MO PHONG tat dinh (khong phai LLM that); chi chay LLM that o che do LIVE"
    n = len(vs)

    def mean(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    return [
        Metric(
            ds,
            "Baseline 'Pure LLM' lai phang - sai so tuong doi TB (%)",
            mean(flat),
            "%",
            n,
            None,
            sim,
        ),
        Metric(
            ds,
            "VayShield Neuro-Symbolic - sai so tuong doi TB (%)",
            mean(vs),
            "%",
            n,
            Sla("<", 0.05),
            "oracle fake + bo giai app.finance (ca tra gop nhieu ky, APR != null)",
        ),
    ]


# --------------------------------------------------------------------------- Ablation 2


def ablation2(samples: Sequence[dict[str, Any]]) -> list[Metric]:
    text = coercion_rates(samples, multimodal=False)
    multi = coercion_rates(samples, multimodal=True)
    ds = "Ablation2"
    n_c = int(multi["n_coached"])
    note = "cung classifier tat dinh; text-only = chi loi khai, multimodal = + AudioMode/MediaProjection/TimeSinceDisb"
    coached = [s for s in samples if s["label"] == "SCAM" and s["coached"]]
    c7_text = sum(classify_sample(s, multimodal=False)[1] for s in coached)
    c7_multi = sum(classify_sample(s, multimodal=True)[1] for s in coached)

    def opt(v: float, n: int) -> float | None:
        return None if n == 0 or math.isnan(v) else v

    return [
        Metric(
            ds,
            "Text-only: ty le bo lot nan nhan bi mom loi (coached)",
            opt(1.0 - text["coached_detect"], n_c),
            "ratio",
            n_c,
            None,
            "tham chieu dac ta ~85% bo lot; " + note,
        ),
        Metric(
            ds,
            "Multimodal VayShield: ty le chan coached",
            opt(multi["coached_detect"], n_c),
            "ratio",
            n_c,
            Sla(">=", 0.93),
            "muc tieu dac ta > 93%; " + note,
        ),
        Metric(
            ds,
            "Text-only: coached co truc C7",
            opt(c7_text / n_c if n_c else math.nan, n_c),
            "ratio",
            n_c,
            None,
            note,
        ),
        Metric(
            ds,
            "Multimodal: coached co truc C7",
            opt(c7_multi / n_c if n_c else math.nan, n_c),
            "ratio",
            n_c,
            None,
            note,
        ),
        Metric(
            ds,
            "Text-only: Scam Recall (tat ca)",
            opt(text["scam_recall"], int(text["n_scam"])),
            "ratio",
            int(text["n_scam"]),
            None,
            note,
        ),
        Metric(
            ds,
            "Multimodal: Scam Recall (tat ca)",
            opt(multi["scam_recall"], int(multi["n_scam"])),
            "ratio",
            int(multi["n_scam"]),
            None,
            note,
        ),
    ]


# --------------------------------------------------------------------------- Ablation 3

N_MULE_ACCOUNTS = 20


def _make_settings() -> Settings:
    import base64

    from pydantic import SecretStr

    def b64(b: bytes) -> str:
        return base64.b64encode(b).decode()

    return Settings(
        app_env=AppEnv.TEST,
        database_url=None,
        redis_url=None,
        log_json=False,
        kms_master_kek=SecretStr(b64(bytes(range(32)))),
        kms_master_kek_prev=None,
        ed25519_policy_private_key=SecretStr(b64(bytes([7]) * 32)),
        ed25519_policy_key_id="bench-fake-k1",
        pepper_b2b_phone=SecretStr("bench-phone-pepper-" + "a" * 32),
        pepper_b2b_phone_prev=None,
        pepper_b2b_phone_version=1,
        pepper_mule_account=SecretStr("bench-mule-pepper-" + "c" * 32),
        pepper_mule_account_prev=None,
        pepper_mule_account_version=1,
    )


@dataclass(frozen=True, slots=True)
class MuleOutcome:
    static_blocked: int
    station_blocked_case1: int
    graph_flagged_case2: int
    total: int


async def run_mule_experiment(
    advance_results: Sequence[ContractResult], n_accounts: int = N_MULE_ACCOUNTS
) -> MuleOutcome:
    settings = _make_settings()
    clock = FakeClock()
    service = MuleGraphService(
        InMemoryRepository(),
        BlindIndexer.from_settings(settings),
        EnvelopeCipher(bytes(range(32))),
    )
    # Danh sách đen tĩnh (mô phỏng SIMO/nTrust): chỉ chứa STK đã biết từ trước; 20 STK mồi mới
    # (tuổi 0 giờ) chưa có trong đó.
    static_blacklist = {f"0000900{i:06d}" for i in range(500)}
    accounts = [f"00007{i:08d}" for i in range(n_accounts)]
    static = sum(a in static_blacklist for a in accounts)
    station = 0
    flagged2 = 0
    for i, acct in enumerate(accounts):
        # Ca 1: hành trình Trạm 2 (Advance-Fee/QR) chặn dựa trên hợp đồng mẫu, chưa cần báo cáo nào.
        if advance_results and advance_results[i % len(advance_results)].advance_fee_detected:
            station += 1
        now: datetime = clock.now() if hasattr(clock, "now") else datetime.now(UTC)
        first = await service.report_mule_account(
            bank_bin="970436",
            account_no=acct,
            reporter_device_id=uuid4(),
            channel=EvidenceChannel.VIETQR_CRYPTO_DECODED,
            now=now,
        )
        second = await service.report_mule_account(
            bank_bin="970436",
            account_no=acct,
            reporter_device_id=uuid4(),
            channel=EvidenceChannel.VIETQR_CRYPTO_DECODED,
            now=now,
        )
        _ = first
        flagged2 += second.status == SimoDossierStatus.QUORUM_REACHED
    return MuleOutcome(static, station, flagged2, n_accounts)


def ablation3(out: MuleOutcome, have_advance_samples: bool) -> list[Metric]:
    ds = "Ablation3"
    note = (
        "20 STK moi (tuoi 0h) la TONG HOP; ca 1 ghep voi hop dong advance-fee cua dataset theo vong"
    )
    return [
        Metric(
            ds,
            "Static blacklist: bat duoc / 20",
            float(out.static_blocked),
            "count",
            out.total,
            None,
            note,
        ),
        Metric(
            ds,
            "VayShield Tram 2/3: chan ngay ca 1 / 20",
            float(out.station_blocked_case1) if have_advance_samples else None,
            "count",
            out.total,
            None,
            note if have_advance_samples else "NOT_MEASURED: tap con khong co mau advance-fee",
        ),
        Metric(
            ds,
            "VayShield Mule Graph: STK dat quorum o ca thu 2 / 20",
            float(out.graph_flagged_case2),
            "count",
            out.total,
            Sla(">=", float(out.total)),
            "2 thiet bi doc lap -> QUORUM_REACHED (S2-EC27); InMemoryRepository",
        ),
    ]


# --------------------------------------------------------------------------- Ablation 1 (LIVE)


async def live_ablation1(
    samples: Sequence[dict[str, Any]], api_key: str, max_calls: int
) -> list[Metric]:
    """CHỈ gọi khi người dùng bật BENCH_LIVE=1 + khóa. Gửi dòng tiền TỔNG HỢP tới Gemini thật,
    hỏi APR, đo sai số tương đối. Chưa được kiểm thử bằng mạng thật trong task này."""
    from ai_pipeline.extraction.gemini_client import GenerationRequest, TextPart
    from ai_pipeline.extraction.genai_client import GoogleGenAIClient
    from pydantic import BaseModel, SecretStr

    class AprAnswer(BaseModel):
        apr_percent_per_year: float

    client = GoogleGenAIClient()
    errs: list[float] = []
    used = 0
    for s in samples:
        exp = s["expected_apr_vn_365"]
        if exp is None or exp <= 0 or len(s["cashflows"]) <= 2:
            continue
        if used >= max_calls:
            break
        used += 1
        prompt = (
            "Tinh lai suat nam (APR, %/nam) cua khoan vay co dong tien (ngay, VND; duong = nhan, am = tra): "
            + str([(f["day"], f["amount_vnd"]) for f in s["cashflows"]])
        )
        req = GenerationRequest(
            model="gemini-2.5-flash",
            system_instruction="Chi tra JSON.",
            parts=(TextPart(prompt),),
            response_schema=AprAnswer,
            want_logprobs=False,
        )
        res = await client.generate(req, api_key=SecretStr(api_key))
        try:
            got = AprAnswer.model_validate_json(res.text).apr_percent_per_year
        except ValueError:
            continue
        errs.append(_rel_err_pct(got, exp))
    return [
        Metric(
            "Ablation1-LIVE",
            "Gemini that (khong cong cu) - sai so tuong doi TB (%)",
            sum(errs) / len(errs) if errs else None,
            "%",
            len(errs),
            None,
            f"LIVE, {used} lan goi that, du lieu tong hop",
        )
    ]


__all__ = [
    "MuleOutcome",
    "ablation1",
    "ablation2",
    "ablation3",
    "live_ablation1",
    "run_mule_experiment",
]
