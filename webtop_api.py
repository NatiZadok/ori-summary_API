# -*- coding: utf-8 -*-
"""Webtop SmartSchool HTTP API client (no Playwright / no browser scrape)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import uuid
from enum import Enum
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
    getproxies,
)

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "webtop_state.json"
TOKEN_FILE = ROOT / "webtop_token.txt"
BIO_FILE = ROOT / "bio_credentials.json"
API_BASE = "https://webtopserver.smartschool.co.il/server/api"
WEB_ORIGIN = "https://webtop.smartschool.co.il"
ALLOWED_API_HOST = urlparse(API_BASE).hostname or "webtopserver.smartschool.co.il"


def env_flag(name: str, default: bool = False) -> bool:
    """Parse boolean env vars explicitly — 'false'/'0'/'no' are False."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    return default


ON_CLOUD = bool(os.environ.get("RENDER")) or env_flag("WEBTOP_CLOUD")
DIAGNOSTICS = env_flag("WEBTOP_DIAGNOSTICS")


class ErrorCategory(str, Enum):
    MISSING_TOKEN = "MISSING_TOKEN"
    INVALID_TOKEN = "INVALID_TOKEN"
    CONFIRMED_EXPIRED_TOKEN = "CONFIRMED_EXPIRED_TOKEN"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    EXTERNAL_ACCESS_REJECTED = "EXTERNAL_ACCESS_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    DNS_ERROR = "DNS_ERROR"
    TLS_ERROR = "TLS_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    INVALID_JSON = "INVALID_JSON"
    UNEXPECTED_RESPONSE = "UNEXPECTED_RESPONSE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN"


def git_revision() -> str:
    head = ROOT / ".git" / "HEAD"
    try:
        raw = head.read_text(encoding="utf-8").strip()
        if raw.startswith("ref:"):
            ref = (ROOT / ".git" / raw.split(" ", 1)[1].strip()).read_text(encoding="utf-8").strip()
            return ref[:12]
        return raw[:12]
    except Exception:
        return os.environ.get("RENDER_GIT_COMMIT", "unknown")[:12]


def _load_config() -> dict:
    cfg = {
        "username": os.environ.get("WEBTOP_USERNAME", ""),
        "password": os.environ.get("WEBTOP_PASSWORD", ""),
        "api_base": os.environ.get("WEBTOP_API_BASE", API_BASE),
        "token": os.environ.get("WEBTOP_TOKEN", "").strip(),
    }
    local = ROOT / "config.json"
    if local.exists():
        try:
            file_cfg = json.loads(local.read_text(encoding="utf-8"))
            if not isinstance(file_cfg, dict):
                raise ValueError("config.json must be an object")
            for key in ("username", "password", "api_base", "token"):
                if not cfg.get(key) and file_cfg.get(key):
                    cfg[key] = file_cfg[key]
        except Exception as exc:
            print(f"CONFIG warn config.json ignored: {exc}", flush=True)
    return cfg


CONFIG = _load_config()


def normalize_token(value: str) -> str:
    """Preserve opaque credentials; only strip surrounding whitespace/quotes."""
    t = (value or "").strip()
    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
        t = t[1:-1].strip()
    return t.replace("\r", "").replace("\n", "").strip()


# Back-compat alias used by older call sites / tests naming
_clean_token = normalize_token


def token_fingerprint(token: str) -> str:
    if not token:
        return "(empty)"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def short_fingerprint(token: str) -> str:
    full = token_fingerprint(token)
    return full if full == "(empty)" else full[:16]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _header_get(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name) or headers.get(name.lower())
        return str(value) if value else ""
    except Exception:
        return ""


def body_kind(content_type: str, text: str) -> str:
    ct = (content_type or "").lower()
    if "json" in ct:
        return "json"
    if "html" in ct:
        return "html"
    stripped = (text or "").lstrip()[:32].lower()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        return "html"
    if not stripped:
        return "empty"
    return "other"


