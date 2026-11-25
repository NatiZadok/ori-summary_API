# -*- coding: utf-8 -*-
"""Secure session/data storage abstraction.

Render Free filesystem is ephemeral — file writes are NOT durable across
restarts/deploys. Durable options (require approval before provisioning):
  - Render Environment secrets (current production path for WEBTOP_*)
  - Render Disk (paid)
  - External encrypted store (e.g. Supabase) — interface only, not provisioned
"""
from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SessionStore(ABC):
    """Stores opaque session material server-side only — never log values."""

    @abstractmethod
    def load_session_meta(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def save_session_meta(self, meta: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def clear_session_meta(self) -> None:
        ...

    @abstractmethod
    def backend_name(self) -> str:
        ...

    @abstractmethod
    def durable(self) -> bool:
        ...


class FileSessionStore(SessionStore):
    """Local/dev store. On Render Free this is ephemeral — not permanent."""

    def __init__(self, root: Path | None = None):
        self.root = root or ROOT
        self.path = self.root / "webtop_session_meta.json"

    def backend_name(self) -> str:
        return "file"

    def durable(self) -> bool:
        # Explicit: container filesystem is not durable on Render Free.
        return os.environ.get("RENDER") is None

    def load_session_meta(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_session_meta(self, meta: dict[str, Any]) -> None:
        safe = {
            k: v
            for k, v in (meta or {}).items()
            if k
            in (
                "status",
                "last_check_at",
                "last_success_at",
                "last_error_category",
                "cloud",
                "poc_result",
                "official_oauth_available",
                "message",
            )
        }
        _atomic_write(self.path, json.dumps(safe, ensure_ascii=False, indent=2))

    def clear_session_meta(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except TypeError:
            if self.path.exists():
                self.path.unlink()


class EnvLegacyHintStore(SessionStore):
    """Read-only view: credentials may exist in Render env (legacy). No password storage."""

    def backend_name(self) -> str:
        return "render_env_legacy"

    def durable(self) -> bool:
        return True  # Render env vars persist across deploys

    def load_session_meta(self) -> dict[str, Any]:
        has_token = bool((os.environ.get("WEBTOP_TOKEN") or "").strip())
        has_bio = bool((os.environ.get("WEBTOP_BIO_JSON") or "").strip())
        return {
            "legacy_env_token_present": has_token,
            "legacy_env_bio_present": has_bio,
            "note": "Env presence only — values never returned",
        }

    def save_session_meta(self, meta: dict[str, Any]) -> None:
        raise RuntimeError("Render env cannot be written from the app runtime")

    def clear_session_meta(self) -> None:
        raise RuntimeError("Clear Render env secrets via the Render dashboard")


class SupabaseSessionStore(SessionStore):
    """Optional encrypted remote store — not provisioned; requires approval + credentials."""

    def __init__(self):
        self.url = (os.environ.get("SUPABASE_URL") or "").strip()
        self.key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

    def backend_name(self) -> str:
        return "supabase_optional"

    def durable(self) -> bool:
        return bool(self.url and self.key)

    def configured(self) -> bool:
        return self.durable()

    def load_session_meta(self) -> dict[str, Any]:
        if not self.configured():
            return {"configured": False}
        raise RuntimeError(
            "Supabase store is not activated — provision only after explicit approval"
        )

    def save_session_meta(self, meta: dict[str, Any]) -> None:
        raise RuntimeError(
            "Supabase store is not activated — provision only after explicit approval"
        )

    def clear_session_meta(self) -> None:
        raise RuntimeError(
            "Supabase store is not activated — provision only after explicit approval"
        )


def default_session_store() -> SessionStore:
    return FileSessionStore()
