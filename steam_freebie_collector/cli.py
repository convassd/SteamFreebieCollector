from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .asf import AsfClient, AsfHealthError
from .config import AppConfig, load_config
from .eventlog import EventLogger
from .http_client import KeylolClient
from .models import CandidateStatus
from .service import CollectorService, RunSummary
from .storage import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steam-freebie-collector",
        description="Safely collect authored Keylol Steam giveaway commands through ASF IPC.",
    )
    parser.add_argument("--config", type=Path, help="Path to config.toml")
    subcommands = parser.add_subparsers(dest="operation", required=True)

    run_parser = subcommands.add_parser("run", help="Fetch and process the Keylol index")
    run_parser.add_argument("--mode", required=True, choices=("dry-run", "review", "automatic"))

    review_parser = subcommands.add_parser("review", help="Manage persisted candidates")
    review_commands = review_parser.add_subparsers(dest="review_operation", required=True)
    review_commands.add_parser("list", help="List actionable candidates")
    approve_parser = review_commands.add_parser("approve", help="Submit a pending candidate or all pending candidates")
    approve_parser.add_argument("target", help="Candidate ID or 'all'")
    reject_parser = review_commands.add_parser("reject", help="Reject a candidate")
    reject_parser.add_argument("candidate_id", type=int)
    retry_parser = review_commands.add_parser("retry", help="Retry a failed or unknown candidate")
    retry_parser.add_argument("candidate_id", type=int)

    history_parser = subcommands.add_parser("history", help="Show recent ASF attempts")
    history_parser.add_argument("--limit", type=int, default=20)
    subcommands.add_parser("health", help="Perform a read-only ASF IPC health check")
    return parser


def _build_service(config: AppConfig, *, with_store: bool) -> CollectorService:
    fetcher = KeylolClient(
        connect_timeout=config.connect_timeout_seconds,
        read_timeout=config.read_timeout_seconds,
        retry_count=config.get_retry_count,
    )
    asf_client = AsfClient(
        base_url=config.asf_base_url,
        ipc_password=os.environ.get("STEAM_FREEBIE_ASF_IPC_PASSWORD"),
    )
    return CollectorService(
        index_url=config.index_url,
        fetcher=fetcher,
        logger=EventLogger(config.logs_path),
        asf_client=asf_client,
        store=StateStore(config.database_path) if with_store else None,
        health_wait_seconds=config.asf_health_wait_seconds,
        health_poll_seconds=config.asf_health_poll_seconds,
    )


def _print_summary(summary: RunSummary) -> None:
    print(
        f"run={summary.run_id} mode={summary.mode} discovered={summary.discovered} "
        f"issues={summary.issues} pending={summary.pending} submitted={summary.submitted} "
        f"skipped={summary.skipped} errors={summary.errors}"
    )


def _print_candidates(store: StateStore) -> None:
    statuses = (
        CandidateStatus.PENDING.value,
        CandidateStatus.FAILED.value,
        CandidateStatus.UNKNOWN.value,
    )
    records = store.list_candidates(statuses)
    if not records:
        print("No actionable candidates.")
        return
    for record in records:
        print(
            f"{record.id}: {record.status} {record.availability} {record.normalized_command} "
            f"thread={record.thread_id} title={record.title}"
        )


def _print_history(store: StateStore, limit: int) -> None:
    for attempt in store.history(limit=max(1, limit)):
        print(
            f"{attempt.id}: candidate={attempt.candidate_id} at={attempt.attempted_at_utc} "
            f"mode={attempt.mode} outcome={attempt.outcome} http={attempt.http_status} "
            f"success={attempt.api_success} message={attempt.response_message or ''}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)

        if args.operation == "run":
            service = _build_service(config, with_store=args.mode != "dry-run")
            summary = service.run(args.mode)
            _print_summary(summary)
            return 2 if summary.errors else 0

        if args.operation == "health":
            service = _build_service(config, with_store=False)
            service.asf_client.wait_until_healthy(config.asf_health_wait_seconds, config.asf_health_poll_seconds)
            print(f"ASF IPC is healthy at {config.asf_base_url}")
            return 0

        store = StateStore(config.database_path)
        if args.operation == "history":
            _print_history(store, args.limit)
            return 0

        service = _build_service(config, with_store=True)
        if args.review_operation == "list":
            _print_candidates(store)
            return 0
        if args.review_operation == "approve":
            if args.target.casefold() == "all":
                candidate_ids = None
            else:
                try:
                    candidate_ids = [int(args.target)]
                except ValueError as error:
                    raise ValueError("Approval target must be a candidate ID or 'all'") from error
            summary = service.approve(candidate_ids)
            _print_summary(summary)
            return 2 if summary.errors else 0
        if args.review_operation == "reject":
            service.reject(args.candidate_id)
            print(f"Rejected candidate {args.candidate_id}.")
            return 0
        if args.review_operation == "retry":
            summary = service.retry(args.candidate_id)
            _print_summary(summary)
            return 2 if summary.errors else 0
        raise RuntimeError("Unhandled command")
    except (ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except AsfHealthError as error:
        print(f"ASF unavailable: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"fatal: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
