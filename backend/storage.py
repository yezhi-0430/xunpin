import json
import sqlite3
import threading
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from .domain import Snapshot, changes, digest, now_iso, task_draft


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, mode TEXT, source TEXT, product TEXT, hash TEXT, data TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS latest(mode TEXT, source TEXT, product TEXT, snapshot_id INTEGER, data TEXT, checked TEXT, PRIMARY KEY(mode,source,product));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, mode TEXT, data TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, mode TEXT, data TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, mode TEXT, source TEXT, status TEXT, message TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS seen_reviews(mode TEXT, source TEXT, product TEXT, review_id TEXT, PRIMARY KEY(mode,source,product,review_id));
            """)
            for row in db.execute("SELECT id,mode,data FROM tasks").fetchall():
                task = json.loads(row["data"])
                if "observed_product_id" in task:
                    continue
                event_row = db.execute(
                    "SELECT data FROM events WHERE id=? AND mode=?",
                    (task["event_id"], row["mode"]),
                ).fetchone()
                if not event_row:
                    continue
                event = json.loads(event_row[0])
                item_row = db.execute(
                    "SELECT data FROM snapshots WHERE id=?",
                    (event["after_snapshot_id"],),
                ).fetchone()
                if not item_row:
                    continue
                snapshot = Snapshot(**json.loads(item_row[0]))
                enriched = task_draft(event)
                task.update(
                    action=enriched["action"],
                    brief=enriched["brief"],
                    **self.task_targets(db, snapshot, event["kind"], row["mode"]),
                )
                db.execute(
                    "UPDATE tasks SET data=? WHERE id=?", (self.dumps(task), row["id"])
                )

    @staticmethod
    def task_targets(db, snapshot, kind, mode):
        candidates = [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM latest WHERE mode=?", (mode,))
        ]
        target_ids = (
            [snapshot.product_id]
            if snapshot.role == "own"
            else sorted(
                {
                    row["product_id"]
                    for row in candidates
                    if row["role"] == "own"
                    and snapshot.spec
                    and row.get("spec") == snapshot.spec
                    and (
                        kind != "price" or row.get("conditions") == snapshot.conditions
                    )
                }
            )
        )
        return dict(
            observed_product_id=snapshot.product_id,
            target_product_ids=target_ids,
            target_note="候选本店商品，执行前由运营确认"
            if target_ids
            else "尚无可匹配本店商品，由运营指定目标",
        )

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def dumps(data):
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    def ingest(self, snapshot: Snapshot, mode):
        stamp, data = now_iso(), snapshot.model_dump()
        fingerprint = digest(snapshot.content())
        with self.db() as db:
            previous = db.execute(
                "SELECT s.*, l.data AS latest_data FROM latest l JOIN snapshots s ON s.id=l.snapshot_id WHERE l.mode=? AND l.source=? AND l.product=?",
                (mode, snapshot.source_id, snapshot.product_id),
            ).fetchone()
            if previous and datetime.fromisoformat(
                snapshot.observed_at
            ) < datetime.fromisoformat(
                json.loads(previous["latest_data"])["observed_at"]
            ):
                return dict(
                    out_of_order=True, duplicate=False, baseline=False, events_created=0
                )
            if previous and previous["hash"] == fingerprint:
                db.execute(
                    "UPDATE latest SET data=?, checked=? WHERE mode=? AND source=? AND product=?",
                    (
                        self.dumps(data),
                        stamp,
                        mode,
                        snapshot.source_id,
                        snapshot.product_id,
                    ),
                )
                return dict(duplicate=True, baseline=False, events_created=0)
            cursor = db.execute(
                "INSERT INTO snapshots(mode,source,product,hash,data,created) VALUES(?,?,?,?,?,?)",
                (
                    mode,
                    snapshot.source_id,
                    snapshot.product_id,
                    fingerprint,
                    self.dumps(data),
                    stamp,
                ),
            )
            snapshot_id = cursor.lastrowid
            db.execute(
                "INSERT OR REPLACE INTO latest VALUES(?,?,?,?,?,?)",
                (
                    mode,
                    snapshot.source_id,
                    snapshot.product_id,
                    snapshot_id,
                    self.dumps(data),
                    stamp,
                ),
            )
            detected = (
                changes(Snapshot(**json.loads(previous["data"])), snapshot)
                if previous
                else []
            )
            known = {
                r[0]
                for r in db.execute(
                    "SELECT review_id FROM seen_reviews WHERE mode=? AND source=? AND product=?",
                    (mode, snapshot.source_id, snapshot.product_id),
                )
            }
            # Existing snapshots also seed the persistent index for databases created before this table existed.
            if previous:
                known.update(
                    r["review_id"]
                    for r in (json.loads(previous["data"]).get("reviews") or [])
                )
            filtered = []
            for event in detected:
                if event["kind"] == "reviews":
                    samples = [
                        r
                        for r in event["facts"]["samples"]
                        if r["review_id"] not in known
                    ]
                    if not samples:
                        continue
                    event["facts"].update(samples=samples, sample_count=len(samples))
                filtered.append(event)
            detected = filtered
            for review_id in known | {r.review_id for r in (snapshot.reviews or [])}:
                db.execute(
                    "INSERT OR IGNORE INTO seen_reviews VALUES(?,?,?,?)",
                    (mode, snapshot.source_id, snapshot.product_id, review_id),
                )
            for event in detected:
                event.update(
                    id=digest(
                        [
                            mode,
                            snapshot.source_id,
                            snapshot.product_id,
                            previous["id"],
                            snapshot_id,
                            event["kind"],
                        ]
                    )[:20],
                    product_id=snapshot.product_id,
                    product_title=snapshot.title,
                    role=snapshot.role,
                    source_id=snapshot.source_id,
                    mode=mode,
                    before_snapshot_id=previous["id"],
                    after_snapshot_id=snapshot_id,
                    observed_at=snapshot.observed_at,
                    created_at=stamp,
                )
                db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                    (event["id"], mode, self.dumps(event), stamp),
                )
                task = task_draft(event)
                task.update(
                    id="task-" + event["id"],
                    event_id=event["id"],
                    product_id=snapshot.product_id,
                    **self.task_targets(db, snapshot, event["kind"], mode),
                    priority=event["priority"],
                    created_at=stamp,
                    history=[],
                )
                db.execute(
                    "INSERT OR IGNORE INTO tasks VALUES(?,?,?,?)",
                    (task["id"], mode, self.dumps(task), stamp),
                )
            return dict(
                duplicate=False, baseline=not previous, events_created=len(detected)
            )

    def overview(self, mode):
        with self.db() as db:
            products = []
            for row in db.execute(
                "SELECT * FROM latest WHERE mode=? ORDER BY product", (mode,)
            ):
                products.append(
                    {
                        **json.loads(row["data"]),
                        "snapshot_id": row["snapshot_id"],
                        "checked_at": row["checked"],
                    }
                )
            events = [
                json.loads(r["data"])
                for r in db.execute(
                    "SELECT data FROM events WHERE mode=? ORDER BY created DESC, rowid DESC LIMIT 150",
                    (mode,),
                )
            ]
            tasks = [
                json.loads(r["data"])
                for r in db.execute(
                    "SELECT data FROM tasks WHERE mode=? ORDER BY created DESC, rowid DESC LIMIT 150",
                    (mode,),
                )
            ]
            runs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM runs WHERE mode=? ORDER BY id DESC LIMIT 80", (mode,)
                )
            ]
        return dict(mode=mode, products=products, events=events, tasks=tasks, runs=runs)

    def evidence(self, event_id, mode):
        with self.db() as db:
            row = db.execute(
                "SELECT data FROM events WHERE id=? AND mode=?", (event_id, mode)
            ).fetchone()
            if not row:
                raise KeyError("事件不存在")
            event = json.loads(row["data"])
            versions = [
                json.loads(
                    db.execute(
                        "SELECT data FROM snapshots WHERE id=? AND mode=?",
                        (event[key], mode),
                    ).fetchone()[0]
                )
                for key in ["before_snapshot_id", "after_snapshot_id"]
            ]
        return dict(event=event, before=versions[0], after=versions[1])

    def feedback(self, task_id, status, note, mode, execution=None):
        with self.db() as db:
            row = db.execute(
                "SELECT data FROM tasks WHERE id=? AND mode=?", (task_id, mode)
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            task = json.loads(row["data"])
            task["status"], task["note"] = status, note
            if execution is not None:
                task["execution"] = execution
            entry = dict(status=status, note=note, at=now_iso())
            if "execution" in task:
                entry["execution"] = task["execution"]
            task["history"].append(entry)
            db.execute(
                "UPDATE tasks SET data=? WHERE id=? AND mode=?",
                (self.dumps(task), task_id, mode),
            )
        return task

    def save_analysis(self, task_id, mode, result):
        with self.db() as db:
            row = db.execute(
                "SELECT data FROM tasks WHERE id=? AND mode=?", (task_id, mode)
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            task = json.loads(row["data"])
            task["analysis"] = result
            db.execute(
                "UPDATE tasks SET data=? WHERE id=? AND mode=?",
                (self.dumps(task), task_id, mode),
            )

    def state(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT data FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, data):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO state VALUES(?,?)", (key, self.dumps(data))
            )

    def log(self, mode, source, status, message):
        with self.db() as db:
            db.execute(
                "INSERT INTO runs(mode,source,status,message,created) VALUES(?,?,?,?,?)",
                (mode, source, status, message, now_iso()),
            )

    def reset_replay(self):
        with self.db() as db:
            for table in [
                "latest",
                "snapshots",
                "events",
                "tasks",
                "runs",
                "seen_reviews",
            ]:
                db.execute(f"DELETE FROM {table} WHERE mode='replay'")
            db.execute("DELETE FROM state WHERE key LIKE 'replay:%'")
