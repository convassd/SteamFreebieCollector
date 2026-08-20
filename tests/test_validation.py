import json
from dataclasses import replace

import pytest

from steam_freebie_collector.asf import SubmissionOutcome, SubmissionResult
from steam_freebie_collector.config import load_config
from steam_freebie_collector.eventlog import EventLogger
from steam_freebie_collector.storage import StateStore
from steam_freebie_collector.validation import execute_one_off_validation


INDEX_URL = "https://keylol.com/t572814-1-1"
DETAIL_URL = "https://keylol.com/t1045840-1-1"


class TrackingFetcher:
    def __init__(self, mapping, events, error=None):
        self.mapping = mapping
        self.events = events
        self.error = error

    def get_html(self, url):
        self.events.append(f"fetch:{url}")
        if self.error is not None:
            raise self.error
        return self.mapping[url]


class TrackingAsf:
    def __init__(self, events):
        self.events = events
        self.submits = []

    def submit(self, command):
        self.events.append(f"submit:{command}")
        self.submits.append(command)
        return SubmissionResult(SubmissionOutcome.SUBMITTED, 200, True, "OK", "done", None, 1)


class TrackingLifecycle:
    def __init__(self, *, events, **kwargs):
        self.events = events
        self.ready = False

    def ensure_available(self):
        if not self.ready:
            self.events.append("asf:ensure")
            self.ready = True

    def cleanup(self):
        self.events.append("asf:cleanup")


def config_for(tmp_path):
    return replace(load_config(), database_path=tmp_path / "state.sqlite3", logs_path=tmp_path / "logs")


def fixture_mapping(fixture_text):
    return {INDEX_URL: fixture_text("current_index.html"), DETAIL_URL: fixture_text("current_detail.html")}


def execute(tmp_path, fixture_text, *, events, asf=None, fetch_error=None):
    config = config_for(tmp_path)
    store = StateStore(config.database_path)
    fetcher = TrackingFetcher(fixture_mapping(fixture_text), events, error=fetch_error)
    asf = asf or TrackingAsf(events)

    def lifecycle_factory(**kwargs):
        return TrackingLifecycle(events=events, **kwargs)

    summary = execute_one_off_validation(
        config=config,
        fetcher=fetcher,
        asf_client=asf,
        store=store,
        logger=EventLogger(config.logs_path),
        lifecycle_factory=lifecycle_factory,
    )
    return summary, store, asf, config


def read_events(logs_path):
    return [json.loads(line) for path in logs_path.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]


def test_validation_checks_asf_before_fetch_and_cleans_up_after_processing(tmp_path, fixture_text):
    events = []
    summary, _, asf, config = execute(tmp_path, fixture_text, events=events)
    assert summary.submitted == 1
    assert asf.submits == ["!ALA s/1706211"]
    assert events == [
        "asf:ensure",
        f"fetch:{INDEX_URL}",
        f"fetch:{DETAIL_URL}",
        "submit:!ALA s/1706211",
        "asf:cleanup",
    ]
    logged = read_events(config.logs_path)
    wrapper = [record for record in logged if record["event"].startswith("validation_")]
    assert [record["event"] for record in wrapper] == [
        "validation_run_started",
        "validation_asf_ready",
        "validation_run_completed",
    ]
    assert all(record["one_off_validation"] is True for record in wrapper)
    assert all(record["cycle_guard_bypassed"] is True for record in wrapper)


def test_validation_does_not_read_or_modify_cycle_records(tmp_path, fixture_text):
    config = config_for(tmp_path)
    store = StateStore(config.database_path)
    store.seed_completed_cycle(
        cycle_id="2026-08-19",
        cycle_start_local="2026-08-19T21:00:00+08:00",
        source="test",
    )
    before = store.list_cycles()
    events = []
    execute(tmp_path, fixture_text, events=events)
    assert StateStore(config.database_path).list_cycles() == before


def test_validation_preserves_license_deduplication(tmp_path, fixture_text):
    first_events = []
    _, _, first_asf, _ = execute(tmp_path, fixture_text, events=first_events)
    assert first_asf.submits == ["!ALA s/1706211"]

    second_events = []
    _, _, second_asf, _ = execute(tmp_path, fixture_text, events=second_events)
    assert second_asf.submits == []
    assert second_events[0] == "asf:ensure"
    assert second_events[-1] == "asf:cleanup"


def test_validation_cleans_up_after_collector_failure(tmp_path, fixture_text):
    events = []
    with pytest.raises(RuntimeError, match="network down"):
        execute(tmp_path, fixture_text, events=events, fetch_error=RuntimeError("network down"))
    assert events == ["asf:ensure", f"fetch:{INDEX_URL}", "asf:cleanup"]
    config = config_for(tmp_path)
    logged = read_events(config.logs_path)
    assert logged[-1]["event"] == "validation_run_failed"
    assert logged[-1]["cycle_guard_bypassed"] is True
