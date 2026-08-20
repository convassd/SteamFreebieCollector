from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .eventlog import utc_now_text
from .models import CandidateStatus, DiscoveryCandidate, DiscoveryIssue


@dataclass(frozen=True, slots=True)
class StoredCandidate:
    id: int
    target_group: str
    identifier_kind: str
    identifier_value: int
    thread_id: int
    source_url: str
    title: str
    status_text: str
    availability: str
    raw_command: str
    normalized_command: str
    status: str
    first_seen_utc: str
    last_seen_utc: str
    last_reason: str | None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: int
    candidate_id: int
    run_id: str
    attempted_at_utc: str
    mode: str
    outcome: str
    http_status: int | None
    api_success: bool | None
    response_message: str | None
    response_result: str | None
    error_category: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class CycleLeaseDecision:
    acquired: bool
    reason: str
    cycle_id: str
    cycle_start_local: str
    lease_owner: str | None
    stale_lease_recovered: bool = False


@dataclass(frozen=True, slots=True)
class RunCycleRecord:
    cycle_id: str
    cycle_start_local: str
    state: str
    lease_owner: str | None
    lease_acquired_utc: str | None
    completed_utc: str | None
    completion_source: str | None
    last_error: str | None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_group TEXT NOT NULL,
                    identifier_kind TEXT NOT NULL,
                    identifier_value INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status_text TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    raw_command TEXT NOT NULL,
                    normalized_command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    first_seen_utc TEXT NOT NULL,
                    last_seen_utc TEXT NOT NULL,
                    last_reason TEXT,
                    UNIQUE(target_group, identifier_kind, identifier_value)
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                    run_id TEXT NOT NULL,
                    attempted_at_utc TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    http_status INTEGER,
                    api_success INTEGER,
                    response_message TEXT,
                    response_result TEXT,
                    error_category TEXT,
                    duration_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    thread_id INTEGER,
                    raw_value TEXT
                );

                CREATE TABLE IF NOT EXISTS run_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    cycle_start_local TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('in_progress', 'completed')),
                    lease_owner TEXT,
                    lease_acquired_utc TEXT,
                    completed_utc TEXT,
                    completion_source TEXT,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
                CREATE INDEX IF NOT EXISTS idx_attempts_candidate ON attempts(candidate_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_issues_run ON issues(run_id, id);
                CREATE INDEX IF NOT EXISTS idx_run_cycles_state ON run_cycles(state);
                """
            )

    @staticmethod
    def _utc_text(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamp must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

    def acquire_cycle_lease(
        self,
        *,
        cycle_id: str,
        cycle_start_local: str,
        lease_owner: str,
        lease_timeout_seconds: float,
        now_utc: datetime | None = None,
    ) -> CycleLeaseDecision:
        now = now_utc or datetime.now(UTC)
        now_text = self._utc_text(now)
        stale_before = now - timedelta(seconds=max(1.0, lease_timeout_seconds))

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM run_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO run_cycles (
                        cycle_id, cycle_start_local, state, lease_owner, lease_acquired_utc,
                        completed_utc, completion_source, last_error
                    ) VALUES (?, ?, 'in_progress', ?, ?, NULL, NULL, NULL)
                    """,
                    (cycle_id, cycle_start_local, lease_owner, now_text),
                )
                connection.commit()
                return CycleLeaseDecision(True, "lease_acquired", cycle_id, cycle_start_local, lease_owner)

            if row["state"] == "completed":
                connection.commit()
                return CycleLeaseDecision(False, "cycle_already_completed", cycle_id, row["cycle_start_local"], None)

            acquired_at = self._parse_utc(row["lease_acquired_utc"])
            if acquired_at > stale_before:
                connection.commit()
                return CycleLeaseDecision(False, "cycle_in_progress", cycle_id, row["cycle_start_local"], row["lease_owner"])

            previous_owner = row["lease_owner"]
            connection.execute(
                """
                UPDATE run_cycles
                SET cycle_start_local = ?, lease_owner = ?, lease_acquired_utc = ?,
                    completed_utc = NULL, completion_source = NULL,
                    last_error = ?
                WHERE cycle_id = ? AND state = 'in_progress'
                """,
                (cycle_start_local, lease_owner, now_text, f"Recovered stale lease from {previous_owner}", cycle_id),
            )
            connection.commit()
            return CycleLeaseDecision(
                True,
                "stale_lease_recovered",
                cycle_id,
                cycle_start_local,
                lease_owner,
                stale_lease_recovered=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_cycle(
        self,
        *,
        cycle_id: str,
        lease_owner: str,
        source: str = "scheduled_run",
        now_utc: datetime | None = None,
    ) -> None:
        completed = self._utc_text(now_utc or datetime.now(UTC))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE run_cycles
                SET state = 'completed', completed_utc = ?, completion_source = ?, last_error = NULL
                WHERE cycle_id = ? AND state = 'in_progress' AND lease_owner = ?
                """,
                (completed, source, cycle_id, lease_owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Cycle lease {cycle_id} is no longer owned by {lease_owner}")

    def release_cycle_lease(self, *, cycle_id: str, lease_owner: str, error: str) -> None:
        """Release a failed lease so another retry in the same cycle remains eligible."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM run_cycles WHERE cycle_id = ? AND state = 'in_progress' AND lease_owner = ?",
                (cycle_id, lease_owner),
            )

    def seed_completed_cycle(
        self,
        *,
        cycle_id: str,
        cycle_start_local: str,
        source: str,
        now_utc: datetime | None = None,
    ) -> None:
        completed = self._utc_text(now_utc or datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO run_cycles (
                    cycle_id, cycle_start_local, state, lease_owner, lease_acquired_utc,
                    completed_utc, completion_source, last_error
                ) VALUES (?, ?, 'completed', NULL, NULL, ?, ?, NULL)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    cycle_start_local = excluded.cycle_start_local,
                    state = 'completed', lease_owner = NULL, lease_acquired_utc = NULL,
                    completed_utc = excluded.completed_utc,
                    completion_source = excluded.completion_source,
                    last_error = NULL
                """,
                (cycle_id, cycle_start_local, completed, source),
            )

    def get_cycle(self, cycle_id: str) -> RunCycleRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM run_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
        return RunCycleRecord(**dict(row)) if row is not None else None

    def list_cycles(self, limit: int = 20) -> tuple[RunCycleRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM run_cycles ORDER BY cycle_id DESC LIMIT ?", (limit,)).fetchall()
        return tuple(RunCycleRecord(**dict(row)) for row in rows)

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> StoredCandidate:
        return StoredCandidate(**dict(row))

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> AttemptRecord:
        values = dict(row)
        if values["api_success"] is not None:
            values["api_success"] = bool(values["api_success"])
        return AttemptRecord(**values)

    def upsert_candidate(self, candidate: DiscoveryCandidate, reason: str | None = None) -> StoredCandidate:
        now = utc_now_text()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO candidates (
                    target_group, identifier_kind, identifier_value, thread_id, source_url,
                    title, status_text, availability, raw_command, normalized_command,
                    status, first_seen_utc, last_seen_utc, last_reason
                ) VALUES ('ASF', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_group, identifier_kind, identifier_value) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    source_url = excluded.source_url,
                    title = excluded.title,
                    status_text = excluded.status_text,
                    availability = excluded.availability,
                    raw_command = excluded.raw_command,
                    normalized_command = excluded.normalized_command,
                    last_seen_utc = excluded.last_seen_utc,
                    last_reason = COALESCE(excluded.last_reason, candidates.last_reason)
                """,
                (
                    candidate.identifier.kind,
                    candidate.identifier.value,
                    candidate.thread_id,
                    candidate.source_url,
                    candidate.title,
                    candidate.status_text,
                    candidate.availability.value,
                    candidate.raw_command,
                    candidate.normalized_command,
                    CandidateStatus.PENDING.value,
                    now,
                    now,
                    reason,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM candidates
                WHERE target_group = 'ASF' AND identifier_kind = ? AND identifier_value = ?
                """,
                (candidate.identifier.kind, candidate.identifier.value),
            ).fetchone()
        assert row is not None
        return self._candidate_from_row(row)

    def record_issue(self, run_id: str, issue: DiscoveryIssue) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO issues (run_id, observed_at_utc, code, message, source_url, thread_id, raw_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, utc_now_text(), issue.code, issue.message, issue.source_url, issue.thread_id, issue.raw_value),
            )

    def get_candidate(self, candidate_id: int) -> StoredCandidate | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return self._candidate_from_row(row) if row is not None else None

    def list_candidates(self, statuses: tuple[str, ...] | None = None) -> tuple[StoredCandidate, ...]:
        query = "SELECT * FROM candidates"
        parameters: tuple[Any, ...] = ()
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            parameters = statuses
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._candidate_from_row(row) for row in rows)

    def set_candidate_status(self, candidate_id: int, status: CandidateStatus, reason: str | None = None) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE candidates SET status = ?, last_reason = ? WHERE id = ?",
                (status.value, reason, candidate_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Candidate {candidate_id} does not exist")

    def record_attempt(
        self,
        *,
        candidate_id: int,
        run_id: str,
        mode: str,
        outcome: str,
        http_status: int | None,
        api_success: bool | None,
        response_message: str | None,
        response_result: Any,
        error_category: str | None,
        duration_ms: int,
    ) -> None:
        serialized_result = None
        if response_result is not None:
            serialized_result = (
                response_result
                if isinstance(response_result, str)
                else json.dumps(response_result, ensure_ascii=False, separators=(",", ":"), default=str)
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO attempts (
                    candidate_id, run_id, attempted_at_utc, mode, outcome, http_status,
                    api_success, response_message, response_result, error_category, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    run_id,
                    utc_now_text(),
                    mode,
                    outcome,
                    http_status,
                    None if api_success is None else int(api_success),
                    response_message,
                    serialized_result,
                    error_category,
                    duration_ms,
                ),
            )

    def history(self, limit: int = 20) -> tuple[AttemptRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return tuple(self._attempt_from_row(row) for row in rows)
