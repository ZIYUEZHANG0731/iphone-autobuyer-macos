from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import hashlib
from app.models.config import Settings,Mode,Product
from app.security.privacy import Redactor
from app.models.flow import FlowError
from app.security.vault import settings_for_disk

def now_iso():
    return datetime.now(timezone.utc).isoformat()

class Repository:
    def __init__(self, path: Path, redactor: Redactor | None = None):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.redactor = redactor or Redactor()
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, alias TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, mode TEXT NOT NULL, state TEXT NOT NULL, message TEXT, updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, time TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL, elapsed REAL NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS submissions (intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL, order_id TEXT, updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS stock_snapshots (product_key TEXT NOT NULL, store_key TEXT NOT NULL, available INTEGER NOT NULL, quote TEXT NOT NULL, observed TEXT NOT NULL, PRIMARY KEY(product_key,store_key));
              CREATE TABLE IF NOT EXISTS stock_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, product TEXT NOT NULL, options TEXT NOT NULL, store_name TEXT NOT NULL, available INTEGER NOT NULL, previous_available INTEGER, quote TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_config(self, config: Settings):
        config.validate(Mode.WEBSITE_DRY_RUN, require_stores=False)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO config VALUES(1,?)", (json.dumps(settings_for_disk(config),ensure_ascii=False),))

    def load_config(self) -> Settings:
        with self.connect() as db:
            row = db.execute("SELECT data FROM config WHERE id=1").fetchone()
        return Settings.from_dict(json.loads(row[0])) if row else Settings()

    def profiles(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM profiles ORDER BY alias")]

    def save_alias(self, profile_id: str, alias: str):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO profiles VALUES(?,?)",(profile_id, self.redactor.clean(alias)))

    def remove_alias(self, profile_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM profiles WHERE id=?",(profile_id,))

    def update_run(self, run_id, mode, state, message, elapsed=0):
        message = self.redactor.clean(message)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",(run_id, mode, state, message, now_iso()))
            db.execute("INSERT INTO events(run_id,time,stage,result,elapsed) VALUES(?,?,?,?,?)",(run_id,now_iso(),state,message,elapsed))

    def latest_runs(self, limit=30):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY updated DESC LIMIT ?",(limit,))]

    def events(self, run_id=None, limit=500):
        with self.connect() as db:
            if run_id:
                rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",(run_id,limit))
            else:
                rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",(limit,))
            return [dict(r) for r in rows]

    def claim_submission(self, intent_id: str, run_id: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("INSERT OR IGNORE INTO submissions VALUES(?,?,?,NULL,?)",(intent_id,run_id,"PENDING",now_iso()))
            return result.rowcount == 1

    def submission(self, intent_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM submissions WHERE intent_id=?",(intent_id,)).fetchone()
            return dict(row) if row else None

    def submissions(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM submissions ORDER BY updated DESC LIMIT ?', (limit,))]

    def observe_stores(self, product: Product, stores: list[dict]):
        scope=json.dumps([product.url,product.options,product.quantity],sort_keys=True,ensure_ascii=False)
        product_key=hashlib.sha256(scope.encode()).hexdigest()
        observed=now_iso();changes=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for store in stores:
                if type(store.get('available')) is not bool or not store.get('name'):
                    continue
                store_key=store.get('store_id') or store['name'];available=int(store['available'])
                quote=self.redactor.clean(store.get('quote',''))
                previous=db.execute('SELECT available,quote FROM stock_snapshots WHERE product_key=? AND store_key=?',(product_key,store_key)).fetchone()
                if not previous or previous['available']!=available or previous['quote']!=quote:
                    change={'time':observed,'product':self.redactor.clean(product.name),'options':json.dumps(self.redactor.object(product.options),ensure_ascii=False),
                        'store_name':self.redactor.clean(store['name']),'available':available,
                        'previous_available':previous['available'] if previous else None,'quote':quote}
                    db.execute('INSERT INTO stock_changes(time,product,options,store_name,available,previous_available,quote) VALUES(:time,:product,:options,:store_name,:available,:previous_available,:quote)',change)
                    changes.append(change)
                db.execute('INSERT OR REPLACE INTO stock_snapshots VALUES(?,?,?,?,?)',(product_key,store_key,available,quote,observed))
        return changes

    def stock_changes(self, limit=200):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM stock_changes ORDER BY id DESC LIMIT ?', (limit,))]

    def record_submission(self, intent_id: str, status: str, order_id: str | None = None):
        if status not in {"PENDING","UNKNOWN","CONFIRMED","REJECTED"}:
            raise FlowError("订单锁状态不正确")
        with self.connect() as db:
            db.execute("UPDATE submissions SET status=?,order_id=?,updated=? WHERE intent_id=?",(status,order_id,now_iso(),intent_id))

    def export_logs(self, path: Path):
        path.write_text(json.dumps(self.redactor.object(self.events(limit=10000)),ensure_ascii=False,indent=2),encoding="utf-8")
        path.chmod(0o600)
