from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .asf import AsfClient
from .asf_lifecycle import AsfLifecycleManager
from .config import AppConfig
from .eventlog import EventLogger
from .service import CollectorService, HtmlFetcher, RunSummary
from .storage import StateStore


def execute_one_off_validation(
    *,
    config: AppConfig,
    fetcher: HtmlFetcher,
    asf_client: AsfClient,
    store: StateStore,
    logger: EventLogger,
    lifecycle_factory: Callable[..., AsfLifecycleManager] = AsfLifecycleManager,
) -> RunSummary:
    """Run normal automatic collection without reading or changing cycle state."""
    run_id = uuid.uuid4().hex
    logger.emit(
        "validation_run_started",
        run_id=run_id,
        mode="automatic",
        scheduled=False,
        one_off_validation=True,
        cycle_guard_bypassed=True,
        source_url=config.index_url,
    )
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
        # Prove the complete dependency path even when license deduplication
        # leaves no new command to submit.
        lifecycle.ensure_available()
        logger.emit(
            "validation_asf_ready",
            run_id=run_id,
            mode="automatic",
            scheduled=False,
            one_off_validation=True,
            cycle_guard_bypassed=True,
        )
        summary = service.run("automatic", run_id=run_id, scheduled=False)
        if summary.errors:
            raise RuntimeError(f"One-off validation completed with {summary.errors} error(s)")
    except BaseException as error:
        failure = error
    finally:
        try:
            lifecycle.cleanup()
        except BaseException as cleanup_error:
            logger.emit(
                "asf_cleanup_failed",
                run_id=run_id,
                one_off_validation=True,
                cycle_guard_bypassed=True,
                error_category=type(cleanup_error).__name__,
                message=str(cleanup_error),
            )
            if failure is None:
                failure = cleanup_error

    if failure is not None:
        logger.emit(
            "validation_run_failed",
            run_id=run_id,
            mode="automatic",
            scheduled=False,
            one_off_validation=True,
            cycle_guard_bypassed=True,
            error_category=type(failure).__name__,
            message=str(failure),
        )
        raise failure

    assert summary is not None
    logger.emit(
        "validation_run_completed",
        run_id=run_id,
        mode="automatic",
        scheduled=False,
        one_off_validation=True,
        cycle_guard_bypassed=True,
        summary=asdict(summary),
    )
    return summary
