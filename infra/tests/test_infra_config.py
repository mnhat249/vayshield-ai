"""Kiểm tĩnh cấu hình hạ tầng T18 (không cần Docker/gitleaks/k6).

Chạy:  cd backend && uv run pytest -c pyproject.toml ../infra/tests -q -p no:cacheprovider
Các mục cần Docker thật (`docker compose up --build`) KHÔNG được kiểm ở đây.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"

# Biến chỉ phục vụ hạ tầng/entrypoint, không nằm trong Settings.
INFRA_ONLY_ENV = {
    "DB_ADMIN_PASSWORD",
    "DB_PASSWORD",
    "PUBLIC_DOMAIN",
    "FORWARDED_ALLOW_IPS",
    "PORT",
    "WEB_CONCURRENCY",
    "UVICORN_KEEPALIVE_S",
    "PROBE_URL",
    "REDIS_PASSWORD",
    "PROBE_INTERVAL_S",
    "UV_LINK_MODE",
    "UV_PROJECT_ENVIRONMENT",
}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _settings_env_names() -> set[str]:
    tree = ast.parse((ROOT / "backend/app/core/config.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                n.target.id.upper()
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
    raise AssertionError("không thấy class Settings")


COMPOSES = [ROOT / "docker-compose.yml", INFRA / "docker-compose.prod.yml"]


@pytest.mark.parametrize(
    "path", [*COMPOSES, INFRA / "docker-compose.dev.yml"], ids=lambda p: p.name
)
def test_compose_yaml_hop_le(path: Path) -> None:
    data = _load(path)
    assert "vayshield-backend" in data["services"]


def test_ec_ops04_dockerfile_linux_amd64_va_khong_root() -> None:
    text = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"^FROM (.+)$", text, flags=re.M)
    assert froms and all("--platform=linux/amd64" in f for f in froms)
    assert re.search(r"^USER vayshield$", text, flags=re.M)  # không chạy root
    for path in COMPOSES:
        assert _load(path)["services"]["vayshield-backend"]["platform"] == "linux/amd64"


def test_ec_ops03_gitattributes_ep_lf_va_dockerfile_sed() -> None:
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attrs
    for ext in ("sh", "lua", "sql"):
        assert re.search(rf"^\*\.{ext}\s+text eol=lf$", attrs, flags=re.M), ext
    assert "sed -i 's/\\r$//g'" in (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")


def test_ec_ops03_lua_sql_trong_index_la_lf() -> None:
    """Kiểm nội dung trong index git (i/lf). Bản working tree có thể CRLF do autocrlf cục bộ."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "--eol", "--", "*.lua", "*.sql", "*.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    assert out
    bad = [line for line in out if not line.startswith("i/lf") and not line.startswith("i/none")]
    assert bad == []
    assert all("eol=lf" in line for line in out)  # .gitattributes đã áp cho từng file


def test_ec_ops19_dockerfile_dong_bang_uv_lock() -> None:
    text = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "COPY pyproject.toml uv.lock" in text
    assert "uv sync --frozen" in text


def test_ec_ops05_prod_chi_mo_443_va_khong_lo_db_redis() -> None:
    services = _load(INFRA / "docker-compose.prod.yml")["services"]
    published = {name: svc.get("ports", []) for name, svc in services.items() if svc.get("ports")}
    assert set(published) == {"caddy"}
    assert sorted(published["caddy"]) == ["443:443", "80:80"]
    assert ":8000" not in json.dumps(published)
    caddy = (INFRA / "Caddyfile").read_text(encoding="utf-8")
    assert "flush_interval -1" in caddy  # SSE không gom gói
    assert "X-Accel-Buffering" in caddy


def test_ec_ops18_www_redirect_308() -> None:
    caddy = (INFRA / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"www\.\{\$PUBLIC_DOMAIN\}\s*\{\s*redir [^\n]+ 308", caddy)


def test_ec_ops13_log_rotation_moi_service() -> None:
    for path in COMPOSES:
        for name, svc in _load(path)["services"].items():
            opts = svc["logging"]["options"]
            assert (opts["max-size"], opts["max-file"]) == ("50m", "3"), (
                path.name,
                name,
            )


