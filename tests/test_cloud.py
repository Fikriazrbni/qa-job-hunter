"""Synthetic cloud migration checks, without network or personal data."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jobhunter.state import Store
from scripts.bootstrap_state import bootstrap
from scripts.cloud_run import cloud_config, validate_state

ROOT = Path(__file__).resolve().parent.parent


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = self.root / 'local'
        self.local.mkdir()
        (self.local / 'config.json').write_text((ROOT / 'config.json').read_text())
        (self.local / 'profile.json').write_text((ROOT / 'profile.example.json').read_text())
        store = Store(self.local / 'data/jobs.sqlite3')
        store.set('last_completed', '2099-01-01T02:00:00+00:00')
        store.set('history_imported', {'count': 17})
        store.db.commit()
        store.close()
        (self.local / 'output/automatic').mkdir(parents=True)
        (self.local / 'output/automatic/latest.html').write_text('synthetic private report')
        self.dest = self.root / 'state'

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_preserves_anchor_dedup_and_reports(self):
        bootstrap(self.local / 'config.json', self.dest)
        validate_state(self.dest)
        store = Store(self.dest / 'data/jobs.sqlite3')
        self.assertEqual(store.get('history_imported'), {'count': 17})
        self.assertEqual(store.get('last_completed'), '2099-01-01T02:00:00+00:00')
        store.close()
        self.assertEqual((self.dest / 'reports/latest.html').read_text(), 'synthetic private report')
        self.assertEqual(cloud_config(self.dest)['profile'], str(self.dest / 'profile.json'))
        result = subprocess.run([sys.executable, 'scripts/cloud_run.py', '--state-dir', str(self.dest)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'waiting')

    def test_bootstrap_refuses_overwrite(self):
        self.dest.mkdir()
        (self.dest / 'important.txt').write_text('keep')
        with self.assertRaises(ValueError):
            bootstrap(self.local / 'config.json', self.dest)
        self.assertEqual((self.dest / 'important.txt').read_text(), 'keep')

    def test_missing_database_never_creates_fresh_history(self):
        bootstrap(self.local / 'config.json', self.dest)
        (self.dest / 'data/jobs.sqlite3').unlink()
        with self.assertRaises(sqlite3.Error):
            validate_state(self.dest)
        self.assertFalse((self.dest / 'data/jobs.sqlite3').exists())

    def test_incompatible_or_corrupt_state_is_rejected(self):
        bootstrap(self.local / 'config.json', self.dest)
        (self.dest / 'state-version.json').write_text('{"version": 999}')
        with self.assertRaises(ValueError):
            validate_state(self.dest)
        (self.dest / 'state-version.json').write_text('{"version": 1}')
        (self.dest / 'data/jobs.sqlite3').write_bytes(b'not sqlite')
        with self.assertRaises(sqlite3.Error):
            validate_state(self.dest)
