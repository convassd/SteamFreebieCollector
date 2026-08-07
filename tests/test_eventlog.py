import json

from steam_freebie_collector.eventlog import EventLogger


def test_event_log_redacts_sensitive_fields(tmp_path):
    logger = EventLogger(tmp_path)
    logger.emit("test", password="bad", Authentication="also-bad", nested={"token": "bad", "safe": "ok"})
    path = next(tmp_path.glob("*.jsonl"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["password"] == "<redacted>"
    assert record["Authentication"] == "<redacted>"
    assert record["nested"]["token"] == "<redacted>"
    assert record["nested"]["safe"] == "ok"

