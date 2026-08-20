from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Protocol

from .asf import AsfClient, AsfHealthError, SubmissionOutcome
from .asf_lifecycle import AsfLifecycleManager
from .eventlog import EventLogger
from .models import (
    Availability,
    CandidateStatus,
    DiscoveryCandidate,
    DiscoveryIssue,
    DiscoveryResult,
)
from .parsing import ParseError, parse_detail, parse_index
from .storage import StateStore, StoredCandidate


class HtmlFetcher(Protocol):
    def get_html(self, url: str) -> str: ...


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    mode: str
    discovered: int
    issues: int
    pending: int
    submitted: int
    skipped: int
    errors: int


class CollectorService:
    def __init__(
        self,
        *,
        index_url: str,
        fetcher: HtmlFetcher,
        logger: EventLogger,
        asf_client: AsfClient,
        store: StateStore | None,
        health_wait_seconds: float = 120.0,
        health_poll_seconds: float = 2.0,
        asf_lifecycle: AsfLifecycleManager | None = None,
    ) -> None:
        self.index_url = index_url
        self.fetcher = fetcher
        self.logger = logger
        self.asf_client = asf_client
        self.store = store
        self.health_wait_seconds = health_wait_seconds
        self.health_poll_seconds = health_poll_seconds
        self.asf_lifecycle = asf_lifecycle

    def discover(self, run_id: str) -> DiscoveryResult:
        self.logger.emit("index_fetch_started", run_id=run_id, source_url=self.index_url)
        index_html = self.fetcher.get_html(self.index_url)
        entries = parse_index(index_html, self.index_url)
        self.logger.emit("index_parsed", run_id=run_id, source_url=self.index_url, relevant_posts=len(entries))

        candidates: list[DiscoveryCandidate] = []
        issues: list[DiscoveryIssue] = []
        for entry in entries:
            try:
                detail_html = self.fetcher.get_html(entry.url)
                parsed = parse_detail(detail_html, entry.url, entry.thread_id)
            except ParseError as error:
                issue = DiscoveryIssue(
                    code="detail_parse_error",
                    message=str(error),
                    source_url=entry.url,
                    thread_id=entry.thread_id,
                )
                issues.append(issue)
                self.logger.emit(
                    "detail_failed",
                    run_id=run_id,
                    source_url=entry.url,
                    thread_id=entry.thread_id,
                    error_category="parse_error",
                    message=str(error),
                )
                continue
            except Exception as error:
                issue = DiscoveryIssue(
                    code="detail_fetch_error",
                    message=f"{type(error).__name__}: {error}",
                    source_url=entry.url,
                    thread_id=entry.thread_id,
                )
                issues.append(issue)
                self.logger.emit(
                    "detail_failed",
                    run_id=run_id,
                    source_url=entry.url,
                    thread_id=entry.thread_id,
                    error_category=type(error).__name__,
                    message=str(error),
                )
                continue

            issues.extend(parsed.issues)
            for license_item in parsed.licenses:
                candidate = DiscoveryCandidate(
                    thread_id=entry.thread_id,
                    source_url=entry.url,
                    title=entry.title,
                    status_text=entry.status_text,
                    availability=entry.availability,
                    raw_command=license_item.raw_command,
                    identifier=license_item.identifier,
                )
                candidates.append(candidate)
                self.logger.emit(
                    "candidate_discovered",
                    run_id=run_id,
                    source_url=candidate.source_url,
                    thread_id=candidate.thread_id,
                    title=candidate.title,
                    availability=candidate.availability.value,
                    raw_command=candidate.raw_command,
                    normalized_command=candidate.normalized_command,
                )

        for issue in issues:
            self.logger.emit(
                "discovery_issue",
                run_id=run_id,
                source_url=issue.source_url,
                thread_id=issue.thread_id,
                decision_reason=issue.code,
                message=issue.message,
                raw_value=issue.raw_value,
            )
        return DiscoveryResult(candidates=tuple(candidates), issues=tuple(issues))

    def run(self, mode: str, *, run_id: str | None = None, scheduled: bool = False) -> RunSummary:
        if mode not in {"dry-run", "review", "automatic"}:
            raise ValueError(f"Unsupported run mode: {mode}")
        if mode != "dry-run" and self.store is None:
            raise RuntimeError("Persistent modes require a state store")

        run_id = run_id or uuid.uuid4().hex
        self.logger.emit("run_started", run_id=run_id, mode=mode, scheduled=scheduled, source_url=self.index_url)
        try:
            result = self.discover(run_id)
        except Exception as error:
            self.logger.emit(
                "run_failed",
                run_id=run_id,
                mode=mode,
                scheduled=scheduled,
                source_url=self.index_url,
                error_category=type(error).__name__,
                message=str(error),
            )
            raise

        if mode == "dry-run":
            summary = RunSummary(
                run_id=run_id,
                mode=mode,
                discovered=len(result.candidates),
                issues=len(result.issues),
                pending=0,
                submitted=0,
                skipped=0,
                errors=sum(issue.code.endswith("_error") for issue in result.issues),
            )
            self.logger.emit("run_completed", scheduled=scheduled, **asdict(summary))
            return summary

        assert self.store is not None
        for issue in result.issues:
            self.store.record_issue(run_id, issue)

        records_to_submit: list[StoredCandidate] = []
        pending = 0
        skipped = 0
        seen_record_ids: set[int] = set()
        for candidate in result.candidates:
            reason = "awaiting_review"
            if mode == "automatic":
                reason = "current_and_safe" if candidate.availability is Availability.CURRENT else "not_explicitly_current"
            record = self.store.upsert_candidate(candidate, reason=reason)
            if record.id in seen_record_ids:
                skipped += 1
                continue
            seen_record_ids.add(record.id)

            retryable_statuses = {CandidateStatus.PENDING.value}
            if scheduled:
                # A definite scheduled failure keeps the cycle incomplete and is
                # retried by Task Scheduler/logon. Unknown POST outcomes remain
                # suppressed because replaying them could duplicate a claim.
                retryable_statuses.add(CandidateStatus.FAILED.value)
            if mode == "automatic" and candidate.availability is Availability.CURRENT and record.status in retryable_statuses:
                records_to_submit.append(record)
            elif record.status in {CandidateStatus.SUBMITTED.value, CandidateStatus.REJECTED.value, CandidateStatus.UNKNOWN.value}:
                skipped += 1
            else:
                pending += 1

        submitted, submit_errors = self._submit_records(records_to_submit, mode=mode, run_id=run_id)
        summary = RunSummary(
            run_id=run_id,
            mode=mode,
            discovered=len(result.candidates),
            issues=len(result.issues),
            pending=pending + max(0, len(records_to_submit) - submitted),
            submitted=submitted,
            skipped=skipped,
            errors=submit_errors + sum(issue.code.endswith("_error") for issue in result.issues),
        )
        self.logger.emit("run_completed", scheduled=scheduled, **asdict(summary))
        return summary

    def approve(self, candidate_ids: list[int] | None = None) -> RunSummary:
        store = self._require_store()
        run_id = uuid.uuid4().hex
        pending_records = store.list_candidates((CandidateStatus.PENDING.value,))
        selected = pending_records if candidate_ids is None else tuple(record for record in pending_records if record.id in candidate_ids)
        if candidate_ids is not None and len(selected) != len(set(candidate_ids)):
            available = {record.id for record in pending_records}
            missing = sorted(set(candidate_ids) - available)
            raise ValueError(f"Candidates are missing or not pending: {missing}")
        submitted, errors = self._submit_records(list(selected), mode="review-approve", run_id=run_id)
        return RunSummary(run_id, "review-approve", len(selected), 0, len(selected) - submitted, submitted, 0, errors)

    def retry(self, candidate_id: int) -> RunSummary:
        store = self._require_store()
        record = store.get_candidate(candidate_id)
        if record is None:
            raise ValueError(f"Candidate {candidate_id} does not exist")
        if record.status not in {CandidateStatus.FAILED.value, CandidateStatus.UNKNOWN.value}:
            raise ValueError(f"Candidate {candidate_id} is not failed or unknown")
        store.set_candidate_status(candidate_id, CandidateStatus.PENDING, "manual_retry")
        refreshed = store.get_candidate(candidate_id)
        assert refreshed is not None
        run_id = uuid.uuid4().hex
        submitted, errors = self._submit_records([refreshed], mode="review-retry", run_id=run_id)
        return RunSummary(run_id, "review-retry", 1, 0, 1 - submitted, submitted, 0, errors)

    def reject(self, candidate_id: int) -> None:
        store = self._require_store()
        record = store.get_candidate(candidate_id)
        if record is None:
            raise ValueError(f"Candidate {candidate_id} does not exist")
        if record.status == CandidateStatus.SUBMITTED.value:
            raise ValueError("A submitted candidate cannot be rejected")
        store.set_candidate_status(candidate_id, CandidateStatus.REJECTED, "manual_rejection")
        self.logger.emit(
            "candidate_rejected",
            candidate_id=candidate_id,
            source_url=record.source_url,
            normalized_command=record.normalized_command,
            decision_reason="manual_rejection",
        )

    def _require_store(self) -> StateStore:
        if self.store is None:
            raise RuntimeError("This operation requires a state store")
        return self.store

    def _submit_records(self, records: list[StoredCandidate], *, mode: str, run_id: str) -> tuple[int, int]:
        if not records:
            return 0, 0
        store = self._require_store()
        try:
            if self.asf_lifecycle is not None:
                self.asf_lifecycle.ensure_available()
            else:
                self.asf_client.wait_until_healthy(self.health_wait_seconds, self.health_poll_seconds)
        except AsfHealthError as error:
            for record in records:
                store.record_attempt(
                    candidate_id=record.id,
                    run_id=run_id,
                    mode=mode,
                    outcome=SubmissionOutcome.PRE_SEND_FAILURE.value,
                    http_status=None,
                    api_success=None,
                    response_message=str(error),
                    response_result=None,
                    error_category=type(error).__name__,
                    duration_ms=0,
                )
                self.logger.emit(
                    "command_not_sent",
                    run_id=run_id,
                    candidate_id=record.id,
                    source_url=record.source_url,
                    normalized_command=record.normalized_command,
                    mode=mode,
                    decision_reason="asf_unavailable",
                    error_category=type(error).__name__,
                    message=str(error),
                )
            return 0, len(records)

        submitted = 0
        errors = 0
        for record in records:
            result = self.asf_client.submit(record.normalized_command)
            if result.outcome is SubmissionOutcome.SUBMITTED:
                status = CandidateStatus.SUBMITTED
                submitted += 1
            elif result.outcome is SubmissionOutcome.UNKNOWN:
                status = CandidateStatus.UNKNOWN
                errors += 1
            elif result.outcome is SubmissionOutcome.PRE_SEND_FAILURE:
                status = CandidateStatus.PENDING
                errors += 1
            else:
                status = CandidateStatus.FAILED
                errors += 1

            store.set_candidate_status(record.id, status, result.error_category or result.message)
            store.record_attempt(
                candidate_id=record.id,
                run_id=run_id,
                mode=mode,
                outcome=result.outcome.value,
                http_status=result.http_status,
                api_success=result.api_success,
                response_message=result.message,
                response_result=result.result,
                error_category=result.error_category,
                duration_ms=result.duration_ms,
            )
            self.logger.emit(
                "command_attempted",
                run_id=run_id,
                candidate_id=record.id,
                source_url=record.source_url,
                thread_id=record.thread_id,
                raw_command=record.raw_command,
                normalized_command=record.normalized_command,
                mode=mode,
                decision_reason=result.outcome.value,
                http_status=result.http_status,
                api_success=result.api_success,
                response_message=result.message,
                response_result=result.result,
                duration_ms=result.duration_ms,
                error_category=result.error_category,
            )
        return submitted, errors
