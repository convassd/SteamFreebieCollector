from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

import requests

from .parsing import is_canonical_command


class SubmissionOutcome(StrEnum):
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    PRE_SEND_FAILURE = "pre_send_failure"


class AsfHealthError(RuntimeError):
    pass


class AsfAuthenticationError(AsfHealthError):
    pass


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    outcome: SubmissionOutcome
    http_status: int | None
    api_success: bool | None
    message: str | None
    result: Any
    error_category: str | None
    duration_ms: int


@dataclass(slots=True)
class AsfClient:
    base_url: str
    ipc_password: str | None = None
    session: requests.Session | None = None
    connect_timeout: float = 3.0
    read_timeout: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.session is None:
            self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.ipc_password:
            headers["Authentication"] = self.ipc_password
        return headers

    def is_healthy(self) -> bool:
        assert self.session is not None
        try:
            response = self.session.get(
                f"{self.base_url}/Api/ASF",
                headers=self._headers(),
                timeout=(self.connect_timeout, min(self.read_timeout, 5.0)),
            )
        except requests.RequestException:
            return False
        if response.status_code in {401, 403}:
            raise AsfAuthenticationError(f"ASF IPC authentication failed with HTTP {response.status_code}")
        if not response.ok:
            return False
        try:
            return response.json().get("Success") is True
        except ValueError:
            return False

    def wait_until_healthy(self, wait_seconds: float, poll_seconds: float) -> None:
        assert self.session is not None
        deadline = self.monotonic() + max(0.0, wait_seconds)
        last_error = "ASF did not become healthy"

        while True:
            try:
                response = self.session.get(
                    f"{self.base_url}/Api/ASF",
                    headers=self._headers(),
                    timeout=(self.connect_timeout, min(self.read_timeout, 10.0)),
                )
                if response.status_code in {401, 403}:
                    raise AsfAuthenticationError(f"ASF IPC authentication failed with HTTP {response.status_code}")
                if response.ok:
                    try:
                        payload = response.json()
                    except ValueError:
                        last_error = "ASF health endpoint returned malformed JSON"
                    else:
                        if payload.get("Success") is True:
                            return
                        last_error = f"ASF health endpoint reported failure: {payload.get('Message', 'unknown error')}"
                else:
                    last_error = f"ASF health endpoint returned HTTP {response.status_code}"
            except AsfAuthenticationError:
                raise
            except requests.RequestException as error:
                last_error = f"ASF health request failed: {type(error).__name__}"

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise AsfHealthError(last_error)
            self.sleep(min(max(0.05, poll_seconds), remaining))

    def submit(self, command: str) -> SubmissionResult:
        if not is_canonical_command(command):
            raise ValueError("Refusing to submit a non-canonical ASF command")

        assert self.session is not None
        started = self.monotonic()
        try:
            response = self.session.post(
                f"{self.base_url}/Api/Command",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"Command": command},
                timeout=(self.connect_timeout, self.read_timeout),
            )
        except requests.ConnectTimeout as error:
            return self._error_result(started, SubmissionOutcome.PRE_SEND_FAILURE, error)
        except (requests.ReadTimeout, requests.ConnectionError, requests.Timeout) as error:
            return self._error_result(started, SubmissionOutcome.UNKNOWN, error)
        except requests.RequestException as error:
            return self._error_result(started, SubmissionOutcome.UNKNOWN, error)

        duration_ms = round((self.monotonic() - started) * 1000)
        if not response.ok:
            return SubmissionResult(
                outcome=SubmissionOutcome.FAILED,
                http_status=response.status_code,
                api_success=None,
                message=f"ASF returned HTTP {response.status_code}",
                result=response.text,
                error_category="http_error",
                duration_ms=duration_ms,
            )

        try:
            payload = response.json()
        except ValueError:
            return SubmissionResult(
                outcome=SubmissionOutcome.UNKNOWN,
                http_status=response.status_code,
                api_success=None,
                message="ASF returned malformed JSON after accepting the POST",
                result=response.text,
                error_category="malformed_json",
                duration_ms=duration_ms,
            )

        api_success = payload.get("Success") is True
        return SubmissionResult(
            outcome=SubmissionOutcome.SUBMITTED if api_success else SubmissionOutcome.FAILED,
            http_status=response.status_code,
            api_success=api_success,
            message=payload.get("Message"),
            result=payload.get("Result"),
            error_category=None if api_success else "asf_rejected",
            duration_ms=duration_ms,
        )

    def request_exit(self) -> None:
        """Request ASF shutdown with a fixed lifecycle-only command.

        This method intentionally accepts no command argument and is separate from
        the strict webpage-derived add-license submission path.
        """
        assert self.session is not None
        try:
            response = self.session.post(
                f"{self.base_url}/Api/Command",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"Command": "!exit"},
                timeout=(self.connect_timeout, min(self.read_timeout, 10.0)),
            )
            if response.status_code in {401, 403}:
                raise AsfAuthenticationError(f"ASF IPC authentication failed with HTTP {response.status_code}")
        except AsfAuthenticationError:
            raise
        except requests.RequestException:
            # ASF can close IPC while the shutdown response is in flight. The
            # lifecycle manager verifies the exact owned process actually exits.
            return


    def _error_result(
        self,
        started: float,
        outcome: SubmissionOutcome,
        error: requests.RequestException,
    ) -> SubmissionResult:
        return SubmissionResult(
            outcome=outcome,
            http_status=None,
            api_success=None,
            message=str(error),
            result=None,
            error_category=type(error).__name__,
            duration_ms=round((self.monotonic() - started) * 1000),
        )
