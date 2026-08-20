from pathlib import Path

from steam_freebie_collector.asf_lifecycle import AsfLifecycleManager
from steam_freebie_collector.eventlog import EventLogger


class FakeProcess:
    def __init__(self, pid=4242):
        self.pid = pid
        self.exited = False
        self.terminated = False

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout=None):
        if not self.exited:
            raise AssertionError("process was not asked to exit")
        return 0

    def terminate(self):
        self.terminated = True
        self.exited = True


class LifecycleAsfClient:
    def __init__(self, healthy):
        self.healthy = healthy
        self.process = None
        self.health_waits = 0
        self.exit_requests = 0

    def is_healthy(self):
        return self.healthy

    def wait_until_healthy(self, wait_seconds, poll_seconds):
        self.health_waits += 1
        self.healthy = True

    def request_exit(self):
        self.exit_requests += 1
        if self.process is not None:
            self.process.exited = True


def make_paths(tmp_path):
    executable = tmp_path / "ArchiSteamFarm.exe"
    executable.write_bytes(b"test")
    return executable, tmp_path


def test_asf_started_by_run_is_gracefully_shut_down(tmp_path):
    executable, working = make_paths(tmp_path)
    client = LifecycleAsfClient(healthy=False)
    launched = []

    def launcher(exe, cwd):
        process = FakeProcess()
        client.process = process
        launched.append((exe, cwd, process))
        return process

    manager = AsfLifecycleManager(
        client=client,
        executable=executable,
        working_directory=working,
        logger=EventLogger(tmp_path / "logs"),
        run_id="run",
        health_wait_seconds=10,
        health_poll_seconds=1,
        shutdown_wait_seconds=5,
        launcher=launcher,
    )
    manager.ensure_available()
    manager.cleanup()
    assert len(launched) == 1
    assert client.health_waits == 1
    assert client.exit_requests == 1
    assert launched[0][2].exited
    assert not launched[0][2].terminated


def test_preexisting_asf_is_left_running(tmp_path):
    executable, working = make_paths(tmp_path)
    client = LifecycleAsfClient(healthy=True)
    launches = []
    manager = AsfLifecycleManager(
        client=client,
        executable=executable,
        working_directory=working,
        logger=EventLogger(tmp_path / "logs"),
        run_id="run",
        health_wait_seconds=10,
        health_poll_seconds=1,
        shutdown_wait_seconds=5,
        launcher=lambda exe, cwd: launches.append((exe, cwd)),
    )
    manager.ensure_available()
    manager.cleanup()
    assert launches == []
    assert client.exit_requests == 0


def test_ensure_available_is_idempotent(tmp_path):
    executable, working = make_paths(tmp_path)
    client = LifecycleAsfClient(healthy=True)
    manager = AsfLifecycleManager(
        client=client,
        executable=executable,
        working_directory=working,
        logger=EventLogger(tmp_path / "logs"),
        run_id="run",
        health_wait_seconds=10,
        health_poll_seconds=1,
        shutdown_wait_seconds=5,
    )
    manager.ensure_available()
    manager.ensure_available()
    manager.cleanup()
    log = next((tmp_path / "logs").glob("*.jsonl")).read_text(encoding="utf-8")
    assert log.count('"event":"asf_preexisting"') == 1


def test_failed_graceful_request_force_stops_only_verified_owned_process(tmp_path):
    executable, working = make_paths(tmp_path)
    client = LifecycleAsfClient(healthy=False)

    def failing_exit():
        client.exit_requests += 1
        raise RuntimeError("IPC unavailable")

    client.request_exit = failing_exit
    process = FakeProcess(pid=5151)
    manager = AsfLifecycleManager(
        client=client,
        executable=executable,
        working_directory=working,
        logger=EventLogger(tmp_path / "logs"),
        run_id="run",
        health_wait_seconds=10,
        health_poll_seconds=1,
        shutdown_wait_seconds=5,
        launcher=lambda exe, cwd: process,
        process_path_resolver=lambda pid: executable,
    )
    manager.ensure_available()
    manager.cleanup()
    assert client.exit_requests == 1
    assert process.terminated
