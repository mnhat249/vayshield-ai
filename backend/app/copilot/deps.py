"""Dependency cho bàn làm việc Fraud Ops (test ghi đè bằng ``dependency_overrides``)."""

from __future__ import annotations

from app.copilot.advisory import AdvisoryWriter
from app.copilot.repository import OpsRepository
from app.copilot.repository_pg import SqlOpsRepository
from app.core.db import get_sessionmaker
from app.mule_graph.repository_pg import SqlMuleGraphRepository
from app.security.envelope import EnvelopeCipher, get_envelope_cipher
from app.security.keys import SecurityConfigError


def get_ops_repository() -> OpsRepository:
    """Bản PostgreSQL; chưa cấu hình DB -> 503 (fail-closed)."""
    sessionmaker = get_sessionmaker()
    return SqlOpsRepository(sessionmaker, SqlMuleGraphRepository(sessionmaker))


def get_ops_cipher() -> EnvelopeCipher | None:
    """Thiếu ``KMS_MASTER_KEK`` -> ``None`` (unseal trả 503, các thao tác còn lại vẫn chạy)."""
    try:
        return get_envelope_cipher()
    except SecurityConfigError:
        return None


def get_advisory_writer() -> AdvisoryWriter:
    """Chưa nối LLM: dùng mẫu cứng. Danh sách hotline cho phép rỗng -> chỉ biến mẫu
    ``{{OFFICIAL_HOTLINE}}`` (nhân sự điền từ cấu hình cứng của tổ chức)."""
    return AdvisoryWriter()
