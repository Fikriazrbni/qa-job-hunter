from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .model import Job, plain, safe_url


class SourceError(Exception):
    pass


def get_json(url: str, timeout: int = 25):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "QAJobHunter/0.1 (personal read-only job monitoring)", "Accept": "application/json"})
            with urlopen(request, timeout=timeout) as response:
                if not response.geturl().startswith("https://"):
                    raise SourceError("Unsafe non-HTTPS redirect")
                raw = response.read(64_000_001)
                if len(raw) > 64_000_000:
                    raise SourceError("Response exceeds 64 MB; source not reconciled")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise SourceError(f"HTTP {exc.code}: {url}") from exc
            # Bounded Retry-After, never rapid-fire retries.
            retry = exc.headers.get("Retry-After", "")
            time.sleep(min(10, max(1, int(retry))) if retry.isdigit() else 2 ** (attempt + 1))
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == 2 or isinstance(exc, ValueError):
                raise SourceError(f"{type(exc).__name__}: {exc}") from exc
            time.sleep(2 ** attempt)
    raise SourceError("Unreachable retry state")


def parse_jobs(source: dict, payload) -> list[Job]:
    provider, board, company = source["provider"], source["board"], source["company"]
    if provider in {"greenhouse", "ashby"}:
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceError("Expected an object containing jobs[]; preserving previous source state")
        records = payload["jobs"]
        if provider == "greenhouse" and payload.get("meta", {}).get("total", len(records)) != len(records):
            raise SourceError("Incomplete Greenhouse listing; preserving previous source state")
    elif provider == "lever" and isinstance(payload, list):
        records = payload
    else:
        raise SourceError("Unexpected source payload")
    jobs, seen = [], set()
    for j in records:
        if not isinstance(j, dict) or not j.get("id"):
            raise SourceError("Malformed job; source not reconciled")
        if provider == "ashby" and j.get("isListed") is False:
            continue
        job_id = str(j["id"])
        if job_id in seen:
            raise SourceError("Duplicate posting IDs in source response")
        seen.add(job_id)
        if provider == "greenhouse":
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            description = plain(j.get("content", ""))
            url = safe_url(j.get("absolute_url", ""))
            job = Job(provider, board, job_id, company, title, location, description, url, url,
                      published_at=j.get("first_published"), date_basis="first_published" if j.get("first_published") else "Not available",
                      updated_at=j.get("updated_at"), deadline=j.get("application_deadline"))
            if j.get("pay_input_ranges"):
                job.salary = json.dumps(j["pay_input_ranges"], ensure_ascii=False)
        elif provider == "lever":
            category = j.get("categories", {})
            locations = category.get("allLocations") or [category.get("location", "")]
            body = [j.get("descriptionBodyPlain") or j.get("descriptionPlain") or plain(j.get("description", ""))]
            body += [str(part.get("text", "")) + "\n" + plain(part.get("content", "")) for part in j.get("lists", [])]
            body += [j.get("additionalPlain") or plain(j.get("additional", ""))]
            job = Job(provider, board, job_id, company, j.get("text", ""), " / ".join(locations), "\n\n".join(body),
                      safe_url(j.get("hostedUrl", "")), safe_url(j.get("applyUrl", "")),
                      workplace=str(j.get("workplaceType", "unknown")).lower(), employment=category.get("commitment", "Unknown"),
                      metadata={"created_at": j.get("createdAt")})
            # createdAt is record creation, not an established public publication date.
            salary = j.get("salaryRange")
            if salary:
                job.salary = json.dumps(salary, ensure_ascii=False)
        else:
            locations = [j.get("location", "")] + [x.get("location", "") for x in j.get("secondaryLocations", [])]
            compensation = j.get("compensation") or {}
            job = Job(provider, board, job_id, company, j.get("title", ""), " / ".join(locations),
                      j.get("descriptionPlain") or plain(j.get("descriptionHtml", "")),
                      safe_url(j.get("jobUrl", "")), safe_url(j.get("applyUrl", "")),
                      workplace=str(j.get("workplaceType", "unknown")).lower(), employment=j.get("employmentType", "Unknown"),
                      published_at=j.get("publishedAt"), date_basis="publishedAt (last published)" if j.get("publishedAt") else "Not available",
                      salary=compensation.get("compensationTierSummary") or compensation.get("scrapeableCompensationSalarySummary") or "Unknown",
                      metadata={"is_remote": j.get("isRemote")})
        if not job.title or not job.url:
            raise SourceError("Posting missing title/valid URL; source not reconciled")
        jobs.append(job)
    return jobs


def fetch_source(source: dict, timeout=25, getter=get_json) -> list[Job]:
    board = source["board"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise SourceError("Invalid board name")
    provider = source["provider"]
    token = quote(board, safe="")
    if provider == "greenhouse":
        jobs = parse_jobs(source, getter(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout))
        from .screen import role_match
        for job in jobs:
            if not role_match(job):
                continue
            try:
                detail = getter(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{quote(job.id)}?questions=true&pay_transparency=true", timeout)
                if not isinstance(detail, dict) or str(detail.get('id')) != job.id:
                    raise SourceError('Unexpected job-detail response')
                job.published_at = detail.get('first_published') or job.published_at
                job.date_basis = 'first_published' if job.published_at else 'Not available'
                job.deadline = detail.get('application_deadline') or job.deadline
                job.metadata['application_questions'] = [q.get('label', '') for q in detail.get('questions', [])
                    if re.search(r'authori[sz]|sponsor|citizen|travel|relocat|onboard', q.get('label', ''), re.I)]
                if detail.get('pay_input_ranges'):
                    job.salary = json.dumps(detail['pay_input_ranges'], ensure_ascii=False)
            except SourceError as exc:
                job.metadata['detail_error'] = str(exc)
        return jobs
    if provider == "ashby":
        return parse_jobs(source, getter(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true", timeout))
    if provider == "lever":
        all_jobs = []
        for offset in range(0, 10_000, 100):
            payload = getter(f"https://api.lever.co/v0/postings/{token}?mode=json&limit=100&skip={offset}", timeout)
            if not isinstance(payload, list):
                raise SourceError("Expected Lever list")
            all_jobs.extend(payload)
            if len(payload) < 100:
                return parse_jobs(source, all_jobs)
        raise SourceError("Lever safety page limit reached; source not reconciled")
    raise SourceError(f"Unsupported provider: {provider}")
