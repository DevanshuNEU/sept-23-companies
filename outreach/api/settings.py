"""Run settings from the environment, with the guards that keep the API off every other port.

The API listens on 127.0.0.1 only. Ports 3001, 3002, 3011 and 3013 belong to the old dashboard, the mail API and
their previews; 5173 belongs to the web dev server (it is an allowed origin, never a port to bind).
"""
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import live_checkout
from outreach.store.db import DEFAULT_PATH, normalise

FORBIDDEN_PORTS = frozenset({3001, 3002, 3011, 3013})
DEFAULT_PORT = 3100
LOOPBACK = "127.0.0.1"
DEFAULT_BANNED_PHRASES = "~/Desktop/DevVault/job-search/outreach/banned_phrases.txt"
MAX_BUSY_MS = 600_000

_ORIGIN = re.compile(r"http://(?:127\.0\.0\.1|localhost):([0-9]{1,5})")
_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


class SettingsError(ValueError):
    """A setting is not allowed. The message says which one and why, in plain words."""


@dataclass(frozen=True)
class Settings:
    db_path: str
    host: str
    port: int
    dev_origins: frozenset[str]
    web_dist: Path
    banned_phrases: Path
    busy_timeout_ms: int
    sweeper: bool


def _port(name: str, text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        raise SettingsError(f"{name} must be a whole number, got {text!r}") from None
    if not 1024 <= port <= 65535:
        raise SettingsError(f"{name} must be from 1024 to 65535, got {port}")
    if port in FORBIDDEN_PORTS:
        raise SettingsError(f"{name} cannot be {port}: that port belongs to the old dashboard or the mail API")
    return port


def _listen_port(env: Mapping[str, str]) -> int:
    if "OUTREACH_API_PORT" in env:
        return _port("OUTREACH_API_PORT", env["OUTREACH_API_PORT"])
    if env.get("CONDUCTOR_PORT"):  # a Conductor workspace: its own range, one above the web app
        try:
            base = int(env["CONDUCTOR_PORT"])
        except ValueError:
            raise SettingsError(f"CONDUCTOR_PORT must be a whole number, got {env['CONDUCTOR_PORT']!r}") from None
        return _port("CONDUCTOR_PORT + 1", str(base + 1))
    return DEFAULT_PORT


def _origins(text: str) -> frozenset[str]:
    found = set()
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        match = _ORIGIN.fullmatch(item)
        if not match or not 1 <= int(match.group(1)) <= 65535:
            raise SettingsError(
                f"OUTREACH_DEV_ORIGINS entries must look like http://127.0.0.1:5173 or http://localhost:5173, got {item!r}"
            )
        if int(match.group(1)) in FORBIDDEN_PORTS:
            raise SettingsError(f"OUTREACH_DEV_ORIGINS cannot allow {item}: that page belongs to the old dashboard")
        found.add(item)
    return frozenset(found)


def _flag(name: str, text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise SettingsError(f"{name} must be on or off, got {text!r}")


def _busy(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise SettingsError(f"OUTREACH_API_BUSY_MS must be a whole number of milliseconds, got {text!r}") from None
    if not 1 <= value <= MAX_BUSY_MS:
        raise SettingsError(f"OUTREACH_API_BUSY_MS must be from 1 to {MAX_BUSY_MS}, got {value}")
    return value


def _path(text: str) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else Path(live_checkout.REPO_ROOT) / path


def settings_from_env(env: Mapping[str, str] = os.environ) -> Settings:
    host = env.get("OUTREACH_API_HOST", LOOPBACK)
    if host != LOOPBACK:
        raise SettingsError(f"OUTREACH_API_HOST must be {LOOPBACK}, got {host!r}: the API never listens beyond this machine")
    return Settings(
        db_path=normalise(env.get("OUTREACH_DB") or DEFAULT_PATH),
        host=host,
        port=_listen_port(env),
        dev_origins=_origins(env.get("OUTREACH_DEV_ORIGINS", "")),
        web_dist=_path(env.get("OUTREACH_WEB_DIST") or "web/dist"),
        banned_phrases=_path(env.get("OUTREACH_BANNED_PHRASES") or DEFAULT_BANNED_PHRASES),
        busy_timeout_ms=_busy(env.get("OUTREACH_API_BUSY_MS", "5000")),
        sweeper=_flag("OUTREACH_API_SWEEPER", env.get("OUTREACH_API_SWEEPER", "on")),
    )