def test_prod_secret_bat_buoc_va_khong_gia_tri_mac_dinh() -> None:
    env = _load(INFRA / "docker-compose.prod.yml")["services"]["vayshield-backend"]["environment"]
    for key in (
        "DATABASE_URL",
        "PEPPER_B2B_PHONE",
        "PEPPER_MULE_ACCOUNT",
        "KMS_MASTER_KEK",
        "ED25519_POLICY_PRIVATE_KEY",
        "JWT_OPS_SECRET",
    ):
        assert ":?" in env[key], key
    assert env["APP_ENV"] == "production"
    assert env["EXPOSE_ERROR_DETAILS"] == "false"
    assert env["CORS_ORIGINS"].startswith("${CORS_ORIGINS:?")  # bắt buộc khai báo, không mặc định *
    # allowlist proxy: CIDR cụ thể, không "*"
    assert env["FORWARDED_ALLOW_IPS"] != "*"
    caddy = _load(INFRA / "docker-compose.prod.yml")["services"]["caddy"]
    assert env["FORWARDED_ALLOW_IPS"] == caddy["networks"]["edge"]["ipv4_address"]


def test_ec_ops22_url_cong_khai_la_https() -> None:
    env = _load(INFRA / "docker-compose.prod.yml")["services"]["vayshield-backend"]["environment"]
    assert env["APP_PUBLIC_BASE_URL"].startswith("https://")
    assert not re.search(r"\bws://", (INFRA / "cloudrun/service.yaml").read_text(encoding="utf-8"))


def test_ten_bien_moi_truong_khop_config_py() -> None:
    known = _settings_env_names() | INFRA_ONLY_ENV
    used: set[str] = set()
    for path in (*COMPOSES, INFRA / "docker-compose.dev.yml"):
        env = _load(path)["services"]["vayshield-backend"].get("environment", {})
        used |= set(env)
    cr = _load(INFRA / "cloudrun/service.yaml")
    container = cr["spec"]["template"]["spec"]["containers"][0]
    used |= {e["name"] for e in container["env"]}
    for line in (INFRA / "env.sample").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            used.add(line.split("=", 1)[0])
    assert used - known == set(), used - known


def test_cloud_run_zero_cold_start_va_probe() -> None:
    svc = _load(INFRA / "cloudrun/service.yaml")
    ann = svc["spec"]["template"]["metadata"]["annotations"]
    assert ann["autoscaling.knative.dev/minScale"] == "1"
    assert ann["run.googleapis.com/cpu-throttling"] == "false"
    container = svc["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"]["httpGet"]["path"] == "/healthz"
    # Không secret inline: mọi biến nhạy cảm đi qua secretKeyRef
    for e in container["env"]:
        if e["name"] in {
            "DATABASE_URL",
            "JWT_OPS_SECRET",
            "KMS_MASTER_KEK",
            "GEMINI_API_KEYS",
        }:
            assert "value" not in e and "valueFrom" in e, e["name"]
    assert "<PROJECT>" in container["image"]  # placeholder, chưa trỏ tài nguyên thật


def test_railway_khong_ngu_va_healthcheck() -> None:
    cfg = json.loads((INFRA / "railway/railway.json").read_text(encoding="utf-8"))
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["deploy"]["healthcheckPath"] == "/healthz"
    assert cfg["deploy"]["numReplicas"] >= 1
    assert cfg["deploy"]["sleepApplication"] is False


def test_ec_ops11_keepwarm_180s() -> None:
    svc = _load(INFRA / "docker-compose.prod.yml")["services"]["keepwarm"]
    assert svc["environment"]["PROBE_INTERVAL_S"] == "180"
    assert svc["environment"]["PROBE_URL"].endswith("/healthz")
    probe = (INFRA / "scripts/probe.py").read_text(encoding="utf-8")
    assert "DEFAULT_INTERVAL_S = 180.0" in probe


def test_probe_readiness_degraded_vs_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """healthz luôn 200: liveness ok khi degraded, nhưng readiness (--ready) phải fail."""
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("probe", INFRA / "scripts/probe.py")
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_open(body: bytes) -> Any:
        return lambda url, timeout: Resp(body)

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_open(b'{"status":"degraded"}'))
    assert probe.check("http://x/healthz", ready=False)[0] is True
    assert probe.check("http://x/healthz", ready=True)[0] is False
    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_open(b'{"status":"ok"}'))
    assert probe.check("http://x/healthz", ready=True)[0] is True
    assert probe.check("file:///etc/passwd", ready=False)[0] is False


