from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from .asf import AsfClient
from .eventlog import EventLogger


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...


def _launch_asf(executable: Path, working_directory: Path) -> ManagedProcess:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [str(executable)],
        cwd=str(working_directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _windows_process_path(pid: int) -> Path | None:
    if os.name != "nt":
        return None
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class AsfLifecycleManager:
    def __init__(
        self,
        *,
        client: AsfClient,
        executable: Path,
        working_directory: Path,
        logger: EventLogger,
        run_id: str,
        health_wait_seconds: float,
        health_poll_seconds: float,
        shutdown_wait_seconds: float,
        launcher: Callable[[Path, Path], ManagedProcess] = _launch_asf,
        process_path_resolver: Callable[[int], Path | None] = _windows_process_path,
    ) -> None:
        self.client = client
        self.executable = executable.resolve()
        self.working_directory = working_directory.resolve()
        self.logger = logger
        self.run_id = run_id
        self.health_wait_seconds = health_wait_seconds
        self.health_poll_seconds = health_poll_seconds
        self.shutdown_wait_seconds = shutdown_wait_seconds
        self.launcher = launcher
        self.process_path_resolver = process_path_resolver
        self.owned_process: ManagedProcess | None = None
        self.preexisting_asf = False
        self._ready = False

    def ensure_available(self) -> None:
        if self._ready:
            return
        if self.client.is_healthy():
            self.preexisting_asf = True
            self._ready = True
            self.logger.emit("asf_preexisting", run_id=self.run_id, asf_started_by_run=False)
            return

        if not self.executable.is_file():
            raise FileNotFoundError(f"ASF executable was not found: {self.executable}")
        if not self.working_directory.is_dir():
            raise FileNotFoundError(f"ASF working directory was not found: {self.working_directory}")

        process = self.launcher(self.executable, self.working_directory)
        self.owned_process = process
        self.logger.emit(
            "asf_started",
            run_id=self.run_id,
            asf_started_by_run=True,
            process_id=process.pid,
            executable=str(self.executable),
        )
        self.client.wait_until_healthy(self.health_wait_seconds, self.health_poll_seconds)
        if process.poll() is not None:
            raise RuntimeError(f"ASF process {process.pid} exited before becoming usable")
        self._ready = True
        self.logger.emit(
            "asf_healthy",
            run_id=self.run_id,
            process_id=process.pid,
            asf_started_by_run=True,
        )

    def _force_stop_owned(self, process: ManagedProcess, *, reason: str) -> None:
        if process.poll() is not None:
            return
        actual_path = self.process_path_resolver(process.pid)
        if actual_path is None or actual_path.resolve() != self.executable:
            raise RuntimeError(f"Refusing to force-stop PID {process.pid}: executable identity could not be verified")
        process.terminate()
        process.wait(timeout=5.0)
        self.logger.emit(
            "asf_forced_stop",
            run_id=self.run_id,
            process_id=process.pid,
            executable=str(actual_path),
            asf_started_by_run=True,
            decision_reason=reason,
        )

    def cleanup(self) -> None:
        process = self.owned_process
        if process is None:
            self.logger.emit(
                "asf_cleanup_skipped",
                run_id=self.run_id,
                reason="preexisting_asf" if self.preexisting_asf else "asf_not_needed",
                asf_started_by_run=False,
            )
            return
        if process.poll() is not None:
            self.logger.emit("asf_already_exited", run_id=self.run_id, process_id=process.pid, asf_started_by_run=True)
            return

        self.logger.emit("asf_shutdown_requested", run_id=self.run_id, process_id=process.pid, asf_started_by_run=True)
        try:
            self.client.request_exit()
        except BaseException as error:
            self.logger.emit(
                "asf_graceful_shutdown_failed",
                run_id=self.run_id,
                process_id=process.pid,
                asf_started_by_run=True,
                error_category=type(error).__name__,
                message=str(error),
            )
            self._force_stop_owned(process, reason="graceful_shutdown_request_failed")
            return
        try:
            process.wait(timeout=self.shutdown_wait_seconds)
        except subprocess.TimeoutExpired:
            self._force_stop_owned(process, reason="graceful_shutdown_timeout")
        else:
            self.logger.emit("asf_stopped", run_id=self.run_id, process_id=process.pid, asf_started_by_run=True)
