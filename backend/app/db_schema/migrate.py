"""Runner migration idempotent: ``python -m app.db_schema.migrate`` (role ADMIN).

- Áp ``backend/migrations/*.sql`` theo thứ tự tên file, mỗi file đúng 1 lần
  (bảng ``schema_migrations`` lưu checksum). File đã áp mà nội dung đổi → lỗi
  (CLAUDE.md: không sửa migration đã apply). Bản thân SQL cũng idempotent.
- Khóa advisory để nhiều runner song song không đạp nhau.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
BOOTSTRAP_FILE = "bootstrap_roles.sql"
_LOCK_KEY = 7_203_001

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


class MigrationError(RuntimeError):
    pass


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Migration đánh số NNNN_*.sql (bootstrap_roles.sql chạy riêng)."""
    return sorted(directory.glob("[0-9]*.sql"), key=lambda p: p.name)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def run_migrations(admin_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Trả danh sách file vừa được áp (rỗng nếu đã cập nhật)."""
    applied: list[str] = []
    conn = await asyncpg.connect(to_asyncpg_dsn(admin_url))
    try:
        await conn.fetchval("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        await conn.execute(_CREATE_TABLE)
        done = {
            r["filename"]: r["checksum"]
            for r in await conn.fetch("SELECT * FROM schema_migrations")
        }
        bootstrap = directory / BOOTSTRAP_FILE
        if bootstrap.exists():
            await conn.execute(bootstrap.read_text(encoding="utf-8"))
        for path in list_migrations(directory):
            digest = checksum(path)
            if path.name in done:
                if done[path.name] != digest:
                    raise MigrationError(f"{path.name} đã áp dụng nhưng nội dung bị sửa")
                continue
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                path.name,
                digest,
            )
            applied.append(path.name)
        if bootstrap.exists():  # GRANT cho bảng vừa tạo (+ schema_migrations)
            await conn.execute(bootstrap.read_text(encoding="utf-8"))
    finally:
        try:
            if conn.is_in_transaction():
                await conn.execute("ROLLBACK")
            await conn.fetchval("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
        except Exception:  # noqa: S110 - dọn dẹp, không che lỗi gốc
            pass
        finally:
            await conn.close()
    return applied


def main() -> int:
    url = os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("TEST_ADMIN_DATABASE_URL")
    if not url:
        print("Thiếu ADMIN_DATABASE_URL", file=sys.stderr)
        return 2
    for name in asyncio.run(run_migrations(url)):
        print(f"applied {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
