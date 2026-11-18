import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = dict(distance_m=1.0, absence_seconds=30, photo_count=4, countdown=3,
                retention_days=7, enabled_filters=['none', 'glasses', 'hat', 'mono', 'sepia'],
                default_filter='none')


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.photos = self.root / 'photos'
        self.photos.mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'booth.db', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, owner TEXT NOT NULL,
          created TEXT NOT NULL, status TEXT NOT NULL, files TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, created TEXT, message TEXT);
        ''')
        self.db.commit()

    def settings(self):
        with self.lock:
            result = DEFAULTS.copy()
            for row in self.db.execute('SELECT * FROM settings'):
                result[row['key']] = json.loads(row['value'])
            return result

    def configure(self, values):
        with self.lock:
            for key, value in values.items():
                self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
            self.db.commit()

    def log(self, message):
        with self.lock:
            self.db.execute('INSERT INTO events(created,message) VALUES (?,?)', (self.now(), message))
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)')
            self.db.commit()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def create(self, sid, owner):
        with self.lock:
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (sid, owner, self.now(), 'capturing', '[]'))
            self.db.commit()

    def finish(self, sid, status, files):
        with self.lock:
            self.db.execute('UPDATE sessions SET status=?, files=? WHERE id=?', (status, json.dumps(files), sid))
            self.db.commit()

    def sessions(self, owner=None):
        with self.lock:
            query = 'SELECT * FROM sessions'
            rows = self.db.execute(query + (' WHERE owner=?' if owner else '') + ' ORDER BY created DESC', (owner,) if owner else ()).fetchall()
            return [dict(id=r['id'], created=r['created'], status=r['status'], files=json.loads(r['files'])) for r in rows]

    def delete(self, sid):
        with self.lock:
            row = self.db.execute('SELECT files FROM sessions WHERE id=?', (sid,)).fetchone()
            if not row:
                return False
            for name in json.loads(row['files']):
                (self.photos / Path(name).name).unlink(missing_ok=True)
            self.db.execute('DELETE FROM sessions WHERE id=?', (sid,))
            self.db.commit()
            return True

    def recover(self):
        with self.lock:
            self.db.execute("UPDATE sessions SET status='interrupted' WHERE status='capturing'")
            self.db.commit()

    def events(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT created,message FROM events ORDER BY id DESC LIMIT 100')]
