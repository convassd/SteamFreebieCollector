from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class AppConfig:
    project_root: Path
    index_url: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    get_retry_count: int
    asf_base_url: str
    asf_health_wait_seconds: float
    asf_health_poll_seconds: float
    asf_executable_path: Path
    asf_working_directory: Path
    asf_shutdown_wait_seconds: float
    cycle_lease_timeout_seconds: float
    database_path: Path
    logs_path: Path


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path is not None else PROJECT_ROOT / "config.toml"
    project_root = config_path.resolve().parent

    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    keylol = raw.get("keylol", {})
    asf = raw.get("asf", {})
    scheduled = raw.get("scheduled", {})
    storage = raw.get("storage", {})

    return AppConfig(
        project_root=project_root,
        index_url=str(keylol.get("index_url", "https://keylol.com/t572814-1-1")),
        connect_timeout_seconds=float(keylol.get("connect_timeout_seconds", 10.0)),
        read_timeout_seconds=float(keylol.get("read_timeout_seconds", 30.0)),
        get_retry_count=int(keylol.get("get_retry_count", 3)),
        asf_base_url=str(asf.get("base_url", "http://localhost:1242")).rstrip("/"),
        asf_health_wait_seconds=float(asf.get("health_wait_seconds", 120.0)),
        asf_health_poll_seconds=float(asf.get("health_poll_seconds", 2.0)),
        asf_executable_path=Path(str(asf.get("executable", "E:/download/ASF-win-x64/ArchiSteamFarm.exe"))),
        asf_working_directory=Path(str(asf.get("working_directory", "E:/download/ASF-win-x64"))),
        asf_shutdown_wait_seconds=float(asf.get("shutdown_wait_seconds", 30.0)),
        cycle_lease_timeout_seconds=float(scheduled.get("cycle_lease_timeout_seconds", 900.0)),
        database_path=_resolve_project_path(project_root, str(storage.get("database", "data/collector.sqlite3"))),
        logs_path=_resolve_project_path(project_root, str(storage.get("logs", "logs"))),
    )
