"""Utility helpers for the site crawler."""

import re
from urllib.parse import urlparse, urljoin, urlunparse


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Lowercases scheme and hostname
    - Removes query strings and fragments
    - Normalizes trailing slashes (treats /path and /path/ as equivalent)
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    # Rebuild without query or fragment
    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized


def extract_same_domain_links(html: str, base_domain: str, base_url: str) -> list[str]:
    """Extract and return normalized same-domain links from rendered HTML.

    Parses all <a href> attributes, keeps only http/https links whose hostname
    matches *base_domain*, resolves relative URLs against *base_url*, and
    returns deduplicated normalized URLs.

    Discards: javascript:, mailto:, tel:, and fragment-only links.
    """
    from html.parser import HTMLParser

    class _LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.hrefs: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() == "a":
                for name, value in attrs:
                    if name.lower() == "href" and value:
                        self.hrefs.append(value)

    parser = _LinkParser()
    parser.feed(html)

    results: list[str] = []
    seen: set[str] = set()

    for href in parser.hrefs:
        href = href.strip()
        # Skip non-navigable schemes
        lower = href.lower()
        if lower.startswith(("javascript:", "mailto:", "tel:")):
            continue
        # Skip fragment-only links
        if href.startswith("#"):
            continue

        # Resolve relative URLs
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        # Keep only http/https
        if parsed.scheme not in ("http", "https"):
            continue

        # Filter to same domain (hostname only, no port)
        hostname = parsed.hostname or ""
        if hostname != base_domain:
            continue

        normalized = normalize_url(absolute)
        if normalized not in seen:
            seen.add(normalized)
            results.append(normalized)

    return results


def generate_file_name_from_url(url: str) -> str:
    """Derive a safe PDF filename from a URL.

    Steps:
    1. Lowercase the URL.
    2. Replace all non-alphanumeric characters with hyphens.
    3. Collapse consecutive hyphens into a single hyphen.
    4. Strip leading and trailing hyphens.
    5. Truncate the slug to 200 characters.
    6. Append '.pdf'.

    Returns a string like ``"https-example-com-some-page.pdf"``.
    """
    slug = url.lower()
    slug = re.sub(r"[^a-z0-9]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    slug = slug[:200]
    return f"{slug}.pdf"
