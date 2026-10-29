"""Run against a restored private state repository; never silently reset history."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def validate_state(folder: Path):
    marker = json.loads((folder / 'state-version.json').read_text())
    if marker != {'version': 1}:
        raise ValueError('Unsupported state version')
    profile = json.loads((folder / 'profile.json').read_text())
    if not isinstance(profile.get('skills'), list) or not profile['skills']:
        raise ValueError('Private candidate profile requires a nonempty skills list')
    database = folder / 'data' / 'jobs.sqlite3'
    # Read-only mode prevents a missing restore from becoming a fresh empty DB.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('State database failed integrity check')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'jobs', 'state', 'runs', 'events'} <= tables:
            raise ValueError('State database is missing required tables')


def cloud_config(folder: Path, base: Path = ROOT / 'config.json'):
    cfg = json.loads(base.read_text())
    cfg.update(data_dir=str(folder / 'data'), report_dir=str(folder / 'reports'),
               profile=str(folder / 'profile.json'), history_file=str(folder / 'history.md'))
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--mode', choices=['tick', 'run', 'status'], default='tick')
    args = parser.parse_args(argv)
    folder = args.state_dir.resolve()
    try:
        validate_state(folder)
        with tempfile.TemporaryDirectory(prefix='jobhunter-config-') as temp:
            config = Path(temp) / 'config.json'
            config.write_text(json.dumps(cloud_config(folder)))
            return subprocess.run([sys.executable, '-m', 'jobhunter', '--config', str(config), args.mode], cwd=ROOT).returncode
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'Cloud state unavailable: {type(exc).__name__}. Restore the private state repository; history was not reset.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
