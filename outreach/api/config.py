"""The API's own numbers: `config.toml` loaded into a frozen `ApiConfig`."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("config.toml")


class ConfigError(ValueError):
    """A number in config.toml is missing or not a positive whole number."""


@dataclass(frozen=True)
class ApiConfig:
    notes_max_chars: int
    min_requests: int
    finding_group_min: int
    prompt_after_days: int
    reask_after_days: int
    quiet_after_days: int
    search_index_top: int


def load_config(path: Path = DEFAULT_PATH) -> ApiConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    def number(section: str, key: str) -> int:
        value = raw.get(section, {}).get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"[{section}] {key} must be a whole number of 1 or more, got {value!r}")
        return value

    config = ApiConfig(
        notes_max_chars=number("notes", "max_chars"),
        min_requests=number("outcomes", "min_requests"),
        finding_group_min=number("outcomes", "finding_group_min"),
        prompt_after_days=number("outcomes", "prompt_after_days"),
        reask_after_days=number("outcomes", "reask_after_days"),
        quiet_after_days=number("outcomes", "quiet_after_days"),
        search_index_top=number("search", "index_top"),
    )
    if config.quiet_after_days <= config.prompt_after_days:
        raise ConfigError("[outcomes] quiet_after_days must be larger than prompt_after_days")
    return config
