"""JWT Fraud Ops (File 4 §4.1, EC-OPS15) — HS256 bằng ``JWT_OPS_SECRET``.

- Access token ngắn (mặc định 15 phút); refresh token 30 ngày, xoay vòng mỗi lần dùng.
- Tài khoản Giám khảo VIP (``judge_quick_login``, bằng mã ``JUDGE_OPS_QUICK_LOGIN_CODE``): access
  token dài hơn (``JUDGE_ACCESS_TTL_S``) để không bị văng giữa buổi chấm; client còn tự gia hạn
  ngầm bằng refresh token 30 ngày (EC-OPS15).
- Hạn token kiểm bằng ``Clock`` (test tất định), không dùng đồng hồ thật của PyJWT; ``iat`` tương
  lai được dung sai ``IAT_LEEWAY_S`` (lệch đồng hồ giữa các node).
- Refresh token có ``jti`` + ``sid`` (id phiên/họ token): dùng đúng 1 lần (``RefreshStateStore``,
  Redis ``SET NX``); dùng lại token cũ = nghi bị đánh cắp -> thu hồi cả họ. Redis lỗi -> 503
  (fail-closed). Token giám khảo mang ``ver`` = băm của mã hiện hành: đổi mã = mọi token cũ chết.
- Thiếu/yếu ``JWT_OPS_SECRET`` -> 503 (fail-closed), không bao giờ chấp nhận token.
- Mật khẩu: ``OpsUserDirectory`` (Protocol). Mặc định thư mục RỖNG (chưa nối kho tài khoản) ->
  grant ``password`` luôn 401.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Final, Protocol

import jwt
from fastapi import Depends
from redis.asyncio import Redis

from app.core.auth_headers import OpsCaller
from app.core.clock import Clock, get_clock
from app.core.config import AppEnv, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_redis
from app.core.schemas.ops import OpsTokenRequest, OpsTokenResponse

logger = logging.getLogger(__name__)

ROLE_FRAUD_ANALYST: Final = "FRAUD_ANALYST"
JUDGE_SUBJECT: Final = "judge.ops@vayshield.vn"
JUDGE_ACCESS_TTL_S: Final = 4 * 3600
MIN_SECRET_BYTES: Final = 32
_ALG: Final = "HS256"
_PBKDF2_ITERATIONS: Final = 210_000
IAT_LEEWAY_S: Final = 30
MIN_JUDGE_CODE_LEN_PROD: Final = 16
_RT_USED_PREFIX: Final = "vs:ops:rt:used:"
_RT_FAMILY_PREFIX: Final = "vs:ops:rt:revoked:"


@dataclass(frozen=True, slots=True)
class OpsPrincipal:
    subject: str
    roles: tuple[str, ...]
    is_judge: bool = False
    session_id: str = ""  # sid của họ token: phân biệt từng phiên (kể cả các giám khảo dùng chung)

    @property
    def audit_actor(self) -> str:
        """Định danh ghi nhật ký kiểm toán: giám khảo kèm phiên để không dùng chung 1 actor."""
        if self.is_judge and self.session_id:
            return f"{self.subject}#{self.session_id[:12]}"
        return self.subject

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


class RefreshStateStore(Protocol):
    """Trạng thái refresh token: dùng-một-lần + thu hồi theo họ (sid)."""

    async def consume(self, jti: str, ttl_s: int) -> bool:
        """True nếu đây là lần dùng đầu tiên của ``jti``."""
        ...

    async def revoke_family(self, sid: str, ttl_s: int) -> None: ...

    async def is_family_revoked(self, sid: str) -> bool: ...


class InMemoryRefreshStateStore:
    """Cho test/dev một tiến trình (không dùng ở production nhiều worker)."""

    def __init__(self) -> None:
        self._used: set[str] = set()
        self._revoked: set[str] = set()

    async def consume(self, jti: str, ttl_s: int) -> bool:
        if jti in self._used:
            return False
        self._used.add(jti)
        return True

    async def revoke_family(self, sid: str, ttl_s: int) -> None:
        self._revoked.add(sid)

    async def is_family_revoked(self, sid: str) -> bool:
        return sid in self._revoked


class RedisRefreshStateStore:
    """Redis ``SET NX EX``; mọi lỗi Redis -> 503 (fail-closed, không bao giờ cho qua)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _degraded() -> AppError:
        logger.warning("ops_auth: Redis lỗi — từ chối refresh (fail-closed)")
        return AppError(ErrorCode.SERVICE_DEGRADED, 503)

    async def consume(self, jti: str, ttl_s: int) -> bool:
        try:
            return bool(
                await self._redis.set(_RT_USED_PREFIX + jti, "1", ex=max(1, ttl_s), nx=True)
            )
        except Exception:
            raise self._degraded() from None

    async def revoke_family(self, sid: str, ttl_s: int) -> None:
        try:
            await self._redis.set(_RT_FAMILY_PREFIX + sid, "1", ex=max(1, ttl_s))
        except Exception:
            raise self._degraded() from None

    async def is_family_revoked(self, sid: str) -> bool:
        try:
            return bool(await self._redis.exists(_RT_FAMILY_PREFIX + sid))
        except Exception:
            raise self._degraded() from None


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
        state: RefreshStateStore | None = None,
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
        self._state: RefreshStateStore = state or InMemoryRefreshStateStore()

    def _judge_ver(self) -> str:
        """Băm (có khóa) của mã judge hiện hành: đổi mã -> ``ver`` đổi -> token cũ vô hiệu."""
        if not self._judge_code:
            return ""
        return hmac.new(
            self._secret.encode("utf-8"), b"judge-ver:" + self._judge_code.encode("utf-8"), "sha256"
        ).hexdigest()[:16]

    # --- phát hành -------------------------------------------------------------------------
    def _encode(self, typ: str, principal: OpsPrincipal, ttl_s: int, sid: str) -> str:
        now = int(self._clock.now().timestamp())
        claims: dict[str, object] = {
            "iss": self._issuer,
            "sub": principal.subject,
            "typ": typ,
            "roles": list(principal.roles),
            "jdg": principal.is_judge,
            "sid": sid,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + ttl_s,
        }
        if principal.is_judge:
            claims["ver"] = self._judge_ver()
        return jwt.encode(claims, self._secret, algorithm=_ALG)

    def issue(self, principal: OpsPrincipal) -> OpsTokenResponse:
        access_ttl = self._judge_access_ttl if principal.is_judge else self._access_ttl
        sid = principal.session_id or uuid.uuid4().hex
        return OpsTokenResponse(
            access_token=self._encode("access", principal, access_ttl, sid),
            token_type="bearer",  # noqa: S106 - loại token OAuth, không phải bí mật
            expires_in=access_ttl,
            refresh_token=self._encode("refresh", principal, self._refresh_ttl, sid),
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
                options={
                    "verify_exp": False,
                    "verify_iat": False,  # tự kiểm bằng Clock + dung sai (lệch đồng hồ giữa node)
                    "require": ["exp", "iat", "sub", "typ", "iss"],
                },
            )
        except jwt.PyJWTError:
            raise _unauthorized() from None
        exp, iat = claims.get("exp"), claims.get("iat")
        now = int(self._clock.now().timestamp())
        if (
            claims.get("typ") != expected_typ
            or not isinstance(exp, int)
            or not isinstance(iat, int)
            or exp <= now
            or iat > now + IAT_LEEWAY_S
        ):
            raise _unauthorized()
        return claims

    def _judge_claims_valid(self, claims: dict[str, object]) -> bool:
        ver = claims.get("ver")
        return (
            bool(self._judge_code)
            and claims.get("sub") == JUDGE_SUBJECT
            and isinstance(ver, str)
            and hmac.compare_digest(ver.encode("utf-8"), self._judge_ver().encode("utf-8"))
        )

    def verify_access(self, token: str) -> OpsPrincipal:
        claims = self._decode(token, "access")
        roles = claims.get("roles")
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise _unauthorized()
        is_judge = claims.get("jdg") is True
        if is_judge and not self._judge_claims_valid(claims):
            raise _unauthorized()  # mã judge đã đổi/tắt -> token cũ chết ngay
        return OpsPrincipal(
            subject=str(claims["sub"]),
            roles=tuple(str(r) for r in roles),
            is_judge=is_judge,
            session_id=str(claims.get("sid", "")),
        )

    async def _principal_from_refresh(self, token: str) -> OpsPrincipal:
        claims = self._decode(token, "refresh")
        jti, sid = claims.get("jti"), claims.get("sid")
        if not isinstance(jti, str) or not jti or not isinstance(sid, str) or not sid:
            raise _unauthorized()  # token không có jti/sid (cũ/giả) không được đổi
        if await self._state.is_family_revoked(sid):
            raise _unauthorized()
        if not await self._state.consume(jti, self._refresh_ttl):
            # Dùng lại token đã xoay vòng = nghi bị đánh cắp: thu hồi cả họ.
            await self._state.revoke_family(sid, self._refresh_ttl)
            raise _unauthorized()
        subject = str(claims["sub"])
        if claims.get("jdg") is True:
            # Giám khảo: chỉ hiệu lực khi mã đăng nhập nhanh còn bật VÀ chưa đổi (ver khớp).
            if not self._judge_claims_valid(claims):
                raise _unauthorized()
            return OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True, session_id=sid)
        user = await self._directory.get_user(subject)  # quyền lấy từ kho, không tin token cũ
        if user is None:
            raise _unauthorized()
        return OpsPrincipal(user.subject, user.roles, session_id=sid)


