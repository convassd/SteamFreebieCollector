from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from steam_freebie_collector.asf import SubmissionOutcome, SubmissionResult
from steam_freebie_collector.config import load_config
from steam_freebie_collector.eventlog import EventLogger
from steam_freebie_collector.scheduled import execute_scheduled_run
from steam_freebie_collector.storage import StateStore


SGT = timezone(timedelta(hours=8))
NOW_2100 = datetime(2026, 8, 20, 21, 0, tzinfo=SGT)
NOW_LOGON = datetime(2026, 8, 21, 9, 0, tzinfo=SGT)
INDEX_URL = "https://keylol.com/t572814-1-1"
DETAIL_URL = "https://keylol.com/t1045840-1-1"


class CountingFetcher:
    def __init__(self, mapping=None, error=None):
        self.mapping = mapping or {}
        self.error = error
        self.calls = []

    def get_html(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.mapping[url]


class FakeAsf:
    def __init__(self, *, submit_error=None, results=None):
        self.submit_error = submit_error
        self.results = list(results or [])
        self.submits = []

    def submit(self, command):
        self.submits.append(command)
        if self.submit_error:
            raise self.submit_error
        if self.results:
            return self.results.pop(0)
        return SubmissionResult(SubmissionOutcome.SUBMITTED, 200, True, "OK", "done", None, 1)


class FakeLifecycle:
    instances = []

    def __init__(self, **kwargs):
        self.ensure_calls = 0
        self.cleanup_calls = 0
        self.events = kwargs.pop("events", None)
        FakeLifecycle.instances.append(self)

    def ensure_available(self):
        self.ensure_calls += 1
        if self.events is not None:
            self.events.append("ensure")

    def cleanup(self):
        self.cleanup_calls += 1
        if self.events is not None:
            self.events.append("cleanup")


def config_for(tmp_path):
    return replace(
        load_config(),
        database_path=tmp_path / "state.sqlite3",
        logs_path=tmp_path / "logs",
        cycle_lease_timeout_seconds=900,
    )


def mapping(fixture_text):
    return {INDEX_URL: fixture_text("current_index.html"), DETAIL_URL: fixture_text("current_detail.html")}


def execute(tmp_path, fixture_text, *, now, fetcher=None, asf=None, lifecycle_factory=FakeLifecycle):
    config = config_for(tmp_path)
    return execute_scheduled_run(
        config=config,
        fetcher=fetcher or CountingFetcher(mapping(fixture_text)),
        asf_client=asf or FakeAsf(),
        store=StateStore(config.database_path),
        logger=EventLogger(config.logs_path),
        now_factory=lambda: now,
        lifecycle_factory=lifecycle_factory,
    )


def test_daily_run_then_logon_same_cycle_skips_before_fetch_or_asf(tmp_path, fixture_text):
    first_fetcher = CountingFetcher(mapping(fixture_text))
    execute(tmp_path, fixture_text, now=NOW_2100, fetcher=first_fetcher)
    second_fetcher = CountingFetcher(mapping(fixture_text))
    lifecycle_calls = []

    def forbidden_lifecycle(**kwargs):
        lifecycle_calls.append(kwargs)
        raise AssertionError("ASF lifecycle must not be constructed for a completed cycle")

    summary = execute(tmp_path, fixture_text, now=NOW_LOGON, fetcher=second_fetcher, lifecycle_factory=forbidden_lifecycle)
    assert summary.skipped == 1
    assert second_fetcher.calls == []
    assert lifecycle_calls == []


def test_missed_daily_run_is_performed_at_logon(tmp_path, fixture_text):
    fetcher = CountingFetcher(mapping(fixture_text))
    summary = execute(tmp_path, fixture_text, now=NOW_LOGON, fetcher=fetcher)
    assert summary.discovered == 1
    assert fetcher.calls == [INDEX_URL, DETAIL_URL]
    assert StateStore(tmp_path / "state.sqlite3").get_cycle("2026-08-20").state == "completed"


def test_failed_run_remains_eligible_for_same_cycle_retry(tmp_path, fixture_text):
    failing = CountingFetcher(error=RuntimeError("network down"))
    with pytest.raises(RuntimeError, match="network down"):
        execute(tmp_path, fixture_text, now=NOW_2100, fetcher=failing)
    assert StateStore(tmp_path / "state.sqlite3").get_cycle("2026-08-20") is None

    successful = CountingFetcher(mapping(fixture_text))
    summary = execute(tmp_path, fixture_text, now=NOW_LOGON, fetcher=successful)
    assert summary.errors == 0
    assert successful.calls


def test_definite_asf_failure_is_retried_in_same_scheduled_cycle(tmp_path, fixture_text):
    rejected = SubmissionResult(SubmissionOutcome.FAILED, 200, False, "failed", None, "asf_rejected", 1)
    asf = FakeAsf(results=[rejected])
    with pytest.raises(RuntimeError, match="1 error"):
        execute(tmp_path, fixture_text, now=NOW_2100, asf=asf)
    assert StateStore(tmp_path / "state.sqlite3").get_cycle("2026-08-20") is None

    retry_asf = FakeAsf()
    summary = execute(tmp_path, fixture_text, now=NOW_LOGON, asf=retry_asf)
    assert summary.submitted == 1
    assert retry_asf.submits == ["!ALA s/1706211"]


def test_same_cycle_skip_logs_decision_without_fetch_or_asf(tmp_path, fixture_text):
    config = config_for(tmp_path)
    store = StateStore(config.database_path)
    store.seed_completed_cycle(
        cycle_id="2026-08-20",
        cycle_start_local="2026-08-20T21:00:00+08:00",
        source="test",
    )
    fetcher = CountingFetcher(error=AssertionError("fetch must not happen"))
    summary = execute_scheduled_run(
        config=config,
        fetcher=fetcher,
        asf_client=FakeAsf(),
        store=store,
        logger=EventLogger(config.logs_path),
        now_factory=lambda: NOW_LOGON,
        lifecycle_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("ASF must not start")),
    )
    assert summary.skipped == 1
    text = next(config.logs_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert '"cycle_already_completed":true' in text
    assert '"index_fetch_started"' not in text
    assert fetcher.calls == []


def test_cleanup_occurs_after_collector_failure_with_asf_needed(tmp_path, fixture_text):
    events = []

    class TrackingLifecycle(FakeLifecycle):
        def __init__(self, **kwargs):
            super().__init__(events=events)

    asf = FakeAsf(submit_error=RuntimeError("submission exploded"))
    with pytest.raises(RuntimeError, match="submission exploded"):
        execute(tmp_path, fixture_text, now=NOW_2100, asf=asf, lifecycle_factory=TrackingLifecycle)
    assert events == ["ensure", "cleanup"]
    assert StateStore(tmp_path / "state.sqlite3").get_cycle("2026-08-20") is None
