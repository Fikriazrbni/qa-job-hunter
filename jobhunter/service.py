from __future__ import annotations

import fcntl
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import utcnow
from .report import write_reports
from .screen import role_match, screen
from .sources import fetch_source
from .state import Store


def load_config(path: Path) -> dict:
    path = path.resolve()
    cfg = json.loads(path.read_text())
    cfg['_path'] = str(path)
    cfg['_root'] = str(path.parent)
    for key in ('data_dir', 'report_dir', 'profile', 'history_file'):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    ZoneInfo(cfg['timezone'])
    if not isinstance(cfg['interval_days'], int) or cfg['interval_days'] < 1:
        raise ValueError('interval_days must be a positive integer')
    if not 0 <= cfg['hour'] <= 23 or not 0 <= cfg['minute'] <= 59:
        raise ValueError('Invalid local schedule time')
    if not 1 <= cfg['limit'] <= 100 or not 1 <= cfg['freshness_days'] <= 365:
        raise ValueError('Invalid report limit/freshness_days')
    enabled = [s for s in cfg['sources'] if s.get('enabled', True)]
    identities = [s['provider'] + ':' + s['board'].lower() for s in enabled]
    if not enabled or len(identities) != len(set(identities)):
        raise ValueError('Configure at least one source, without duplicate provider/board pairs')
    for s in enabled:
        if s['provider'] not in {'greenhouse', 'lever', 'ashby'} or not s.get('company'):
            raise ValueError('Invalid source definition')
    cfg['sources'] = enabled
    return cfg


@contextmanager
def run_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Pencarian lain masih berjalan; tidak memulai run ganda') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def next_due(cfg, completed, now):
    local = now.astimezone(ZoneInfo(cfg['timezone']))
    if completed:
        date = completed.astimezone(local.tzinfo).date() + timedelta(days=cfg['interval_days'])
    else:
        date = local.date()
    return local.replace(year=date.year, month=date.month, day=date.day, hour=cfg['hour'], minute=cfg['minute'], second=0, microsecond=0)


def perform_run(cfg: dict, fetcher=fetch_source, now=None) -> dict:
    clock_injected = now is not None
    now = now or utcnow()
    timestamp = now.isoformat()
    store = Store(Path(cfg['data_dir']) / 'jobs.sqlite3')
    try:
        imported = store.seed_history(Path(cfg['history_file']), timestamp)
        profile = json.loads(Path(cfg['profile']).read_text())
        run_id = store.start_run(timestamp)
        sources, events, successful = [], [], set()
        screened = fetched = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetcher, s, cfg.get('timeout_seconds', 25)): s for s in cfg['sources']}
            for future in as_completed(futures):
                source = futures[future]
                key = source['provider'] + ':' + source['board'].lower()
                try:
                    jobs = future.result()
                    existing = {r[0] for r in store.db.execute('SELECT key FROM jobs WHERE source=? AND job IS NOT NULL', (key,))}
                    candidates = []
                    for job in jobs:
                        target = role_match(job)
                        if target or job.key in existing:
                            assessment = screen(job, profile, now, cfg['freshness_days'])
                            if not target:
                                assessment['recommendation'] = 'Skip'
                                assessment['flags'].append('Judul/isi berubah ke luar target QA automation')
                            candidates.append((job, assessment))
                    events += store.ingest(key, candidates, run_id, timestamp)
                    sources.append({'source': key, 'count': len(jobs), 'screened': len(candidates)})
                    successful.add(key)
                    fetched += len(jobs)
                    screened += len(candidates)
                except Exception as exc:
                    # No reconciliation on failure: missing data is never treated as closure.
                    sources.append({'source': key, 'count': 0, 'error': f'{type(exc).__name__}: {exc}'})
        outcome = 'success' if len(successful) == len(cfg['sources']) else 'partial' if successful else 'failed'
        # Undelivered events survive report/disk failures and are retried on a fresh source check.
        events = store.pending_events(successful)
        summary = {'successful_sources': len(successful), 'fetched': fetched, 'screened': screened,
                   'imported_history_keys': imported, 'new': sum(e['kind'] == 'new' for e in events),
                   'updated': sum(e['kind'] in {'updated', 'reappeared'} for e in events)}
        result = {'run_id': run_id, 'checked_at': timestamp, 'checked_local': now.astimezone(ZoneInfo(cfg['timezone'])).strftime('%Y-%m-%d %H:%M %Z'),
                  'outcome': outcome, 'summary': summary, 'sources': sorted(sources, key=lambda s: s['source']),
                  'events': events, 'active': store.active(successful)}
        report = write_reports(Path(cfg['report_dir']), result, cfg['limit'])
        store.finish(run_id, timestamp if clock_injected else utcnow().isoformat(), outcome, summary,
                     [i for row in events for i in row.get('event_ids', [])])
        result['report'] = str(report)
        return result
    finally:
        store.close()
