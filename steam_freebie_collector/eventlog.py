from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "authentication")


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_text() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def redact(value: Any, key: str = "") -> Any:
    if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class EventLogger:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    def emit(self, event: str, **fields: Any) -> None:
        now = utc_now()
        record = redact({"timestamp": now.isoformat().replace("+00:00", "Z"), "event": event, **fields})
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"collector-{now.date().isoformat()}.jsonl"
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock, path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(serialized + "\n")