def get_ops_token_service(
    clock: Annotated[Clock, Depends(get_clock)],
    directory: Annotated[OpsUserDirectory, Depends(get_ops_directory)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> OpsTokenService:
    settings = get_settings()
    secret = settings.jwt_ops_secret.get_secret_value() if settings.jwt_ops_secret else None
    if not secret or len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise AppError(
            ErrorCode.SERVICE_DEGRADED, 503, "Dịch vụ đăng nhập Fraud Ops chưa cấu hình."
        )
    judge = settings.judge_ops_quick_login_code
    judge_code = judge.get_secret_value() if judge else None
    if (
        judge_code is not None
        and settings.app_env is AppEnv.PRODUCTION
        and len(judge_code) < MIN_JUDGE_CODE_LEN_PROD
    ):
        logger.warning("ops_auth: mã judge < %d ký tự ở production — tắt", MIN_JUDGE_CODE_LEN_PROD)
        judge_code = None  # fail-closed: mã yếu không bao giờ được chấp nhận
    return OpsTokenService(
        secret=secret,
        issuer=settings.jwt_ops_issuer,
        access_ttl_s=settings.jwt_ops_access_ttl_s,
        refresh_ttl_s=settings.jwt_ops_refresh_ttl_s,
        clock=clock,
        directory=directory,
        judge_code=judge_code,
        state=RedisRefreshStateStore(redis),
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
    "IAT_LEEWAY_S",
    "JUDGE_ACCESS_TTL_S",
    "JUDGE_SUBJECT",
    "ROLE_FRAUD_ANALYST",
    "AnalystCaller",
    "InMemoryOpsUserDirectory",
    "InMemoryRefreshStateStore",
    "OpsPrincipal",
    "OpsTokenService",
    "OpsUser",
    "OpsUserDirectory",
    "RedisRefreshStateStore",
    "RefreshStateStore",
    "analyst_principal",
    "get_ops_directory",
    "get_ops_token_service",
    "ops_principal",
]
