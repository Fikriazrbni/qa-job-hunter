"""Copy a consistent local snapshot into a NEW private state repository checkout."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobhunter.service import load_config, run_lock
from scripts.cloud_run import validate_state


def bootstrap(config: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists() and any(p.name != '.git' for p in destination.iterdir()):
        raise ValueError('Destination must be empty except for .git; existing history will not be overwritten')
    cfg = load_config(config)
    source = Path(cfg['data_dir']) / 'jobs.sqlite3'
    if not source.is_file() or not Path(cfg['profile']).is_file():
        raise ValueError('Run the local job hunter once with your profile before migrating')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'data').mkdir()
    with run_lock(Path(cfg['data_dir'])):
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old, closing(sqlite3.connect(destination / 'data/jobs.sqlite3')) as new:
            old.backup(new)
        shutil.copy2(cfg['profile'], destination / 'profile.json')
        reports = Path(cfg['report_dir'])
        if reports.is_dir():
            shutil.copytree(reports, destination / 'reports')
    (destination / 'state-version.json').write_text(json.dumps({'version': 1}) + '\n')
    (destination / '.gitignore').write_text('data/*.lock\ndata/*-journal\ndata/*-wal\ndata/*-shm\n')
    (destination / 'README.md').write_text('# Private QA Job Hunter state\n\nKeep this repository PRIVATE. It contains the candidate profile, SQLite history and reports.\nDownload reports/latest.html and open it in a browser. The public code repository contains the runner.\nDo not delete data/jobs.sqlite3: this would reset deduplication and the schedule anchor.\n')
    validate_state(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    bootstrap(args.config, args.destination)
    print('Private state snapshot prepared; schedule and deduplication preserved.')


if __name__ == '__main__':
    main()
