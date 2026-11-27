"""JWT Fraud Ops (File 4 §4.1, EC-OPS15) — HS256 bằng ``JWT_OPS_SECRET``.

- Access token ngắn (mặc định 15 phút); refresh token 30 ngày, xoay vòng mỗi lần dùng.
- Tài khoản Giám khảo VIP (``judge_quick_login``, bằng mã ``JUDGE_OPS_QUICK_LOGIN_CODE``): access
  token dài hơn (``JUDGE_ACCESS_TTL_S``) để không bị văng giữa buổi chấm; client còn tự gia hạn
  ngầm bằng refresh token 30 ngày (EC-OPS15).
- Hạn token kiểm bằng ``Clock`` (test tất định), không dùng đồng hồ thật của PyJWT.
- Thiếu/yếu ``JWT_OPS_SECRET`` -> 503 (fail-closed), không bao giờ chấp nhận token.
- Mật khẩu: ``OpsUserDirectory`` (Protocol). Mặc định thư mục RỖNG (chưa nối kho tài khoản) ->
  grant ``password`` luôn 401.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Annotated, Final, Protocol

import jwt
from fastapi import Depends

from app.core.auth_headers import OpsCaller
from app.core.clock import Clock, get_clock
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.schemas.ops import OpsTokenRequest, OpsTokenResponse

ROLE_FRAUD_ANALYST: Final = "FRAUD_ANALYST"
JUDGE_SUBJECT: Final = "judge.ops@vayshield.vn"
JUDGE_ACCESS_TTL_S: Final = 4 * 3600
MIN_SECRET_BYTES: Final = 32
_ALG: Final = "HS256"
_PBKDF2_ITERATIONS: Final = 210_000


@dataclass(frozen=True, slots=True)
class OpsPrincipal:
    subject: str
    roles: tuple[str, ...]
    is_judge: bool = False

    def __repr__(self) -> str:
        return f"OpsPrincipal(roles={self.roles}, is_judge={self.is_judge})"


@dataclass(frozen=True, slots=True)
class OpsUser:
    subject: str
    roles: tuple[str, ...]


class OpsUserDirectory(Protocol):
    async def verify_password(self, username: str, password: str) -> OpsUser | None: ...
    async def get_user(self, subject: str) -> OpsUser | None: ...


def hash_password(password: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


class InMemoryOpsUserDirectory:
    """Thư mục tài khoản trong bộ nhớ (PBKDF2-SHA256, so sánh hằng thời gian)."""

    def __init__(self, *, iterations: int = _PBKDF2_ITERATIONS) -> None:
        self._iterations = iterations
        self._users: dict[str, tuple[bytes, bytes, OpsUser]] = {}
        self._dummy_salt = b"vayshield-dummy-salt"

    def add(self, username: str, password: str, roles: tuple[str, ...], salt: bytes) -> None:
        digest = hash_password(password, salt, self._iterations)
        self._users[username] = (salt, digest, OpsUser(subject=username, roles=roles))

    async def verify_password(self, username: str, password: str) -> OpsUser | None:
        entry = self._users.get(username)
        salt, expected = (entry[0], entry[1]) if entry else (self._dummy_salt, b"\0" * 32)
        actual = hash_password(password, salt, self._iterations)  # luôn băm: cân bằng thời gian
        if entry is not None and hmac.compare_digest(actual, expected):
            return entry[2]
        return None

    async def get_user(self, subject: str) -> OpsUser | None:
        entry = self._users.get(subject)
        return entry[2] if entry else None


class _EmptyDirectory:
    async def verify_password(self, username: str, password: str) -> OpsUser | None:
        return None

    async def get_user(self, subject: str) -> OpsUser | None:
        return None


def get_ops_directory() -> OpsUserDirectory:
    """Dependency; mặc định rỗng cho tới khi nối kho tài khoản Fraud Ops (ghi đè ở nơi nối dây)."""
    return _EmptyDirectory()


def _unauthorized(msg: str | None = None) -> AppError:
    return AppError(ErrorCode.UNAUTHORIZED, 401, msg)


class OpsTokenService:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        access_ttl_s: int,
        refresh_ttl_s: int,
        clock: Clock,
        directory: OpsUserDirectory,
        judge_code: str | None,
        judge_access_ttl_s: int = JUDGE_ACCESS_TTL_S,
    ) -> None:
        if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
            raise ValueError("JWT_OPS_SECRET quá ngắn")
        self._secret = secret
        self._issuer = issuer
        self._access_ttl = access_ttl_s
        self._refresh_ttl = refresh_ttl_s
        self._clock = clock
        self._directory = directory
        self._judge_code = judge_code
        self._judge_access_ttl = judge_access_ttl_s

    # --- phát hành -------------------------------------------------------------------------
    def _encode(self, typ: str, principal: OpsPrincipal, ttl_s: int) -> str:
        now = int(self._clock.now().timestamp())
        claims = {
            "iss": self._issuer,
            "sub": principal.subject,
            "typ": typ,
            "roles": list(principal.roles),
            "jdg": principal.is_judge,
            "iat": now,
            "exp": now + ttl_s,
        }
        return jwt.encode(claims, self._secret, algorithm=_ALG)

    def issue(self, principal: OpsPrincipal) -> OpsTokenResponse:
        access_ttl = self._judge_access_ttl if principal.is_judge else self._access_ttl
        return OpsTokenResponse(
            access_token=self._encode("access", principal, access_ttl),
            token_type="bearer",  # noqa: S106 - loại token OAuth, không phải bí mật
            expires_in=access_ttl,
            refresh_token=self._encode("refresh", principal, self._refresh_ttl),
            refresh_expires_in=self._refresh_ttl,
            roles=list(principal.roles),
        )

    async def exchange(self, req: OpsTokenRequest) -> OpsTokenResponse:
        if req.grant_type == "password":
            if not req.username or not req.password:
                raise _unauthorized()
            user = await self._directory.verify_password(req.username, req.password)
            if user is None:
                raise _unauthorized("Sai tên đăng nhập hoặc mật khẩu.")
            return self.issue(OpsPrincipal(user.subject, user.roles))
        if req.grant_type == "judge_quick_login":
            return self.issue(self._judge_principal(req.judge_access_code))
        if not req.refresh_token:
            raise _unauthorized()
        return self.issue(await self._principal_from_refresh(req.refresh_token))

    def _judge_principal(self, code: str | None) -> OpsPrincipal:
        if not self._judge_code or not code:
            raise _unauthorized("Mã đăng nhập nhanh không hợp lệ.")
        if not hmac.compare_digest(code.encode("utf-8"), self._judge_code.encode("utf-8")):
            raise _unauthorized("Mã đăng nhập nhanh không hợp lệ.")
        return OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True)

    # --- xác minh --------------------------------------------------------------------------
    def _decode(self, token: str, expected_typ: str) -> dict[str, object]:
        try:
            claims: dict[str, object] = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALG],  # cố định thuật toán: chặn alg=none / hạ cấp
                issuer=self._issuer,
                options={"verify_exp": False, "require": ["exp", "iat", "sub", "typ", "iss"]},
            )
        except jwt.PyJWTError:
            raise _unauthorized() from None
        exp = claims.get("exp")
        if (
            claims.get("typ") != expected_typ
            or not isinstance(exp, int)
            or exp <= int(self._clock.now().timestamp())
        ):
            raise _unauthorized()
        return claims

    def verify_access(self, token: str) -> OpsPrincipal:
        claims = self._decode(token, "access")
        roles = claims.get("roles")
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise _unauthorized()
        return OpsPrincipal(
            subject=str(claims["sub"]),
            roles=tuple(str(r) for r in roles),
            is_judge=claims.get("jdg") is True,
        )

    async def _principal_from_refresh(self, token: str) -> OpsPrincipal:
        claims = self._decode(token, "refresh")
        subject = str(claims["sub"])
        if claims.get("jdg") is True:
            # Giám khảo: chỉ còn hiệu lực khi mã đăng nhập nhanh vẫn được bật (thu hồi = xóa mã).
            if not self._judge_code or subject != JUDGE_SUBJECT:
                raise _unauthorized()
            return OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True)
        user = await self._directory.get_user(subject)  # quyền lấy từ kho, không tin token cũ
        if user is None:
            raise _unauthorized()
        return OpsPrincipal(user.subject, user.roles)


def get_ops_token_service(
    clock: Annotated[Clock, Depends(get_clock)],
    directory: Annotated[OpsUserDirectory, Depends(get_ops_directory)],
) -> OpsTokenService:
    settings = get_settings()
    secret = settings.jwt_ops_secret.get_secret_value() if settings.jwt_ops_secret else None
    if not secret or len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise AppError(
            ErrorCode.SERVICE_DEGRADED, 503, "Dịch vụ đăng nhập Fraud Ops chưa cấu hình."
        )
    judge = settings.judge_ops_quick_login_code
    return OpsTokenService(
        secret=secret,
        issuer=settings.jwt_ops_issuer,
        access_ttl_s=settings.jwt_ops_access_ttl_s,
        refresh_ttl_s=settings.jwt_ops_refresh_ttl_s,
        clock=clock,
        directory=directory,
        judge_code=judge.get_secret_value() if judge else None,
    )


def ops_principal(
    creds: OpsCaller, tokens: Annotated[OpsTokenService, Depends(get_ops_token_service)]
) -> OpsPrincipal:
    """JWT hợp lệ (chưa kiểm vai trò) — 401 nếu sai/hết hạn."""
    return tokens.verify_access(creds.bearer_token)


def analyst_principal(
    principal: Annotated[OpsPrincipal, Depends(ops_principal)],
) -> OpsPrincipal:
    """RBAC: bắt buộc vai trò ``FRAUD_ANALYST`` — thiếu -> 403."""
    if ROLE_FRAUD_ANALYST not in principal.roles:
        raise AppError(ErrorCode.FORBIDDEN, 403)
    return principal


AnalystCaller = Annotated[OpsPrincipal, Depends(analyst_principal)]

__all__ = [
    "JUDGE_ACCESS_TTL_S",
    "JUDGE_SUBJECT",
    "ROLE_FRAUD_ANALYST",
    "AnalystCaller",
    "InMemoryOpsUserDirectory",
    "OpsPrincipal",
    "OpsTokenService",
    "OpsUser",
    "OpsUserDirectory",
    "analyst_principal",
    "get_ops_directory",
    "get_ops_token_service",
    "ops_principal",
]
