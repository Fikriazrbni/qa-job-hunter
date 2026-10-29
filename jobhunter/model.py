from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value: str) -> str:
    value = str(value or "")
    # Greenhouse sometimes escapes the entire HTML fragment twice.
    for _ in range(2):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    parser = TextParser()
    parser.feed(value)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def safe_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""


def canonical_url(value: str) -> str:
    if not safe_url(value):
        return ""
    p = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "source", "trk"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))


def url_identity(url: str) -> str | None:
    p = urlsplit(url)
    parts = p.path.strip("/").split("/")
    host = (p.hostname or "").lower()
    if host in {"job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"} and len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
        return f"greenhouse:{parts[0].lower()}:{parts[2]}"
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"} and len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F-]{36}", parts[1]):
        return f"{'ashby' if 'ashby' in host else 'lever'}:{parts[0].lower()}:{parts[1]}"
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


@dataclass
class Job:
    provider: str
    board: str
    id: str
    company: str
    title: str
    location: str
    description: str
    url: str
    apply_url: str
    workplace: str = "unknown"
    employment: str = "Unknown"
    published_at: str | None = None
    date_basis: str = "Not available"
    updated_at: str | None = None
    deadline: str | None = None
    salary: str = "Unknown"
    metadata: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.board.lower()}:{self.id}"

    @property
    def source_key(self) -> str:
        return f"{self.provider}:{self.board.lower()}"

    def dict(self) -> dict:
        return asdict(self)

    def fingerprint(self) -> str:
        # Cosmetic description whitespace/URLs and timestamps are not meaningful updates.
        values = self.dict()
        for field_name in ("updated_at", "published_at", "date_basis", "metadata"):
            values.pop(field_name, None)
        values['application_questions'] = self.metadata.get('application_questions', [])
        values["description"] = re.sub(r"\s+", " ", self.description).strip()
        values["url"] = canonical_url(self.url)
        values["apply_url"] = canonical_url(self.apply_url)
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
