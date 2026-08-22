import json
from dataclasses import dataclass

from steam_freebie_collector.asf import SubmissionOutcome, SubmissionResult
from steam_freebie_collector.eventlog import EventLogger
from steam_freebie_collector.models import CandidateStatus
from steam_freebie_collector.service import CollectorService
from steam_freebie_collector.storage import StateStore


INDEX_URL = "https://keylol.com/t572814-1-1"


class MappingFetcher:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_html(self, url):
        return self.mapping[url]


@dataclass
class FakeAsfClient:
    healthy: bool = True

    def __post_init__(self):
        self.commands = []
        self.health_checks = 0

    def wait_until_healthy(self, wait_seconds, poll_seconds):
        self.health_checks += 1
        if not self.healthy:
            from steam_freebie_collector.asf import AsfHealthError

            raise AsfHealthError("offline")

    def submit(self, command):
        self.commands.append(command)
        return SubmissionResult(SubmissionOutcome.SUBMITTED, 200, True, "OK", "done", None, 1)


def build_service(tmp_path, mapping, *, store, asf=None):
    return CollectorService(
        index_url=INDEX_URL,
        fetcher=MappingFetcher(mapping),
        logger=EventLogger(tmp_path / "logs"),
        asf_client=asf or FakeAsfClient(),
        store=store,
        health_wait_seconds=0,
        health_poll_seconds=0.01,
    )


def test_dry_run_never_creates_state_or_calls_asf(tmp_path, fixture_text):
    asf = FakeAsfClient()
    service = build_service(
        tmp_path,
        {
            INDEX_URL: fixture_text("current_index.html"),
            "https://keylol.com/t1045840-1-1": fixture_text("current_detail.html"),
        },
        store=None,
        asf=asf,
    )
    summary = service.run("dry-run")
    assert summary.discovered == 1
    assert not asf.commands
    assert asf.health_checks == 0
    assert not (tmp_path / "state.sqlite3").exists()


def test_review_persists_without_sending(tmp_path, fixture_text):
    store = StateStore(tmp_path / "state.sqlite3")
    asf = FakeAsfClient()
    service = build_service(
        tmp_path,
        {
            INDEX_URL: fixture_text("current_index.html"),
            "https://keylol.com/t1045840-1-1": fixture_text("current_detail.html"),
        },
        store=store,
        asf=asf,
    )
    summary = service.run("review")
    records = store.list_candidates()
    assert summary.pending == 1
    assert records[0].status == CandidateStatus.PENDING.value
    assert not asf.commands


def test_automatic_submits_current_once_and_deduplicates(tmp_path, fixture_text):
    store = StateStore(tmp_path / "state.sqlite3")
    asf = FakeAsfClient()
    mapping = {
        INDEX_URL: fixture_text("current_index.html"),
        "https://keylol.com/t1045840-1-1": fixture_text("current_detail.html"),
    }
    service = build_service(tmp_path, mapping, store=store, asf=asf)
    first = service.run("automatic")
    second = service.run("automatic")
    assert first.submitted == 1
    assert second.submitted == 0
    assert second.skipped == 1
    assert asf.commands == ["!ALA s/1706211"]


def test_automatic_queues_future_and_ambiguous(tmp_path, fixture_text):
    store = StateStore(tmp_path / "state.sqlite3")
    asf = FakeAsfClient()
    details = {
        "https://keylol.com/t111-1-1": '<td id="postmessage_1"><div class="blockcode"><li>!ALA s/111</li></div></td>',
        "https://keylol.com/t222-1-1": '<td id="postmessage_2"><div class="blockcode"><li>!ALA s/222</li></div></td>',
        "https://keylol.com/t333-1-1": '<td id="postmessage_3"><div class="blockcode"><li>!ALA s/333</li></div></td>',
    }
    service = build_service(
        tmp_path,
        {INDEX_URL: fixture_text("edge_index.html"), **details},
        store=store,
        asf=asf,
    )
    summary = service.run("automatic")
    assert summary.submitted == 1
    assert summary.pending == 2
    assert asf.commands == ["!ALA s/111"]


def test_widget_fallback_is_canonicalized_submitted_and_logged_with_safe_provenance(tmp_path, fixture_text):
    store = StateStore(tmp_path / "state.sqlite3")
    asf = FakeAsfClient()
    service = build_service(
        tmp_path,
        {
            INDEX_URL: fixture_text("current_index.html"),
            "https://keylol.com/t1045840-1-1": fixture_text("missing_detail.html"),
        },
        store=store,
        asf=asf,
    )
    summary = service.run("automatic")
    assert summary.discovered == 1
    assert summary.issues == 0
    assert asf.commands == ["!ALA a/123"]
    record = store.list_candidates()[0]
    assert record.raw_command == "widget_copy_fallback app/123"

    logged = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    discovered = next(event for event in logged if event["event"] == "candidate_discovered")
    assert discovered["command_provenance"] == "widget_copy_fallback"
    assert discovered["raw_command"] == "widget_copy_fallback app/123"
    assert "setCopy" not in json.dumps(logged)


def test_missing_command_is_recorded_without_submission(tmp_path, fixture_text):
    store = StateStore(tmp_path / "state.sqlite3")
    asf = FakeAsfClient()
    service = build_service(
        tmp_path,
        {
            INDEX_URL: fixture_text("current_index.html"),
            "https://keylol.com/t1045840-1-1": fixture_text("no_supported_detail.html"),
        },
        store=store,
        asf=asf,
    )
    summary = service.run("automatic")
    assert summary.discovered == 0
    assert summary.issues == 1
    assert not asf.commands
