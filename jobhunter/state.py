from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .model import Job, url_identity


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          key TEXT PRIMARY KEY, source TEXT, job TEXT, assessment TEXT,
          fingerprint TEXT, first_seen TEXT, last_seen TEXT, activity TEXT,
          historical INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, started TEXT, completed TEXT, outcome TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, run_id INTEGER, job_key TEXT, kind TEXT, observed TEXT, delivered INTEGER NOT NULL DEFAULT 0);
        """)
        if 'delivered' not in {r[1] for r in self.db.execute('PRAGMA table_info(events)')}:
            self.db.execute('ALTER TABLE events ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE events SET delivered=1 WHERE run_id IN (SELECT id FROM runs WHERE outcome IN ('success','partial','failed') AND completed IS NOT NULL)")
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def seed_history(self, path: Path, observed: str) -> int:
        if not path.is_file() or self.get("history_imported"):
            return 0
        keys = set()
        for url in re.findall(r"https?://[^\s<>|;)]+", path.read_text()):
            identity = url_identity(url.rstrip(".,"))
            if identity:
                keys.add(identity)
        for key in keys:
            self.db.execute("INSERT OR IGNORE INTO jobs(key,source,first_seen,activity,historical) VALUES (?,?,?,?,1)",
                            (key, ":".join(key.split(":")[:2]), observed, "unknown"))
        self.set("history_imported", {"path": str(path), "at": observed, "count": len(keys)})
        self.db.commit()
        return len(keys)

    def start_run(self, timestamp):
        cursor = self.db.execute("INSERT INTO runs(started,outcome) VALUES (?, 'running')", (timestamp,))
        self.set("last_attempt", timestamp)
        self.db.commit()
        return cursor.lastrowid

    def ingest(self, source: str, records: list[tuple[Job, dict]], run_id: int, timestamp: str) -> list[dict]:
        events = []
        present = set()
        with self.db:
            for job, assessment in records:
                present.add(job.key)
                old = self.db.execute("SELECT * FROM jobs WHERE key=?", (job.key,)).fetchone()
                kind = None
                if old is None:
                    kind = "new"
                elif old["job"] is not None:
                    if old["activity"] == "not_listed":
                        kind = "reappeared"
                    elif old["fingerprint"] != job.fingerprint():
                        kind = "updated"
                # An imported manual-history key suppresses its first machine snapshot.
                first_seen = old["first_seen"] if old else timestamp
                self.db.execute("""INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET job=excluded.job,assessment=excluded.assessment,
                    fingerprint=excluded.fingerprint,last_seen=excluded.last_seen,activity=excluded.activity""",
                    (job.key, source, json.dumps(job.dict()), json.dumps(assessment), job.fingerprint(),
                     first_seen, timestamp, "listed", int(bool(old and old["historical"]))))
                if kind:
                    self.db.execute("INSERT INTO events(run_id,job_key,kind,observed) VALUES (?,?,?,?)", (run_id, job.key, kind, timestamp))
                    events.append({"kind": kind, "job": job.dict(), "assessment": assessment, "first_seen": first_seen, "last_seen": timestamp})
            for old in self.db.execute("SELECT * FROM jobs WHERE source=? AND activity='listed'", (source,)).fetchall():
                if old["key"] not in present:
                    self.db.execute("UPDATE jobs SET activity='not_listed' WHERE key=?", (old["key"],))
                    self.db.execute("INSERT INTO events(run_id,job_key,kind,observed) VALUES (?,?,?,?)", (run_id, old["key"], "not_listed", timestamp))
                    events.append({"kind": "not_listed", "job": json.loads(old["job"]), "assessment": json.loads(old["assessment"]),
                                   "first_seen": old["first_seen"], "last_seen": old["last_seen"]})
        return events

    def active(self, successful_sources: set[str]) -> list[dict]:
        rows = []
        for row in self.db.execute("SELECT * FROM jobs WHERE activity='listed' AND job IS NOT NULL"):
            rows.append({"kind": "seen", "job": json.loads(row["job"]), "assessment": json.loads(row["assessment"]),
                         "first_seen": row["first_seen"], "last_seen": row["last_seen"], "checked_this_run": row["source"] in successful_sources})
        return rows

    def pending_events(self, successful_sources: set[str]) -> list[dict]:
        grouped = {}
        for event in self.db.execute('SELECT e.*, j.source, j.job, j.assessment, j.first_seen, j.last_seen FROM events e JOIN jobs j ON e.job_key=j.key WHERE e.delivered=0 ORDER BY e.id'):
            if event['source'] not in successful_sources:
                continue
            key = event['job_key']
            previous = grouped.get(key)
            kind = event['kind']
            if previous and previous['kind'] == 'new' and kind != 'not_listed':
                kind = 'new'
            grouped[key] = {'kind': kind, 'job': json.loads(event['job']), 'assessment': json.loads(event['assessment']),
                            'first_seen': event['first_seen'], 'last_seen': event['last_seen'], 'checked_this_run': True,
                            'event_ids': (previous['event_ids'] if previous else []) + [event['id']]}
        return list(grouped.values())

    def finish(self, run_id, timestamp, outcome, summary, reported_event_ids=()):
        with self.db:
            self.db.execute("UPDATE runs SET completed=?,outcome=?,summary=? WHERE id=?", (timestamp, outcome, json.dumps(summary), run_id))
            if outcome in {"success", "partial"}:
                self.set("last_completed", timestamp)
            if outcome == "success":
                self.set("last_success", timestamp)
            self.db.executemany('UPDATE events SET delivered=1 WHERE id=?', [(i,) for i in reported_event_ids])
