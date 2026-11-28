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
    subnet = _load(INFRA / "docker-compose.prod.yml")["networks"]["edge"]["ipam"]["config"][0]
    assert env["FORWARDED_ALLOW_IPS"] == subnet["subnet"]


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