class DenyRedirectHandler(HTTPRedirectHandler):
    """Do not auto-follow redirects on authenticated API calls (method/host safety)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


class WebtopApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        status: int | None = None,
        endpoint: str | None = None,
        payload: Any = None,
        diagnostics: dict | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.status = status
        self.endpoint = endpoint
        self.payload = payload
        self.diagnostics = diagnostics or {}

    def user_message(self) -> str:
        mapping = {
            ErrorCategory.MISSING_TOKEN: "חסר טוקן Webtop. יש להגדיר WEBTOP_TOKEN או לבצע רענון מקומי.",
            ErrorCategory.INVALID_TOKEN: "הטוקן נדחה על ידי Webtop.",
            ErrorCategory.CONFIRMED_EXPIRED_TOKEN: "הטוקן פג תוקף.",
            ErrorCategory.AUTHENTICATION_REQUIRED: "נדרשת התחברות מחדש ל־Webtop.",
            ErrorCategory.EXTERNAL_ACCESS_REJECTED: (
                "Webtop דחה את הגישה מהשרת (גישה חיצונית נדחתה). "
                "הטוקן לא סווג כפג־תוקף אוטומטית — ראי לוגי האבחון."
            ),
            ErrorCategory.RATE_LIMITED: "Webtop הגביל בקשות (rate limit).",
            ErrorCategory.NETWORK_TIMEOUT: "תם הזמן לחיבור ל־Webtop.",
            ErrorCategory.DNS_ERROR: "כשל בפתרון DNS לשרת Webtop.",
            ErrorCategory.TLS_ERROR: "כשל TLS מול Webtop.",
            ErrorCategory.SERVER_ERROR: "שגיאת שרת Webtop.",
            ErrorCategory.INVALID_JSON: "תשובת Webtop אינה JSON תקין.",
            ErrorCategory.UNEXPECTED_RESPONSE: "תשובת Webtop לא תואמת את הסכימה הצפויה.",
            ErrorCategory.CONFIGURATION_ERROR: "שגיאת תצורה.",
        }
        return mapping.get(self.category, str(self))


class TokenManager:
    """Centralized credential loading/saving without import-time file mutations."""

    def __init__(self, root: Path | None = None):
        self.root = root or ROOT
        self.token_file = self.root / "webtop_token.txt"
        self.state_file = self.root / "webtop_state.json"
        self.bio_file = self.root / "bio_credentials.json"
        self._runtime_token = ""
        self._source = "none"

    def normalize(self, value: str) -> str:
        return normalize_token(value)

    def _read_env_token(self) -> str:
        # Live env only — never a cached CONFIG snapshot from import time.
        return self.normalize(os.environ.get("WEBTOP_TOKEN", ""))

    def _read_config_token(self) -> str:
        local = self.root / "config.json"
        if not local.exists():
            return ""
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("token"):
                return self.normalize(str(data["token"]))
        except Exception:
            return ""
        return ""

    def _read_file_token(self) -> str:
        if not self.token_file.exists():
            return ""
        try:
            return self.normalize(self.token_file.read_text(encoding="utf-8"))
        except Exception:
            return ""

    def _read_state_token(self) -> str:
        if not self.state_file.exists():
            return ""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            for cookie in data.get("cookies") or []:
                if cookie.get("name") == "webToken" and cookie.get("value"):
                    return self.normalize(str(cookie["value"]))
        except Exception:
            return ""
        return ""

    def load(self) -> tuple[str, str]:
        """
        Deterministic precedence:
        1) runtime (after verified renew)
        2) WEBTOP_FORCE_ENV_TOKEN → WEBTOP_TOKEN (intentional rotation)
        3) webtop_token.txt (verified/persisted)
        4) WEBTOP_TOKEN
        5) config.json token
        6) webtop_state.json cookie
        """
        if self._runtime_token:
            self._source = "runtime"
            return self._runtime_token, self._source

        env_tok = self._read_env_token()
        file_tok = self._read_file_token()
        config_tok = self._read_config_token()
        state_tok = self._read_state_token()

        if env_flag("WEBTOP_FORCE_ENV_TOKEN"):
            if not env_tok:
                raise WebtopApiError(
                    "WEBTOP_FORCE_ENV_TOKEN set but WEBTOP_TOKEN empty",
                    category=ErrorCategory.CONFIGURATION_ERROR,
                )
            self._source = "env_forced"
            return env_tok, self._source

        if file_tok:
            self._source = "file"
            return file_tok, self._source
        if env_tok:
            self._source = "env"
            return env_tok, self._source
        if config_tok:
            self._source = "config"
            return config_tok, self._source
        if state_tok:
            self._source = "state"
            return state_tok, self._source
        self._source = "none"
        return "", self._source

    def set_runtime(self, token: str) -> None:
        self._runtime_token = self.normalize(token)
        self._source = "runtime"

    def save_verified(self, token: str) -> None:
        cleaned = self.normalize(token)
        if not cleaned:
            raise WebtopApiError("refusing to save empty token", category=ErrorCategory.CONFIGURATION_ERROR)
        _atomic_write_text(self.token_file, cleaned)
        self.set_runtime(cleaned)
        print("TOKEN saved webtop_token.txt (verified)", flush=True)

    def load_bio(self) -> dict | None:
        raw = os.environ.get("WEBTOP_BIO_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("bioLogin"):
                    return data
            except Exception as exc:
                print(f"BIO env invalid: {exc}", flush=True)
        if self.bio_file.exists():
            try:
                data = json.loads(self.bio_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("bioLogin"):
                    return data
            except Exception as exc:
                print(f"BIO file invalid: {exc}", flush=True)
        return None

    def save_bio(self, data: dict) -> None:
        if not isinstance(data, dict) or not data.get("bioLogin"):
            raise WebtopApiError("invalid bio payload", category=ErrorCategory.CONFIGURATION_ERROR)
        _atomic_write_text(self.bio_file, json.dumps(data, ensure_ascii=False, indent=2))
        print("BIO saved bio_credentials.json", flush=True)

    def bootstrap_optional_files(self) -> None:
        """
        Explicit bootstrap only — never called at import.
        Writes bio/state from env when present. Does NOT overwrite a newer file token
        with WEBTOP_TOKEN unless WEBTOP_FORCE_ENV_TOKEN is set.
        """
        import base64

        bio = os.environ.get("WEBTOP_BIO_JSON", "").strip()
        if bio:
            try:
                data = json.loads(bio)
                if isinstance(data, dict) and data.get("bioLogin"):
                    self.save_bio(data)
            except Exception as exc:
                print(f"BOOTSTRAP bio invalid: {exc}", flush=True)

        if env_flag("WEBTOP_FORCE_ENV_TOKEN"):
            tok = self._read_env_token()
            if tok:
                self.save_verified(tok)
                print("BOOTSTRAP token from WEBTOP_TOKEN (forced)", flush=True)
        elif not self._read_file_token():
            tok = self._read_env_token()
            if tok:
                _atomic_write_text(self.token_file, tok)
                print("BOOTSTRAP token from WEBTOP_TOKEN (file missing)", flush=True)

        b64 = os.environ.get("WEBTOP_STATE_B64", "").strip()
        raw = os.environ.get("WEBTOP_STATE_JSON", "").strip()
        try:
            if b64:
                data = json.loads(base64.b64decode(b64).decode("utf-8"))
                _atomic_write_text(self.state_file, json.dumps(data, ensure_ascii=False))
                print("BOOTSTRAP wrote webtop_state.json from WEBTOP_STATE_B64", flush=True)
            elif raw:
                data = json.loads(raw)
                _atomic_write_text(self.state_file, json.dumps(data, ensure_ascii=False))
                print("BOOTSTRAP wrote webtop_state.json from WEBTOP_STATE_JSON", flush=True)
        except Exception as exc:
            print(f"BOOTSTRAP state env invalid: {exc}", flush=True)


_TOKEN_MANAGER = TokenManager()


def bootstrap_token_from_env() -> None:
    """Explicit bootstrap entrypoint (not import-time)."""
    _TOKEN_MANAGER.bootstrap_optional_files()


def extract_web_token() -> str:
    token, _source = _TOKEN_MANAGER.load()
    if token:
        return token
    raise WebtopApiError(
        "חסר webToken. הגדירי WEBTOP_TOKEN, או שמרי webtop_token.txt אחרי התחברות.",
        category=ErrorCategory.MISSING_TOKEN,
    )


def save_token(token: str) -> None:
    _TOKEN_MANAGER.save_verified(token)


def load_bio() -> dict | None:
    return _TOKEN_MANAGER.load_bio()


def save_bio(data: dict) -> None:
    _TOKEN_MANAGER.save_bio(data)


def proxy_config_summary() -> dict[str, Any]:
    """Safe proxy diagnostics — types/applicability only, never URLs or credentials."""
    proxies = getproxies() or {}
    keys = sorted(str(k).lower() for k in proxies.keys() if (proxies.get(k) or "").strip())
    env_keys = [
        name
        for name in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        )
        if (os.environ.get(name) or "").strip()
    ]
    return {
        "getproxies_types": keys,
        "getproxies_count": len(keys),
        "env_proxy_vars_set": sorted({k.lower() for k in env_keys}),
        "https_proxy_applicable": any(k in ("https", "all") for k in keys),
        "http_proxy_applicable": any(k in ("http", "all") for k in keys),
    }


def resolve_api_host(hostname: str) -> dict[str, Any]:
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        families = sorted({str(item[0]).replace("AddressFamily.", "") for item in infos})
        return {"ok": True, "family_count": len(infos), "families": families}
    except socket.gaierror as exc:
        return {"ok": False, "error": f"gaierror:{getattr(exc, 'errno', '')}"}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def log_connectivity(*, api_base: str, token: str, token_source: str) -> None:
    parsed = urlparse(api_base)
    host = parsed.hostname or ALLOWED_API_HOST
    proxies = proxy_config_summary()
    dns = resolve_api_host(host)
    print(
        "CONNECTIVITY diag "
        f"git={git_revision()} "
        f"python={sys.version.split()[0]!r} "
        f"openssl={ssl.OPENSSL_VERSION!r} "
        f"platform={sys.platform!r} "
        f"cloud={ON_CLOUD} "
        f"api_base={api_base!r} "
        f"api_host={host!r} "
        f"token_source={token_source} "
        f"token_len={len(token)} "
        f"token_sha256={token_fingerprint(token)} "
        f"token_fp16={short_fingerprint(token)} "
        f"getproxies_types={proxies['getproxies_types']} "
        f"getproxies_count={proxies['getproxies_count']} "
        f"https_proxy_applicable={proxies['https_proxy_applicable']} "
        f"env_proxy_vars={proxies['env_proxy_vars_set']} "
        f"dns_ok={dns.get('ok')} dns_families={dns.get('families') or dns.get('error')}",
        flush=True,
    )


def classify_http_failure(
    *,
    status: int | None,
    content_type: str,
    text: str,
    endpoint: str,
) -> ErrorCategory:
    kind = body_kind(content_type, text)
    if status == 429:
        return ErrorCategory.RATE_LIMITED
    if status in (401,):
        return ErrorCategory.AUTHENTICATION_REQUIRED
    if status == 403:
        if kind == "html":
            return ErrorCategory.EXTERNAL_ACCESS_REJECTED
        # JSON 403 may still be app-level auth — keep distinct from expired-without-evidence
        return ErrorCategory.AUTHENTICATION_REQUIRED
    if status is not None and status >= 500:
        return ErrorCategory.SERVER_ERROR
    if kind == "html" and status is not None and status >= 400:
        return ErrorCategory.EXTERNAL_ACCESS_REJECTED
    if status in (400, 404) and endpoint.endswith("CheckToken"):
        return ErrorCategory.INVALID_TOKEN
    return ErrorCategory.UNKNOWN


def set_cookie_names(headers: Any) -> list[str]:
    """Return Set-Cookie header names only — never values."""
    if headers is None:
        return []
    try:
        vals = headers.get_all("Set-Cookie") or []
    except Exception:
        sc = headers.get("Set-Cookie") if hasattr(headers, "get") else None
        vals = [sc] if sc else []
    names: list[str] = []
    for value in vals:
        name = (value or "").split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def safe_http_diag(
    *,
    endpoint: str,
    status: int | None,
    headers: Any,
    body_text: str = "",
    duration_ms: float | None = None,
    category: ErrorCategory | None = None,
) -> dict[str, Any]:
    content_type = _header_get(headers, "Content-Type")
    cookie_names = set_cookie_names(headers)
    diag = {
        "endpoint": endpoint,
        "status": status,
        "server": _header_get(headers, "Server"),
        "via": _header_get(headers, "Via"),
        "x_cache": _header_get(headers, "X-Cache"),
        "x_amz_cf_id": _header_get(headers, "X-Amz-Cf-Id"),
        "x_amz_cf_pop": _header_get(headers, "X-Amz-Cf-Pop"),
        "age": _header_get(headers, "Age"),
        "cf_ray": _header_get(headers, "CF-RAY"),
        "cf_mitigated": _header_get(headers, "CF-Mitigated"),
        "content_type": content_type,
        "body_kind": body_kind(content_type, body_text),
        "set_cookie_count": len(cookie_names),
        "set_cookie_names": cookie_names,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "category": category.value if category else None,
    }
    print(
        "HTTP diag "
        + " ".join(f"{k}={v!r}" for k, v in diag.items() if v is not None),
        flush=True,
    )
    return diag


def extract_token_from_payload(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("token", "Token", "webToken", "accessToken"):
            if data.get(key):
                return normalize_token(str(data[key]))
        for nest in ("user", "session", "login"):
            nested = data.get(nest)
            if isinstance(nested, dict):
                found = extract_token_from_payload(nested)
                if found:
                    return found
    elif isinstance(data, str) and len(data) > 40:
        return normalize_token(data)
    return ""


class WebtopApi:
    def __init__(self, token: str | None = None, *, api_base: str | None = None, token_manager: TokenManager | None = None):
        self.tokens = token_manager or _TOKEN_MANAGER
        self.api_base = (api_base or CONFIG.get("api_base") or API_BASE).rstrip("/")
        self._validate_api_base()
        self.child: dict[str, Any] | None = None
        self.study_year: int | None = None
        self._last_auth_error = ""
        self._last_category: ErrorCategory | None = None
        self._last_diag: dict[str, Any] = {}
        self._token_source = "none"
        self.correlation_id = uuid.uuid4().hex[:12]
        self._jar = CookieJar()
        if token is not None:
            self.token = normalize_token(token)
            self._token_source = "constructor"
        else:
            try:
                self.token, self._token_source = self.tokens.load()
            except WebtopApiError:
                self.token = ""
                self._token_source = "none"
        self._seed_web_token_cookie()
        self._opener = build_opener(
            ProxyHandler(getproxies()),
            HTTPCookieProcessor(self._jar),
            DenyRedirectHandler,
            HTTPSHandler(),
        )

    def _validate_api_base(self) -> None:
        parsed = urlparse(self.api_base)
        if parsed.scheme != "https":
            raise WebtopApiError(
                "api_base must be https",
                category=ErrorCategory.CONFIGURATION_ERROR,
            )
        if (parsed.hostname or "").lower() != ALLOWED_API_HOST.lower():
            raise WebtopApiError(
                f"api_base host must be {ALLOWED_API_HOST}",
                category=ErrorCategory.CONFIGURATION_ERROR,
            )

    def _seed_web_token_cookie(self) -> None:
        """Put webToken into CookieJar. Do not also set a manual Cookie header."""
        for cookie in list(self._jar):
            if cookie.name == "webToken":
                try:
                    self._jar.clear(cookie.domain, cookie.path, cookie.name)
                except KeyError:
                    pass
        if not self.token:
            return
        self._jar.set_cookie(
            Cookie(
                version=0,
                name="webToken",
                value=self.token,
                port=None,
                port_specified=False,
                domain=ALLOWED_API_HOST,
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )

    def _sync_token_from_jar(self) -> bool:
        """Adopt rotated webToken from Set-Cookie via CookieJar. Returns True if changed."""
        for cookie in self._jar:
            if cookie.name != "webToken" or not cookie.value:
                continue
            candidate = normalize_token(cookie.value)
            if candidate and candidate != self.token:
                print(
                    f"AUTH session cookie rotated fp16={short_fingerprint(candidate)} "
                    f"jar_count={len(list(self._jar))}",
                    flush=True,
                )
                self.token = candidate
                self._token_source = "set_cookie"
                self._seed_web_token_cookie()
                return True
        return False

    def _apply_token_update(self, payload: Any, *, source: str) -> bool:
        candidate = extract_token_from_payload(payload)
        if not candidate or candidate == self.token:
            return False
        print(
            f"AUTH token updated from {source} fp16={short_fingerprint(candidate)}",
            flush=True,
        )
        self.token = candidate
        self._token_source = source
        self._seed_web_token_cookie()
        return True

    def _require_check_flag(self, endpoint: str, data: Any) -> None:
        """CheckToken / CheckBackgroundToken return boolean data; True means usable session."""
        if data is True:
            return
        if data is False:
            raise WebtopApiError(
                f"{endpoint} data=false — token is not usable for a full session",
                category=ErrorCategory.INVALID_TOKEN,
                endpoint=endpoint,
                status=self._last_diag.get("status"),
                diagnostics=self._last_diag,
            )
        raise WebtopApiError(
            f"{endpoint} unexpected data type={type(data).__name__}",
            category=ErrorCategory.UNEXPECTED_RESPONSE,
            endpoint=endpoint,
            status=self._last_diag.get("status"),
            diagnostics=self._last_diag,
        )

    def _headers(self) -> dict[str, str]:
        # Cookie auth is sent by HTTPCookieProcessor from CookieJar.
        # Do not set a manual Cookie header — it conflicts with jar-managed session cookies.
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/plain, */*",
            "language": "he",
            "Origin": WEB_ORIGIN,
            "Referer": WEB_ORIGIN + "/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request_header_names(self) -> list[str]:
        # Include Cookie as conceptually present via jar when token exists.
        names = set(self._headers().keys())
        if self.token:
            names.add("Cookie")
        return sorted(names)

    def post(self, path: str, body: Any = None) -> Any:
        endpoint = path if path.startswith("http") else path.lstrip("/")
        url = path if path.startswith("http") else f"{self.api_base}/{endpoint}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != ALLOWED_API_HOST.lower():
            raise WebtopApiError(
                "refusing request to unintended host",
                category=ErrorCategory.CONFIGURATION_ERROR,
                endpoint=endpoint,
            )
        payload = {} if body is None else body
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # Keep jar cookie in sync with current token before each request.
        self._seed_web_token_cookie()
        req = Request(url, data=raw, headers=self._headers(), method="POST")
        started = time.perf_counter()
        try:
            with self._opener.open(req, timeout=45) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", None) or resp.getcode()
                duration_ms = (time.perf_counter() - started) * 1000
                self._last_diag = safe_http_diag(
                    endpoint=endpoint,
                    status=status,
                    headers=resp.headers,
                    body_text=text,
                    duration_ms=duration_ms,
                )
                self._sync_token_from_jar()
        except HTTPError as exc:
            err_text = ""
            try:
                err_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            duration_ms = (time.perf_counter() - started) * 1000
            category = classify_http_failure(
                status=exc.code,
                content_type=_header_get(exc.headers, "Content-Type"),
                text=err_text,
                endpoint=endpoint,
            )
            diag = safe_http_diag(
                endpoint=endpoint,
                status=exc.code,
                headers=exc.headers,
                body_text=err_text,
                duration_ms=duration_ms,
                category=category,
            )
            self._last_diag = diag
            self._last_category = category
            self._sync_token_from_jar()
            raise WebtopApiError(
                f"{endpoint} HTTP {exc.code}",
                category=category,
                status=exc.code,
                endpoint=endpoint,
                diagnostics=diag,
            ) from exc
        except URLError as exc:
            reason = str(exc.reason)
            lower = reason.lower()
            if "timed out" in lower or "timeout" in lower:
                category = ErrorCategory.NETWORK_TIMEOUT
            elif "name or service not known" in lower or "getaddrinfo" in lower or "nodename" in lower:
                category = ErrorCategory.DNS_ERROR
            elif "ssl" in lower or "certificate" in lower or "tls" in lower:
                category = ErrorCategory.TLS_ERROR
            else:
                category = ErrorCategory.UNKNOWN
            self._last_category = category
            raise WebtopApiError(
                f"{endpoint} network error: {exc.reason}",
                category=category,
                endpoint=endpoint,
            ) from exc
        except TimeoutError as exc:
            self._last_category = ErrorCategory.NETWORK_TIMEOUT
            raise WebtopApiError(
                f"{endpoint} timeout",
                category=ErrorCategory.NETWORK_TIMEOUT,
                endpoint=endpoint,
            ) from exc

        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise WebtopApiError(
                f"{endpoint} non-JSON response (body_kind={body_kind('', text)})",
                category=ErrorCategory.INVALID_JSON,
                endpoint=endpoint,
                status=status,
                diagnostics=self._last_diag,
            ) from exc

        if not isinstance(data, dict) or "status" not in data:
            raise WebtopApiError(
                f"{endpoint} unexpected schema (missing status)",
                category=ErrorCategory.UNEXPECTED_RESPONSE,
                endpoint=endpoint,
                status=status,
                diagnostics=self._last_diag,
            )
        if data.get("status") is not True:
            err_text = (
                data.get("errorDescription")
                or data.get("message")
                or f"{endpoint} status=false"
            )
            category = ErrorCategory.AUTHENTICATION_REQUIRED
            lowered = str(err_text).lower()
            if "expir" in lowered or "פג" in str(err_text) or "תוקף" in str(err_text):
                category = ErrorCategory.CONFIRMED_EXPIRED_TOKEN
            elif "invalid" in lowered or "לא תקין" in str(err_text):
                category = ErrorCategory.INVALID_TOKEN
            raise WebtopApiError(
                str(err_text),
                category=category,
                endpoint=endpoint,
                status=status,
                payload=data,
                diagnostics=self._last_diag,
            )
        payload_data = data.get("data")
        self._apply_token_update(payload_data, source=endpoint)
        return payload_data

    def check_token(self) -> bool:
        if not self.token:
            print("AUTH CheckToken succeeded=False reason=no_token", flush=True)
            self._last_category = ErrorCategory.MISSING_TOKEN
            return False
        try:
            data = self.post("dashboard/CheckToken", {})
            self._require_check_flag("dashboard/CheckToken", data)
            print("AUTH CheckToken succeeded=True data=true", flush=True)
            return True
        except WebtopApiError as exc:
            print(
                f"AUTH CheckToken succeeded=False category={exc.category.value} "
                f"status={exc.status} detail={exc}",
                flush=True,
            )
            self._last_auth_error = str(exc)
            self._last_category = exc.category
            return False

    def diagnose_auth_flow(self) -> dict[str, Any]:
        """End-to-end auth diagnostic: CheckToken → CheckBackgroundToken → InitDashboard."""
        report: dict[str, Any] = {
            "token_present": bool(self.token),
            "token_len": len(self.token),
            "token_fp16": short_fingerprint(self.token),
            "token_source": self._token_source,
            "jar_cookie_count": len(list(self._jar)),
            "steps": [],
        }

        def step(name: str) -> None:
            entry: dict[str, Any] = {"endpoint": name}
            try:
                data = self.post(name, {})
                entry["ok"] = True
                entry["http"] = dict(self._last_diag)
                entry["data_type"] = type(data).__name__
                if name in ("dashboard/CheckToken", "dashboard/CheckBackgroundToken"):
                    entry["data_bool"] = data if isinstance(data, bool) else None
                    self._require_check_flag(name, data)
                elif name == "dashboard/InitDashboard":
                    if not isinstance(data, dict):
                        raise WebtopApiError(
                            "InitDashboard expected object data",
                            category=ErrorCategory.UNEXPECTED_RESPONSE,
                            endpoint=name,
                            diagnostics=self._last_diag,
                        )
                    kids = data.get("childrens") or data.get("children") or []
                    entry["children_count"] = len(kids) if isinstance(kids, list) else None
                    entry["data_keys"] = sorted(str(k) for k in data.keys())
                report["steps"].append(entry)
            except WebtopApiError as exc:
                entry["ok"] = False
                entry["category"] = exc.category.value
                entry["status"] = exc.status
                entry["http"] = exc.diagnostics or dict(self._last_diag)
                report["steps"].append(entry)
                report["ok"] = False
                report["failed_at"] = name
                raise

        try:
            step("dashboard/CheckToken")
            step("dashboard/CheckBackgroundToken")
            step("dashboard/InitDashboard")
            report["ok"] = True
        except WebtopApiError:
            pass
        return report

    def establish_session(self) -> None:
        """Require a fully usable session — not CheckToken alone."""
        if not self.token:
            raise WebtopApiError(
                "חסר webToken",
                category=ErrorCategory.MISSING_TOKEN,
            )
        data = self.post("dashboard/CheckToken", {})
        self._require_check_flag("dashboard/CheckToken", data)
        bg = self.post("dashboard/CheckBackgroundToken", {})
        self._apply_token_update(bg, source="dashboard/CheckBackgroundToken")
        self._require_check_flag("dashboard/CheckBackgroundToken", bg)
        dash = self.post("dashboard/InitDashboard", {})
        if not isinstance(dash, dict):
            raise WebtopApiError(
                "InitDashboard unexpected schema",
                category=ErrorCategory.UNEXPECTED_RESPONSE,
                endpoint="dashboard/InitDashboard",
                diagnostics=self._last_diag,
            )
        kids = dash.get("childrens") or dash.get("children") or []
        if isinstance(kids, list) and kids:
            self.child = kids[0]
        print(
            "AUTH session OK "
            "CheckToken=true CheckBackgroundToken=true InitDashboard=ok "
            f"jar_count={len(list(self._jar))}",
            flush=True,
        )

    def renew_via_bio(self) -> bool:
        """Mint a fresh webToken from bioLogin when available — not used for CloudFront 403."""
        bio = self.tokens.load_bio()
        if not bio:
            print("AUTH token_renewal succeeded=False reason=no_bio", flush=True)
            return False
        body = {
            "id": bio["bioLogin"],
            "param1": str(bio.get("mode", "2")),
            "param2": bio.get("deviceId") or "",
            "param3": bio.get("selectedUser") or "",
            "param4": bio.get("uniqueId") or "",
            "param5": str(bool(bio.get("isMobile", True))).lower(),
        }
        try:
            data = self.post("user/loginByBio", body)
        except WebtopApiError as exc:
            print(
                f"AUTH token_renewal succeeded=False category={exc.category.value} "
                f"status={exc.status} detail={exc}",
                flush=True,
            )
            self._last_auth_error = str(exc)
            self._last_category = exc.category
            return False
        if not isinstance(data, dict):
            print("AUTH token_renewal succeeded=False reason=non_dict", flush=True)
            return False
        token = data.get("token") or data.get("Token")
        if not token:
            print(
                "AUTH token_renewal succeeded=False reason=no_token_field "
                f"keys={sorted(str(k) for k in data)}",
                flush=True,
            )
            return False
        self.token = normalize_token(token)
        self._token_source = "bio"
        self._seed_web_token_cookie()
        self.tokens.save_verified(self.token)
        try:
            self.establish_session()
            print("AUTH token_renewal succeeded=True", flush=True)
            return True
        except WebtopApiError as exc:
            print(
                f"AUTH token_renewal succeeded=False category={exc.category.value}",
                flush=True,
            )
            self._last_auth_error = str(exc)
            self._last_category = exc.category
            return False

    def ensure_auth(self) -> None:
        """Full session auth; renew via bio only for auth/expiry failures — never for edge 403 HTML."""
        self._last_auth_error = ""
        self._last_category = None
        has_token = bool(self.token)
        has_bio = bool(self.tokens.load_bio())
        log_connectivity(api_base=self.api_base, token=self.token, token_source=self._token_source)
        print(
            f"AUTH start corr={self.correlation_id} has_token={has_token} "
            f"token_len={len(self.token)} has_bio={has_bio} cloud={ON_CLOUD} "
            f"token_source={self._token_source} jar_count={len(list(self._jar))}",
            flush=True,
        )
        try:
            self.establish_session()
            return
        except WebtopApiError as exc:
            self._last_auth_error = str(exc)
            self._last_category = exc.category
            if exc.category == ErrorCategory.EXTERNAL_ACCESS_REJECTED:
                raise

        category = self._last_category or ErrorCategory.UNKNOWN
        renewable = category in (
            ErrorCategory.AUTHENTICATION_REQUIRED,
            ErrorCategory.INVALID_TOKEN,
            ErrorCategory.CONFIRMED_EXPIRED_TOKEN,
            ErrorCategory.MISSING_TOKEN,
            ErrorCategory.UNEXPECTED_RESPONSE,
        )
        if renewable and has_bio:
            print("AUTH attempting bio renew after auth-class failure…", flush=True)
            if self.renew_via_bio():
                return
        elif renewable and not has_bio:
            print(
                "AUTH bio renew skipped reason=no_bio — run רענון-טוקן.bat for a fresh session",
                flush=True,
            )

        if not has_token and not has_bio:
            raise WebtopApiError(
                "חסר WEBTOP_TOKEN (או WEBTOP_BIO_JSON).",
                category=ErrorCategory.MISSING_TOKEN,
            )
        raise WebtopApiError(
            WebtopApiError("", category=category).user_message()
            + " יש לרענן טוקן מקומית (רענון-טוקן.bat). "
            f"detail={self._last_auth_error or 'unknown'}",
            category=category,
            status=self._last_diag.get("status"),
            endpoint=self._last_diag.get("endpoint") or "dashboard/CheckToken",
            diagnostics=self._last_diag,
        )

    def init_dashboard(self) -> dict:
        return self.post("dashboard/InitDashboard", {}) or {}

    def ensure_child(self) -> dict:
        if self.child:
            return self.child
        dash = self.init_dashboard()
        kids = dash.get("childrens") or dash.get("children") or []
        if not kids:
            raise WebtopApiError(
                "לא נמצא תלמיד ב־InitDashboard",
                category=ErrorCategory.UNEXPECTED_RESPONSE,
                endpoint="dashboard/InitDashboard",
            )
        if len(kids) > 1 and DIAGNOSTICS:
            print(
                f"AUTH children_count={len(kids)} selecting_index=0 "
                "(set WEBTOP_CHILD_INDEX to override later)",
                flush=True,
            )
        idx = 0
        raw_idx = os.environ.get("WEBTOP_CHILD_INDEX", "").strip()
        if raw_idx.isdigit():
            idx = min(int(raw_idx), len(kids) - 1)
        self.child = kids[idx]
        return self.child

    def pupil_settings(self) -> dict:
        data = self.post("PupilCard/GetSettingsList", {}) or {}
        year = data.get("currentStudyYear")
        if isinstance(year, int):
            self.study_year = year
        return data

    def menu_counters(self) -> dict:
        return self.post("Menu/GetMenuCounters", {}) or {}

    def unread_notifications_preview(self) -> dict:
        return self.post("Menu/GetPreviewUnreadNotifications", {}) or {}

    def messages_inbox(self, page: int = 1) -> list:
        data = self.post(
            "messageBox/GetMessagesInbox",
            {
                "PageId": page,
                "LabelId": 0,
                "HasRead": None,
                "SearchQuery": "",
            },
        )
        return data if isinstance(data, list) else []

    def discipline_events(self) -> dict:
        child = self.ensure_child()
        return (
            self.post(
                "dashboard/GetPupilDiciplineEvents",
                {"id": child["id"], "ClassCode": child.get("classCode")},
            )
            or {}
        )

    def lessons_and_homework(self, *, week_index: int = 0) -> list:
        child = self.ensure_child()
        settings = self.pupil_settings()
        year = self.study_year or settings.get("currentStudyYear") or 2026
        data = self.post(
            "PupilCard/GetPupilLessonsAndHomework",
            {
                "studentID": child["id"],
                "classCode": child.get("classCode"),
                "classNum": child.get("classNum"),
                "studyYear": year,
                "weekIndex": week_index,
                "moduleID": 11,
                "viewType": 0,
            },
        )
        return data if isinstance(data, list) else []


_NO_HW = frozenset(
    v.casefold()
    for v in (
        "אין",
        "אין שיעורי בית",
        "אין שעורי בית",
        "לא הוזן",
        "ללא",
        "ללא שיעורי בית",
        "none",
        "n/a",
        "-",
        "--",
        ".",
        "",
    )
)


def is_no_homework(text: str) -> bool:
    t = " ".join((text or "").split()).strip().strip(".!:;־-").casefold()
    return t in _NO_HW


def parse_iso_date(value: str) -> str:
    """Return dd/mm or dd/mm/yyyy-ish short form for UI."""
    if not value:
        return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(3)}/{m.group(2)}"
    m2 = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if m2:
        return f"{m2.group(1)}/{m2.group(2)}"
    return value


def extract_homework_from_weeks(weeks: list[list]) -> list[dict]:
    """Flatten PupilCard week payloads into {date, subject, text}."""
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for days in weeks:
        for day in days or []:
            if not isinstance(day, dict):
                continue
            date = parse_iso_date(day.get("date") or "")
            for hour in day.get("hoursData") or []:
                if not isinstance(hour, dict):
                    continue
                for entry in hour.get("scheduale") or []:
                    if not isinstance(entry, dict):
                        continue
                    text = (entry.get("homeWork") or "").strip()
                    if is_no_homework(text):
                        continue
                    subject = (entry.get("subject_name") or "אחר").strip()
                    key = (date, subject, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"date": date, "subject": subject, "text": text})
    return items
