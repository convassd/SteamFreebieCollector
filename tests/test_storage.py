from steam_freebie_collector.models import Availability, CandidateStatus, DiscoveryCandidate, LicenseIdentifier
from steam_freebie_collector.storage import StateStore


def make_candidate(*, thread_id=10, kind="sub", value=20, availability=Availability.CURRENT):
    return DiscoveryCandidate(
        thread_id=thread_id,
        source_url=f"https://keylol.com/t{thread_id}-1-1",
        title="Game",
        status_text="现已可领取",
        availability=availability,
        raw_command=f"!ALA {'s' if kind == 'sub' else 'a'}/{value}",
        identifier=LicenseIdentifier(kind, value),
    )


def test_semantic_deduplication_preserves_terminal_status(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first = store.upsert_candidate(make_candidate(thread_id=10), "first")
    store.set_candidate_status(first.id, CandidateStatus.SUBMITTED, "accepted")

    duplicate = store.upsert_candidate(make_candidate(thread_id=11), "seen_again")
    assert duplicate.id == first.id
    assert duplicate.status == CandidateStatus.SUBMITTED.value
    assert duplicate.thread_id == 11


def test_attempt_history_round_trips_response(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    candidate = store.upsert_candidate(make_candidate(), None)
    store.record_attempt(
        candidate_id=candidate.id,
        run_id="run",
        mode="automatic",
        outcome="submitted",
        http_status=200,
        api_success=True,
        response_message="OK",
        response_result={"bot": "OK"},
        error_category=None,
        duration_ms=25,
    )
    history = store.history()
    assert len(history) == 1
    assert history[0].api_success is True
    assert history[0].response_result == '{"bot":"OK"}'


def test_list_candidates_filters_status(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    pending = store.upsert_candidate(make_candidate(value=1), None)
    submitted = store.upsert_candidate(make_candidate(value=2), None)
    store.set_candidate_status(submitted.id, CandidateStatus.SUBMITTED)
    records = store.list_candidates((CandidateStatus.PENDING.value,))
    assert [record.id for record in records] == [pending.id]

