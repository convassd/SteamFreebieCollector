from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .asf import AsfClient
from .asf_lifecycle import AsfLifecycleManager
from .config import AppConfig
from .cycle import operational_cycle
from .eventlog import EventLogger
from .service import CollectorService, HtmlFetcher, RunSummary
from .storage import StateStore


def execute_scheduled_run(
    *,
    config: AppConfig,
    fetcher: HtmlFetcher,
    asf_client: AsfClient,
    store: StateStore,
    logger: EventLogger,
    now_factory: Callable[[], datetime] | None = None,
    lifecycle_factory: Callable[..., AsfLifecycleManager] = AsfLifecycleManager,
) -> RunSummary:
    now = now_factory() if now_factory is not None else datetime.now().astimezone()
    cycle = operational_cycle(now)
    run_id = uuid.uuid4().hex
    cycle_start_text = cycle.start_local.isoformat()
    decision = store.acquire_cycle_lease(
        cycle_id=cycle.cycle_id,
        cycle_start_local=cycle_start_text,
        lease_owner=run_id,
        lease_timeout_seconds=config.cycle_lease_timeout_seconds,
    )
    logger.emit(
        "cycle_decision",
        run_id=run_id,
        mode="automatic",
        scheduled=True,
        cycle_id=cycle.cycle_id,
        cycle_start_local=cycle_start_text,
        lease_acquired=decision.acquired,
        decision_reason=decision.reason,
        cycle_already_completed=decision.reason == "cycle_already_completed",
        cycle_in_progress=decision.reason == "cycle_in_progress",
        stale_lease_recovered=decision.stale_lease_recovered,
        existing_lease_owner=decision.lease_owner if not decision.acquired else None,
    )

    if not decision.acquired:
        logger.emit(
            "scheduled_run_skipped",
            run_id=run_id,
            mode="automatic",
            scheduled=True,
            cycle_id=cycle.cycle_id,
            cycle_start_local=cycle_start_text,
            decision_reason=decision.reason,
            cycle_already_completed=decision.reason == "cycle_already_completed",
            cycle_in_progress=decision.reason == "cycle_in_progress",
        )
        return RunSummary(run_id, "automatic-scheduled", 0, 0, 0, 0, 1, 0)

    lifecycle = lifecycle_factory(
        client=asf_client,
        executable=Path(config.asf_executable_path),
        working_directory=Path(config.asf_working_directory),
        logger=logger,
        run_id=run_id,
        health_wait_seconds=config.asf_health_wait_seconds,
        health_poll_seconds=config.asf_health_poll_seconds,
        shutdown_wait_seconds=config.asf_shutdown_wait_seconds,
    )
    service = CollectorService(
        index_url=config.index_url,
        fetcher=fetcher,
        logger=logger,
        asf_client=asf_client,
        store=store,
        health_wait_seconds=config.asf_health_wait_seconds,
        health_poll_seconds=config.asf_health_poll_seconds,
        asf_lifecycle=lifecycle,
    )

    summary: RunSummary | None = None
    failure: BaseException | None = None
    try:
        summary = service.run("automatic", run_id=run_id, scheduled=True)
        if summary.errors:
            raise RuntimeError(f"Scheduled collection completed with {summary.errors} error(s)")
    except BaseException as error:
        failure = error
    finally:
        try:
            # Service logging and SQLite transactions are closed/flushed before
            # shutdown is requested here.
            lifecycle.cleanup()
        except BaseException as cleanup_error:
            if failure is None:
                failure = cleanup_error
            else:
                logger.emit(
                    "asf_cleanup_failed",
                    run_id=run_id,
                    cycle_id=cycle.cycle_id,
                    error_category=type(cleanup_error).__name__,
                    message=str(cleanup_error),
                )

    if failure is not None:
        store.release_cycle_lease(cycle_id=cycle.cycle_id, lease_owner=run_id, error=str(failure))
        logger.emit(
            "cycle_failed",
            run_id=run_id,
            mode="automatic",
            scheduled=True,
            cycle_id=cycle.cycle_id,
            cycle_start_local=cycle_start_text,
            lease_acquired=True,
            error_category=type(failure).__name__,
            message=str(failure),
        )
        raise failure

    assert summary is not None
    store.complete_cycle(cycle_id=cycle.cycle_id, lease_owner=run_id)
    logger.emit(
        "cycle_completed",
        run_id=run_id,
        mode="automatic",
        scheduled=True,
        cycle_id=cycle.cycle_id,
        cycle_start_local=cycle_start_text,
        lease_acquired=True,
        summary=asdict(summary),
    )
    return summary
