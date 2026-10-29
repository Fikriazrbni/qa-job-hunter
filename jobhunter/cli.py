from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import scheduler
from .service import load_config, perform_run, run_lock

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description='QA job monitoring tanpa AI, API key, atau auto-apply')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run', help='Cari sekarang; simpan snapshot dan perubahan')
    commands.add_parser('tick', help='Jalankan hanya bila jadwal sudah jatuh tempo')
    commands.add_parser('status', help='Status scheduler, jadwal berikut dan run terakhir')
    schedule = commands.add_parser('schedule', help='Kelola LaunchAgent macOS khusus proyek ini')
    schedule.add_argument('action', choices=['prepare', 'install', 'uninstall'])
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == 'run':
            with run_lock(Path(cfg['data_dir'])):
                result = perform_run(cfg)
            print(json.dumps({k: v for k, v in result.items() if k not in {'events', 'active'}}, indent=2, ensure_ascii=False))
            return {'success': 0, 'partial': 2, 'failed': 1}[result['outcome']]
        if args.command == 'tick':
            result = scheduler.tick(cfg)
            print(json.dumps({k: v for k, v in result.items() if k not in {'events', 'active'}}, indent=2, ensure_ascii=False))
            return {'success': 0, 'partial': 2, 'failed': 1}.get(result.get('outcome'), 0)
        if args.command == 'status':
            print(json.dumps(scheduler.status(cfg), indent=2, ensure_ascii=False))
            return 0
        action = {'prepare': scheduler.prepare, 'install': scheduler.install, 'uninstall': scheduler.uninstall}[args.action]
        print(action(cfg))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
