from __future__ import annotations

from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


USER_AGENT = "SteamFreebieCollector/0.1 (+personal Windows automation; read-only scraper)"


@dataclass(slots=True)
class KeylolClient:
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    retry_count: int = 3
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        retry = Retry(
            total=self.retry_count,
            connect=self.retry_count,
            read=self.retry_count,
            status=self.retry_count,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def get_html(self, url: str) -> str:
        assert self.session is not None
        response = self.session.get(url, timeout=(self.connect_timeout, self.read_timeout))
        response.raise_for_status()
        if response.encoding is None:
            response.encoding = "utf-8"
        return response.text

