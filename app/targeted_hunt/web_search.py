from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def _clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def _unwrap_ddg(url: str) -> str:
    parsed = urlparse(html.unescape(url))
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [url])[0]
        return unquote(target)
    return html.unescape(url)


class PublicWebSearch:
    """Small provider abstraction; default backend uses DDG HTML, no API key.

    Research is intentionally limited to public pages. Failures return an empty
    result set so the worker can retry later instead of inventing evidence.
    """

    endpoint = "https://html.duckduckgo.com/html/"

    def __init__(self) -> None:
        self.timeout = float(os.getenv("TARGETED_HUNT_SEARCH_TIMEOUT", "15"))
        self.user_agent = os.getenv(
            "TARGETED_HUNT_USER_AGENT",
            "Mozilla/5.0 (compatible; HH-Agent-Targeted-Hunt/1.0)",
        )

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        try:
            response = httpx.get(
                self.endpoint,
                params={"q": query},
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:
            print(f"[TARGETED HUNT SEARCH] {type(exc).__name__}: {exc}")
            return []

        body = response.text
        links = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = re.findall(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        results: list[SearchResult] = []
        for index, (url, title) in enumerate(links[:limit]):
            results.append(
                SearchResult(
                    title=_clean(title),
                    url=_unwrap_ddg(url),
                    snippet=_clean(snippets[index]) if index < len(snippets) else "",
                )
            )
        return results
