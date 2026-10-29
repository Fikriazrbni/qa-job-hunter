from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .model import parse_date, utcnow
from .service import next_due, perform_run, run_lock
from .state import Store

LABEL = 'com.fikri.qa-job-hunter'


def plist_data(cfg: dict) -> dict:
    logs = Path(cfg['_root']) / 'logs'
    logs.mkdir(exist_ok=True)
    return {'Label': LABEL, 'ProgramArguments': [sys.executable, '-m', 'jobhunter', '--config', cfg['_path'], 'tick'],
            'WorkingDirectory': cfg['_root'], 'RunAtLoad': True, 'StartInterval': 900,
            'StartCalendarInterval': {'Hour': cfg['hour'], 'Minute': cfg['minute']},
            'StandardOutPath': str(logs / 'scheduler.log'), 'StandardErrorPath': str(logs / 'scheduler-error.log'),
            'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
            'ProcessType': 'Background'}


def prepare(cfg: dict) -> Path:
    if sys.platform != 'darwin':
        raise RuntimeError('LaunchAgent tersedia untuk macOS saja')
    folder = Path(cfg['_root']) / 'output' / 'scheduler'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (LABEL + '.plist')
    target.write_bytes(plistlib.dumps(plist_data(cfg)))
    return target


def install(cfg: dict) -> Path:
    prepared = prepare(cfg)
    target = Path.home() / 'Library' / 'LaunchAgents' / prepared.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = plistlib.loads(target.read_bytes())
        if existing.get('Label') != LABEL or existing.get('WorkingDirectory') != cfg['_root']:
            raise RuntimeError('LaunchAgent bernama sama milik checkout lain; tidak ditimpa')
        subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
    target.write_bytes(prepared.read_bytes())
    target.chmod(0o600)
    result = subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Plist tersimpan di {target}, tetapi bootstrap gagal: {result.stderr.strip()}')
    check = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError('Bootstrap selesai tetapi verifikasi launchctl gagal')
    return target


def uninstall(cfg: dict):
    target = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    if not target.exists():
        return 'Scheduler belum terpasang'
    data = plistlib.loads(target.read_bytes())
    if data.get('WorkingDirectory') != cfg['_root'] or data.get('Label') != LABEL:
        raise RuntimeError('LaunchAgent bukan milik proyek ini; tidak dihapus')
    subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
    target.unlink()
    return 'Scheduler dinonaktifkan; data dan laporan tetap tersimpan'


def tick(cfg: dict):
    with run_lock(Path(cfg['data_dir'])):
        now = utcnow()
        store = Store(Path(cfg['data_dir']) / 'jobs.sqlite3')
        try:
            completed = parse_date(store.get('last_completed'))
            due = next_due(cfg, completed, now)
            attempt = parse_date(store.get('last_attempt'))
        finally:
            store.close()
        if now < due:
            return {'state': 'waiting', 'next_due': due.isoformat()}
        if attempt and (now - attempt).total_seconds() < 3600 and (not completed or attempt > completed):
            return {'state': 'retry_cooldown', 'next_due': due.isoformat()}
        return perform_run(cfg)


def status(cfg: dict):
    store = Store(Path(cfg['data_dir']) / 'jobs.sqlite3')
    try:
        completed = store.get('last_completed')
        latest = store.db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        data = {'last_completed': completed, 'last_all_sources_success': store.get('last_success'),
                'next_due': next_due(cfg, parse_date(completed), utcnow()).isoformat(),
                'interval_days': cfg['interval_days'], 'timezone': cfg['timezone'],
                'report': str(Path(cfg['report_dir']) / 'latest.html'),
                'latest_run': dict(latest) if latest else None}
        if sys.platform == 'darwin':
            process = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, text=True)
            data['scheduler_loaded'] = process.returncode == 0
        return data
    finally:
        store.close()
