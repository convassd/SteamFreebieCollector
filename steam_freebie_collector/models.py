from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Availability(StrEnum):
    CURRENT = "current"
    FUTURE = "future"
    AMBIGUOUS = "ambiguous"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IndexEntry:
    thread_id: int
    url: str
    title: str
    status_text: str
    availability: Availability


@dataclass(frozen=True, slots=True)
class LicenseIdentifier:
    kind: str
    value: int

    def __post_init__(self) -> None:
        if self.kind not in {"app", "sub"}:
            raise ValueError(f"Unsupported identifier kind: {self.kind}")
        if self.value <= 0:
            raise ValueError("License identifier must be positive")

    @property
    def asf_token(self) -> str:
        prefix = "a" if self.kind == "app" else "s"
        return f"{prefix}/{self.value}"

    @property
    def normalized(self) -> str:
        return f"{self.kind}/{self.value}"


@dataclass(frozen=True, slots=True)
class ParsedLicense:
    raw_command: str
    identifier: LicenseIdentifier

    @property
    def normalized_command(self) -> str:
        return f"!ALA {self.identifier.asf_token}"


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    thread_id: int
    source_url: str
    title: str
    status_text: str
    availability: Availability
    raw_command: str
    identifier: LicenseIdentifier

    @property
    def normalized_command(self) -> str:
        return f"!ALA {self.identifier.asf_token}"


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    code: str
    message: str
    source_url: str
    thread_id: int | None = None
    raw_value: str | None = None


@dataclass(frozen=True, slots=True)
class DetailParseResult:
    licenses: tuple[ParsedLicense, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: tuple[DiscoveryCandidate, ...]
    issues: tuple[DiscoveryIssue, ...]