def _scan_module() -> Any:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("secret_scan", INFRA / "scripts/secret_scan.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["secret_scan"] = mod  # dataclass cần module đã đăng ký
    spec.loader.exec_module(mod)
    return mod


def test_ec_ops01_gitleaks_toml_hop_le_va_allowlist_hep() -> None:
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert cfg["extend"]["useDefault"] is True
    allow = cfg["allowlist"]
    broad = [p for p in allow["paths"] if re.search(r"(backend|app|src)", p) and "env" not in p]
    assert broad == []  # không nuốt thư mục mã nguồn
    # Không allowlist theo path file mẫu: dán khóa thật vào .env.example/env.sample vẫn phải bị bắt
    assert not any("env" in p for p in allow["paths"])
    assert not any(r in (".*", ".+") for r in allow["regexes"])


def test_ec_ops01_scanner_bat_khoa_google_gia_lap_va_bo_qua_placeholder() -> None:
    mod = _scan_module()
    cfg = mod.load_config(ROOT / ".gitleaks.toml")
    fake_google = "AIzaSy" + "A" * 33
    assert mod.scan_text("a.py", f'KEY = "{fake_google}"', cfg)
    assert not mod.scan_text("a.py", 'KEY = "AIzaSyFAKE' + "0" * 27 + '"', cfg)
    dsn = "postgresql://" + "u:" + "Sup3rRealPw" + "@db.prod.internal:5432/x"
    assert mod.scan_text("a.py", dsn, cfg)
    assert not mod.scan_text("a.yml", "postgresql://u:${DB_PASSWORD:?x}@db:5432/x", cfg)
    assert mod.scan_text("k.txt", "-----BEGIN " + "PRIVATE KEY-----", cfg)


def test_ec_ops01_repo_khong_co_secret_va_khong_co_env_that() -> None:
    mod = _scan_module()
    cfg = mod.load_config(ROOT / ".gitleaks.toml")
    findings = mod.scan_repo(ROOT, cfg)
    assert findings == [], [f"{f.path}:{f.line}:{f.rule}" for f in findings]


def test_k6_script_20_phien_va_nguong() -> None:
    js = (ROOT / "loadtest/k6_judges.js").read_text(encoding="utf-8")
    assert "VUS || '20'" in js
    assert "namespace_leak: ['rate==0']" in js
    assert "p(95)<250" in js


def test_ec_ops01_khoa_that_dan_vao_env_sample_van_bi_bat() -> None:
    mod = _scan_module()
    cfg = mod.load_config(ROOT / ".gitleaks.toml")
    real_like = "GEMINI_API_KEYS=AIzaSy" + "B" * 33
    assert mod.scan_text("infra/env.sample", real_like, cfg)
    assert mod.scan_text(".env.example", real_like, cfg)
    # file mẫu hiện tại sạch
    sample = (INFRA / "env.sample").read_text(encoding="utf-8")
    assert mod.scan_text("infra/env.sample", sample, cfg) == []


def test_compose_judge_co_khoa_dev_hop_le_va_signer_khoi_tao_duoc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings từ env của compose gốc phải dựng được signer/KEK/blind index (healthz ok)."""
    from app.core.config import Settings
    from app.security.blind_index import BlindIndexer
    from app.security.envelope import EnvelopeCipher
    from app.security.signing import Ed25519Signer

    env = _load(ROOT / "docker-compose.yml")["services"]["vayshield-backend"]["environment"]
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    cfg = Settings()
    assert Ed25519Signer.from_settings(cfg).key_id
    assert BlindIndexer.from_settings(cfg) is not None
    assert EnvelopeCipher.from_settings(cfg) is not None
    assert cfg.app_env.value == "local"  # khóa dev KHÔNG đi cùng APP_ENV=production


def test_khoa_dev_bi_preflight_tu_choi() -> None:
    pre = _load_script("preflight")
    env = _load(ROOT / "docker-compose.yml")["services"]["vayshield-backend"]["environment"]
    dev = {k: str(env[k]) for k in pre.B64_32 + pre.STR_32}
    errors = pre.check(dev)
    for key in pre.B64_32 + pre.STR_32:
        assert any(e.startswith(key) for e in errors), key
    assert all("DEV-ONLY" not in e and "dev-only" not in e for e in errors)  # không in giá trị


def test_preflight_chap_nhan_gia_tri_hop_le_va_bat_loi_dinh_dang() -> None:
    import base64

    pre = _load_script("preflight")
    good = {
        "KMS_MASTER_KEK": base64.b64encode(bytes(range(32))).decode(),
        "ED25519_POLICY_PRIVATE_KEY": base64.b64encode(bytes(range(1, 33))).decode(),
        "PEPPER_B2B_PHONE": "p" * 40,
        "PEPPER_MULE_ACCOUNT": "q" * 40,
        "JWT_OPS_SECRET": "j" * 40,
        "DB_ADMIN_PASSWORD": "Adm1nRealPw",
        "DB_PASSWORD": "AppRealPw1",
        "REDIS_PASSWORD": "RedisRealPw1",
        "CORS_ORIGINS": "https://demo.real.vn",
        "PUBLIC_DOMAIN": "demo.real.vn",
    }
    assert pre.check(good) == []
    bad = {
        **good,
        "KMS_MASTER_KEK": base64.b64encode(b"short").decode(),
        "JWT_OPS_SECRET": "x" * 10,
    }
    errs = pre.check(bad)
    assert any(e.startswith("KMS_MASTER_KEK") for e in errs)
    assert any(e.startswith("JWT_OPS_SECRET") for e in errs)
    assert any(e.startswith("CORS_ORIGINS") for e in pre.check({**good, "CORS_ORIGINS": "*"}))
    assert any(
        e.startswith("JUDGE_OPS")
        for e in pre.check({**good, "JUDGE_OPS_QUICK_LOGIN_CODE": "short"})
    )
    assert pre.check(
        pre.parse_env((INFRA / "env.sample").read_text(encoding="utf-8"))
    )  # mẫu bị từ chối


def test_db_seed_compose_judge_va_prod_chi_khi_co_co() -> None:
    root = _load(ROOT / "docker-compose.yml")["services"]
    assert root["db-seed"]["entrypoint"] == ["python", "-m", "seeds.run"]
    assert "ADMIN_DATABASE_URL" in root["db-seed"]["environment"]
    assert "db-init" in root["db-seed"]["depends_on"]
    assert "db-seed" in root["vayshield-backend"]["depends_on"]
    prod = _load(INFRA / "docker-compose.prod.yml")["services"]
    assert prod["db-seed"]["profiles"] == ["seed"]  # chỉ chạy khi --profile seed
    assert "db-seed" not in prod["vayshield-backend"]["depends_on"]


def test_prod_tach_mang_edge_internal_va_redis_requirepass() -> None:
    prod = _load(INFRA / "docker-compose.prod.yml")
    assert prod["networks"]["internal"]["internal"] is True
    svcs = prod["services"]
    assert set(svcs["vayshield-backend"]["networks"]) == {"edge", "internal"}
    assert svcs["postgres-pgvector"]["networks"] == ["internal"]
    assert svcs["redis-hub"]["networks"] == ["internal"]
    assert "--requirepass" in svcs["redis-hub"]["command"]
    assert svcs["vayshield-backend"]["environment"]["REDIS_URL"].startswith("redis://:")


def test_railway_dockerfile_path_khop_root_directory_backend() -> None:
    cfg = json.loads((INFRA / "railway/railway.json").read_text(encoding="utf-8"))
    assert cfg["build"]["dockerfilePath"] == "Dockerfile"  # Root Directory = backend (README)
    assert "Root Directory = `backend`" in (INFRA / "README.md").read_text(encoding="utf-8")


def test_dockerfile_app_chi_doc_cho_user_runtime() -> None:
    text = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    assert "chown -R vayshield" not in text
    assert "chmod -R a-w,a+rX /app" in text


def test_k6_moi_vu_mot_phien_va_429_hop_le() -> None:
    js = (ROOT / "loadtest/k6_judges.js").read_text(encoding="utf-8")
    assert "export function setup()" in js
    assert "expectedStatuses(200, 429)" in js
    create = js.index("/api/v1/sandbox/sessions")
    assert js.index("export default function") > create  # tạo phiên chỉ ở setup(), trước default
    assert "/api/v1/sandbox/sessions" not in js[js.index("export default function") :]


def _load_script(name: str) -> Any:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, INFRA / f"scripts/{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
