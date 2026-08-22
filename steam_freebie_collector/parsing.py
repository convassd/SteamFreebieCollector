from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import (
    Availability,
    DetailParseResult,
    DiscoveryIssue,
    IndexEntry,
    LicenseIdentifier,
    ParsedLicense,
)


class ParseError(RuntimeError):
    """Raised when a required, safety-critical page structure is missing."""


_THREAD_PATH_RE = re.compile(r"^/t(?P<thread_id>[1-9]\d*)-\d+-\d+/?$")
_COMMAND_RE = re.compile(r"^\s*!(?P<command>ALA|AL|ADDLICENSE|ADDLICENCE)\b(?P<args>.*)$", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(r"^(?:(?P<kind>a|app|s|sub)/)?(?P<value>[1-9]\d*)$", re.IGNORECASE)
_CANONICAL_COMMAND_RE = re.compile(r"^!ALA [as]/[1-9]\d*$")
_WIDGET_HREF_RE = re.compile(r"^#asf(?P<id>[1-9]\d*)$")
_WIDGET_DIRECT_ONCLICK_RE = re.compile(
    r"""^\s*(?:javascript:\s*)?setCopy\(\s*
    (?P<command_quote>['\"])!addlicense[ ]asf[ ]a/(?P<id>[1-9]\d*)(?P=command_quote)
    (?:\s*,\s*(?P<message_quote>['\"])[^'\"]*(?P=message_quote))?
    \s*\)\s*;?\s*(?:return\s+false\s*;?)?\s*$""",
    re.VERBOSE,
)
_WIDGET_CONCAT_ONCLICK_RE = re.compile(
    r"""^\s*(?:javascript:\s*)?setCopy\(\s*
    (?P<prefix_quote>['\"])!addlicense[ ]asf[ ]a/(?P=prefix_quote)\s*\+\s*
    this\.href\.split\(\s*(?P<fragment_quote>['\"])\#asf(?P=fragment_quote)\s*\)\s*\[\s*1\s*\]
    (?:\s*,\s*(?P<message_quote>['\"])[^'\"]*(?P=message_quote))?
    \s*\)\s*;?\s*(?:return\s+false\s*;?)?\s*$""",
    re.VERBOSE,
)
_WIDGET_LABELS = {"复制ASF代码", "複製ASF代碼"}
_NEGATIVE_AVAILABILITY_MARKERS = (
    "预告",
    "預告",
    "即将",
    "即將",
    "才开放",
    "才開放",
    "未开始",
    "未開始",
    "尚未",
)
_CURRENT_AVAILABILITY_RE = re.compile(
    r"(?:(?:现|現)(?:已|在)可(?:领取|領取)|今日可(?:(?:限时|限時))?(?:免费|免費)(?:领取|領取))"
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def classify_availability(value: str) -> Availability:
    normalized = normalize_text(value)
    if any(marker in normalized for marker in _NEGATIVE_AVAILABILITY_MARKERS):
        return Availability.FUTURE
    if _CURRENT_AVAILABILITY_RE.search(normalized):
        return Availability.CURRENT
    return Availability.AMBIGUOUS


def canonicalize_thread_url(href: str, base_url: str) -> tuple[int, str] | None:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "keylol.com":
        return None

    match = _THREAD_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None

    thread_id = int(match.group("thread_id"))
    return thread_id, f"https://keylol.com/t{thread_id}-1-1"


def _has_class(tag: Tag, expected: str) -> bool:
    classes = tag.get("class", [])
    return expected in classes


def parse_index(html: str, base_url: str) -> tuple[IndexEntry, ...]:
    soup = BeautifulSoup(html, "html.parser")
    first_post = soup.select_one('td[id^="postmessage_"]')
    if first_post is None:
        raise ParseError("Keylol index is missing the first-post body")

    steam_headers = [
        header
        for header in first_post.select("h1.KyloStylisedHeader0")
        if normalize_text(header.get_text(" ", strip=True)).casefold() == "steam"
    ]
    if not steam_headers:
        raise ParseError("Keylol index is missing the Steam platform section")

    entries_by_thread: dict[int, IndexEntry] = {}
    for header in steam_headers:
        pending_titles: list[str] = []
        pending_text: list[str] = []

        for element in header.next_elements:
            if isinstance(element, Tag):
                if element is not header and element.name == "h1" and _has_class(element, "KyloStylisedHeader0"):
                    break

                if element.name == "h3" and _has_class(element, "KyloStylisedHeader2"):
                    title = normalize_text(element.get_text(" ", strip=True))
                    if title and title not in pending_titles:
                        pending_titles.append(title)
                    continue

                if element.name == "a" and pending_titles:
                    href = element.get("href")
                    if not isinstance(href, str):
                        continue
                    canonical = canonicalize_thread_url(href, base_url)
                    if canonical is None:
                        continue

                    thread_id, url = canonical
                    status_text = normalize_text(" ".join(pending_text))
                    entry = IndexEntry(
                        thread_id=thread_id,
                        url=url,
                        title=" / ".join(pending_titles),
                        status_text=status_text,
                        availability=classify_availability(status_text),
                    )
                    entries_by_thread.setdefault(thread_id, entry)
                    pending_titles = []
                    pending_text = []
                    continue

            if isinstance(element, NavigableString) and pending_titles:
                text = normalize_text(str(element))
                if text:
                    pending_text.append(text)

    return tuple(entries_by_thread.values())


def _command_lines(block: Tag) -> Iterable[str]:
    items = block.find_all("li")
    sources = items if items else [block]
    for source in sources:
        for line in source.get_text("\n", strip=True).splitlines():
            normalized = normalize_text(line)
            if normalized:
                yield normalized


def parse_add_license_command(line: str) -> tuple[LicenseIdentifier, ...] | None:
    match = _COMMAND_RE.fullmatch(line)
    if not match:
        return None

    command = match.group("command").upper()
    args = match.group("args").strip()
    if not args:
        return None

    tokens = [token for token in re.split(r"[\s,]+", args) if token]
    if command != "ALA" and tokens and tokens[0].upper() == "ASF":
        tokens = tokens[1:]
    if not tokens:
        return None

    identifiers: list[LicenseIdentifier] = []
    seen: set[tuple[str, int]] = set()
    for token in tokens:
        identifier_match = _IDENTIFIER_RE.fullmatch(token)
        if not identifier_match:
            return None
        raw_kind = identifier_match.group("kind")
        # ASF's backwards-compatible syntax treats every independently bare
        # numeric license ID as a sub ID. It does not inherit a prior token's
        # explicit app/sub type.
        kind = "app" if raw_kind is not None and raw_kind.lower() in {"a", "app"} else "sub"
        value = int(identifier_match.group("value"))
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            identifiers.append(LicenseIdentifier(kind=kind, value=value))

    return tuple(identifiers)


def is_canonical_command(command: str) -> bool:
    return _CANONICAL_COMMAND_RE.fullmatch(command) is not None


def _widget_fallback_identifiers(first_post: Tag) -> tuple[LicenseIdentifier, ...]:
    identifiers: list[LicenseIdentifier] = []
    seen: set[tuple[str, int]] = set()

    for anchor in first_post.find_all("a"):
        if normalize_text(anchor.get_text(" ", strip=True)) not in _WIDGET_LABELS:
            continue

        href = anchor.get("href")
        onclick = anchor.get("onclick")
        if not isinstance(href, str) or not isinstance(onclick, str):
            continue

        href_match = _WIDGET_HREF_RE.fullmatch(href.strip())
        if href_match is None:
            continue
        href_id = int(href_match.group("id"))

        direct_match = _WIDGET_DIRECT_ONCLICK_RE.fullmatch(onclick)
        if direct_match is not None:
            if int(direct_match.group("id")) != href_id:
                continue
        elif _WIDGET_CONCAT_ONCLICK_RE.fullmatch(onclick) is None:
            continue

        key = ("app", href_id)
        if key not in seen:
            seen.add(key)
            identifiers.append(LicenseIdentifier(kind="app", value=href_id))

    return tuple(identifiers)


def parse_detail(html: str, source_url: str, thread_id: int) -> DetailParseResult:
    soup = BeautifulSoup(html, "html.parser")
    first_post = soup.select_one('td[id^="postmessage_"]')
    if first_post is None:
        raise ParseError(f"Thread {thread_id} is missing the first-post body")

    parsed: list[ParsedLicense] = []
    issues: list[DiscoveryIssue] = []
    seen: set[tuple[str, int]] = set()

    for block in first_post.select(".blockcode"):
        for line in _command_lines(block):
            identifiers = parse_add_license_command(line)
            if identifiers is None:
                if line.startswith("!"):
                    issues.append(
                        DiscoveryIssue(
                            code="unsupported_command",
                            message="Rejected a non-allowlisted or malformed ASF command",
                            source_url=source_url,
                            thread_id=thread_id,
                            raw_value=line,
                        )
                    )
                continue

            for identifier in identifiers:
                key = (identifier.kind, identifier.value)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(ParsedLicense(raw_command=line, identifier=identifier))

    if not parsed:
        for identifier in _widget_fallback_identifiers(first_post):
            key = (identifier.kind, identifier.value)
            if key in seen:
                continue
            seen.add(key)
            parsed.append(
                ParsedLicense(
                    raw_command=f"widget_copy_fallback {identifier.normalized}",
                    identifier=identifier,
                    provenance="widget_copy_fallback",
                )
            )

    if not parsed:
        issues.append(
            DiscoveryIssue(
                code="missing_supported_command",
                message="The original post has no supported authored add-license command or recognized ASF-copy widget",
                source_url=source_url,
                thread_id=thread_id,
            )
        )

    return DetailParseResult(licenses=tuple(parsed), issues=tuple(issues))
