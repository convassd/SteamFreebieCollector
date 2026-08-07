from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
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

                CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
                CREATE INDEX IF NOT EXISTS idx_attempts_candidate ON attempts(candidate_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_issues_run ON issues(run_id, id);
                """
            )

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
