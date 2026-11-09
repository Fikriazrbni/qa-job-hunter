"""Bounded company-directory discovery. Directory regions never establish job eligibility."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import tarfile
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .report import atomic_write, md_text
from .sources import get_json, fetch_source

REPOSITORY = 'https://github.com/remoteintech/remote-jobs'
REGIONS = {'worldwide', 'asia-pacific'}


def check_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only public HTTPS URLs are supported')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Non-public address refused')


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit=2_000_000, timeout=12):
    check_url(url)
    request = Request(url, headers={'User-Agent': 'QAJobHunter/0.2 (personal read-only source discovery)'})
    with build_opener(PublicRedirect()).open(request, timeout=timeout) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Discovery response exceeds size limit')
        return body, response.geturl()


def board_from_url(url):
    try:
        parsed = urlsplit(html.unescape(url))
    except ValueError:
        return None
    if parsed.scheme != 'https':
        return None
    host = (parsed.hostname or '').lower()
    parts = parsed.path.strip('/').split('/')
    provider = None
    if host in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        provider = 'greenhouse'
        board = parse_qs(parsed.query).get('for', [parts[0]])[0] if parts[0] == 'embed' else parts[0]
    elif host == 'jobs.lever.co':
        provider, board = 'lever', parts[0]
    elif host == 'jobs.ashbyhq.com':
        provider, board = 'ashby', parts[0]
    else:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]+', board) or board.lower() in {'embed', 'jobs', 'application'}:
        return None
    return provider, board


def extract_boards(text):
    text = html.unescape(text).replace('\\/', '/')
    urls = re.findall(r'https://[^\s<>"\x27)]+', text)
    return sorted({board for url in urls if (board := board_from_url(url.rstrip('.,;\\')))})


def parse_directory(archive):
    companies, license_text = [], ''
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile():
                continue
            if member.size > 150_000:
                continue
            if member.name.endswith('/LICENSE'):
                license_text = tar.extractfile(member).read().decode('utf-8')
            if not re.fullmatch(r'[^/]+/src/companies/[^/]+\.md', member.name):
                continue
            text = tar.extractfile(member).read().decode('utf-8')
            sections = text.split('---', 2)
            if len(sections) != 3 or sections[0].strip():
                continue
            fields = {}
            # Deliberately a narrow scalar reader, not a general YAML interpreter.
            for key in ('title', 'slug', 'careers_url', 'region'):
                match = re.search(r'^' + key + r':\s*(.*?)\s*$', sections[1], re.M)
                if match:
                    value = match[1]
                    if value.startswith('"'):
                        try:
                            value = json.loads(value)
                        except ValueError:
                            value = value.strip('"')
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1].replace("''", "'")
                    fields[key] = value
            if all(isinstance(fields.get(k), str) and fields[k] for k in ('title', 'slug', 'region')):
                fields['boards'] = [list(x) for x in extract_boards(text)]
                companies.append(fields)
    if not companies:
        raise ValueError('No company profiles found; directory schema may have changed')
    return sorted(companies, key=lambda c: c['slug']), license_text


def discover_sources(cfg, now=None, reader=download, json_reader=get_json, validator=fetch_source):
    options = cfg.get('discovery', {})
    if not options.get('enabled'):
        return cfg['sources'], {'enabled': False}
    now = now or datetime.now(timezone.utc)
    folder = Path(cfg['data_dir'])
    folder.mkdir(parents=True, exist_ok=True)
    cache_path = folder / 'discovery.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    warnings = []
    refreshed = cache.get('refreshed_at')
    if not refreshed or (now - datetime.fromisoformat(refreshed)).total_seconds() >= 7 * 86400:
        try:
            revision = json_reader('https://api.github.com/repos/remoteintech/remote-jobs/commits/main', 20)['sha']
            if not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError('Invalid directory revision')
            raw, _ = reader(f'https://codeload.github.com/remoteintech/remote-jobs/tar.gz/{revision}', 16_000_000, 30)
            companies, license_text = parse_directory(raw)
            cache.update(companies=companies, revision=revision, refreshed_at=now.isoformat())
            atomic_write(folder / 'remote-in-tech-LICENSE.txt', license_text)
        except Exception as exc:
            warnings.append(f'Directory refresh failed; previous cache retained: {type(exc).__name__}: {exc}')
    companies = cache.get('companies', [])
    eligible = [c for c in companies if c['region'] in REGIONS]
    known = {s['provider'] + ':' + s['board'].lower(): s for s in cfg['sources']}
    disabled = set(cfg.get('_disabled_sources', []))
    for s in cache.get('sources', []):
        key = s['provider'] + ':' + s['board'].lower()
        if key not in disabled:
            known.setdefault(key, s)
    candidates = []
    reviews = cache.setdefault('reviews', {})
    for c in eligible:
        for provider, board in c['boards']:
            candidates.append((c, provider, board))
    pages = [c for c in eligible if not c['boards'] and c.get('careers_url', '').startswith('https://')]
    cursor = cache.get('cursor', 0) % max(1, len(pages))
    batch_size = min(options.get('max_career_pages', 20), len(pages))
    batch = [pages[(cursor + i) % len(pages)] for i in range(batch_size)]

    def inspect(c):
        try:
            body, final_url = reader(c['careers_url'])
            boards = extract_boards(body.decode('utf-8', errors='replace') + ' ' + final_url)
            # Multiple tenants may represent a staffing/aggregate page; do not guess ownership.
            return c, boards if len(boards) == 1 else [], 'ATS found' if len(boards) == 1 else 'No single supported ATS found; manual review'
        except Exception as exc:
            return c, [], f'Career page not checked: {type(exc).__name__}: {exc}'

    with ThreadPoolExecutor(max_workers=4) as pool:
        for c, boards, note in pool.map(inspect, batch):
            reviews[c['slug']] = {'company': c['title'], 'url': c['careers_url'], 'note': note, 'checked_at': now.isoformat()}
            candidates.extend((c, provider, board) for provider, board in boards)
    cache['cursor'] = (cursor + batch_size) % max(1, len(pages))
    attempts, added = 0, 0
    failures = cache.setdefault('validation_failures', {})
    for c, provider, board in candidates:
        key = provider + ':' + board.lower()
        if key in known or key in disabled or attempts >= options.get('max_new_boards', 15):
            continue
        # Failed candidates cool down for a week so stale directory links do not starve the rest.
        if key in failures and (now - datetime.fromisoformat(failures[key]['at'])).total_seconds() < 7 * 86400:
            continue
        source = dict(provider=provider, board=board, company=f"{c['title']} [ATS: {board}]", discovered_from=REPOSITORY,
                      directory_revision=cache.get('revision'), directory_slug=c['slug'])
        attempts += 1
        try:
            validator(source, 12)  # Published jobs endpoint must parse before promoting a board.
            known[key] = source
            failures.pop(key, None)
            added += 1
        except Exception as exc:
            failures[key] = {'at': now.isoformat(), 'error': str(exc)}
            reviews[c['slug']] = {'company': c['title'], 'url': c.get('careers_url', ''), 'note': f'ATS validation failed: {key}', 'checked_at': now.isoformat()}
    cache['sources'] = [s for s in known.values() if s.get('discovered_from')]
    atomic_write(cache_path, json.dumps(cache, indent=2, ensure_ascii=False))
    summary = dict(enabled=True, directory=REPOSITORY, revision=cache.get('revision'),
                   directory_companies=len(companies), region_candidates=len(eligible), career_pages_checked=batch_size,
                   new_boards=added, discovered_boards=len(cache['sources']), active_sources=len(known), warnings=warnings)
    report = ['# Source discovery — Remote in Tech', '',
              'Directory region is a discovery hint, not evidence of job eligibility or current hiring.', '',
              f'Source: {REPOSITORY} · revision: {cache.get("revision", "unavailable")} (ISC; notice retained in data/remote-in-tech-LICENSE.txt)', '',
              f'{len(companies)} companies; {len(eligible)} worldwide/APAC candidates; {batch_size} career pages checked this run; {added} boards added.', '',
              'Career pages are checked in rotating batches. Custom/JS-only boards need manual review; no result is not proof of no jobs.', '',
              '## Warnings', '', *warnings, '', '## Active discovered ATS boards', '']
    report += [f'- {md_text(s["company"])}: {s["provider"]}:{s["board"]}' for s in cache['sources']]
    report += ['', '## Career page checks / manual review', '']
    report += [f'- {md_text(r["company"])}: {md_text(r["note"])} — {md_text(r["url"])}' for r in reviews.values()]
    report += ['', f'Not yet checked in the rotating career-page queue: {sum(c["slug"] not in reviews for c in pages)}.']
    atomic_write(Path(cfg['report_dir']) / 'discovery.md', '\n'.join(report))
    return list(known.values()), summary
