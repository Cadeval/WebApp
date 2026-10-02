"""Bounded, process-shared application logs; independent of user data."""
import logging
from contextlib import closing
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings

LIMIT = 2000

def log_path():
    return Path(getattr(settings, 'ADMIN_LOG_PATH', Path(settings.BASE_DIR) / 'data/live-logs.sqlite3'))

def connection():
    path = log_path(); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=.25)
    db.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, level TEXT, logger TEXT, message TEXT)')
    return db

class SharedLogHandler(logging.Handler):
    def emit(self, record):
        if not getattr(settings, 'ADMIN_LOG_ENABLED', True):
            return
        try:
            message = self.format(record)[-8000:]
            with closing(connection()) as db, db:
                db.execute('INSERT INTO logs(time,level,logger,message) VALUES (?,?,?,?)',
                    (datetime.fromtimestamp(record.created, timezone.utc).isoformat(), record.levelname, record.name, message))
                db.execute('DELETE FROM logs WHERE id <= (SELECT MAX(id) - ? FROM logs)', (LIMIT,))
        except (OSError, sqlite3.Error):
            # Diagnostic storage must never cause a calculation/request to fail.
            pass

def read_logs(after=None):
    with closing(connection()) as db, db:
        db.row_factory = sqlite3.Row
        if after is None:
            rows = db.execute('SELECT * FROM (SELECT * FROM logs ORDER BY id DESC LIMIT 200) ORDER BY id').fetchall()
        else:
            rows = db.execute('SELECT * FROM logs WHERE id > ? ORDER BY id LIMIT 200', (after,)).fetchall()
    return [dict(row) for row in rows]

def install_handler():
    root = logging.getLogger()
    if not any(isinstance(h, SharedLogHandler) for h in root.handlers):
        root.addHandler(SharedLogHandler(level=logging.INFO))
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        logging.getLogger('cadevil.runtime').info('Application worker ready; admin log streaming enabled.')
