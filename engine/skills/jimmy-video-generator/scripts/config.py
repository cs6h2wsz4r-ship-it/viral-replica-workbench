from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


DEFAULT_ENV_FILE = Path.home() / ".codex" / "secrets" / "seedance.env"
JIMMY_ORIGIN = "https://api.viraltok.ai/api/open-api/v1"


class ConfigurationError(RuntimeError):
    pass


def _parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class Settings:
    jimmy_api_key: str = field(repr=False)
    jimmy_base_url: str

    def require_jimmy(self) -> None:
        if not self.jimmy_api_key:
            raise ConfigurationError("Missing configuration: JIMMY_API_KEY")


def load_settings(
    path: Path = DEFAULT_ENV_FILE,
    *,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    file_values = _parse_env(path)
    merged = {**file_values, **dict(os.environ if environ is None else environ)}

    jimmy_base_url = merged.get("JIMMY_BASE_URL") or JIMMY_ORIGIN
    parsed_jimmy_url = urlparse(jimmy_base_url)
    if (
        jimmy_base_url != JIMMY_ORIGIN
        or parsed_jimmy_url.scheme != "https"
        or parsed_jimmy_url.hostname != "api.viraltok.ai"
        or parsed_jimmy_url.username is not None
        or parsed_jimmy_url.password is not None
        or parsed_jimmy_url.path != "/api/open-api/v1"
        or parsed_jimmy_url.query
        or parsed_jimmy_url.fragment
    ):
        raise ConfigurationError(f"JIMMY_BASE_URL must be exactly {JIMMY_ORIGIN}")

    return Settings(
        jimmy_api_key=merged.get("JIMMY_API_KEY", ""),
        jimmy_base_url=jimmy_base_url,
    )
