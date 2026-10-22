"""Historical text records; never current products or inferred change events."""

import argparse
import hashlib
import io
import json
import random
import zipfile
from pathlib import Path

from .domain import now_iso

SOURCE_URL = "https://tianchi.aliyun.com/dataset/9717"
LICENSE_NOTE = "原页面正文：CC BY-NC 4.0；顶部标记不同，保留待核对。当前限本地非商用演示，未再分发原始数据。"
CREDIT = "阿里云天池 · 商品描述文案数据集；Chen et al., Towards Knowledge-Based Personalized Product Description Generation in E-commerce, 2019."


class History:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS history_datasets(id TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS history_samples(id TEXT PRIMARY KEY, dataset_id TEXT, line_number INTEGER, title TEXT, description TEXT, analysis TEXT);
                CREATE INDEX IF NOT EXISTS history_dataset_lines ON history_samples(dataset_id,line_number);
            """)

    def import_zip(self, path, limit=200, seed=9717, ai_allowed=False):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        path = Path(path)
        if path.stat().st_size > 350 * 1024 * 1024:
            raise ValueError("archive size limit exceeded")
        fingerprint = hashlib.sha256()
        with path.open("rb") as raw:
            for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                fingerprint.update(chunk)
        archive_sha = fingerprint.hexdigest()
        pool, total, valid, skipped = [], 0, 0, 0
        rng = random.Random(seed)
        with zipfile.ZipFile(path) as archive:
            member = "item_desc_dataset.txt"
            if member not in archive.namelist():
                raise ValueError("expected exact dataset member")
            info = archive.getinfo(member)
            if info.file_size > 1024 * 1024 * 1024:
                raise ValueError("member size limit exceeded")
            with archive.open(info) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="strict")
                while True:
                    line = text.readline(20001)
                    if not line:
                        break
                    total += 1
                    if len(line) > 20000:
                        while line and not line.endswith("\n"):
                            line = text.readline(20001)
                        skipped += 1
                        continue
                    fields = line.rstrip("\r\n").split("\t")
                    if len(fields) != 2 or not all(s.strip() for s in fields) or len(fields[0]) > 1000 or len(fields[1]) > 16000 or "\0" in line:
                        skipped += 1
                        continue
                    valid += 1
                    record = dict(line_number=total, title=fields[0], description=fields[1])
                    if len(pool) < limit:
                        pool.append(record)
                    else:
                        index = rng.randrange(valid)
                        if index < limit:
                            pool[index] = record
        if not pool:
            raise ValueError("no valid historical text rows")
        # Exact duplicates within the selected records do not inflate the sample count.
        unique = {}
        for row in sorted(pool, key=lambda x: x["line_number"]):
            unique.setdefault((row["title"], row["description"]), row)
        pool = list(unique.values())
        dataset_id = f"tianchi9717-{archive_sha[:16]}-s{seed}-n{limit}"
        manifest = dict(
            id=dataset_id, source_url=SOURCE_URL, credit=CREDIT, license_note=LICENSE_NOTE,
            archive_sha256=archive_sha, member=member, total_rows=total, valid_rows=valid,
            skipped_rows=skipped, sample_count=len(pool), selected_duplicates=min(limit, valid) - len(unique),
            sampling="fixed-seed reservoir over valid rows, then exact-pair deduplication", seed=seed,
            source_file_date="2019-05-13", source_observed_at=None, imported_at=now_iso(),
            ai_allowed=bool(ai_allowed), scope="historical_text_only",
        )
        with self.store.db() as db:
            db.execute("INSERT OR REPLACE INTO history_datasets VALUES(?,?)", (dataset_id, self.store.dumps(manifest)))
            for row in pool:
                sample_id = f"{dataset_id}-row{row['line_number']}"
                db.execute("""INSERT INTO history_samples VALUES(?,?,?,?,?,NULL)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description""",
                    (sample_id, dataset_id, row["line_number"], row["title"], row["description"]))
        return manifest

    def list_samples(self, offset=0, limit=20):
        with self.store.db() as db:
            row = db.execute("SELECT data FROM history_datasets ORDER BY json_extract(data,'$.imported_at') DESC LIMIT 1").fetchone()
            if not row:
                return dict(dataset=None, total=0, samples=[])
            manifest = json.loads(row[0])
            rows = db.execute("SELECT id,line_number,title,description,analysis FROM history_samples WHERE dataset_id=? ORDER BY line_number LIMIT ? OFFSET ?", (manifest["id"], limit, offset)).fetchall()
            samples = []
            for r in rows:
                analysis = json.loads(r["analysis"]) if r["analysis"] else {}
                samples.append(dict(id=r["id"], line_number=r["line_number"], title=r["title"], description_preview=r["description"][:160], analysis_status=analysis.get("status"), last_attempt_status=analysis.get("last_attempt", {}).get("status")))
            return dict(dataset=manifest, total=manifest["sample_count"], samples=samples)

    def get(self, sample_id):
        with self.store.db() as db:
            row = db.execute("SELECT s.*,d.data FROM history_samples s JOIN history_datasets d ON s.dataset_id=d.id WHERE s.id=?", (sample_id,)).fetchone()
            if not row:
                raise KeyError(sample_id)
            manifest = json.loads(row["data"])
            return dict(id=row["id"], title=row["title"], description=row["description"], line_number=row["line_number"], archive_sha256=manifest["archive_sha256"], dataset=manifest, analysis=json.loads(row["analysis"]) if row["analysis"] else None)

    def save_analysis(self, sample_id, analysis):
        with self.store.db() as db:
            row = db.execute("SELECT analysis FROM history_samples WHERE id=?", (sample_id,)).fetchone()
            if not row:
                raise KeyError(sample_id)
            previous = json.loads(row["analysis"]) if row["analysis"] else None
            if previous and previous.get("status") == "ok" and analysis.get("status") != "ok":
                analysis = {**previous, "last_attempt": {key: analysis.get(key) for key in ("status", "engine", "limitations", "analyzed_at")}}
            db.execute("UPDATE history_samples SET analysis=? WHERE id=?", (self.store.dumps(analysis), sample_id))
            return analysis


def main():
    from .settings import Config
    from .storage import Store
    parser = argparse.ArgumentParser(description="Import local Tianchi historical text; no product or model requests.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=9717)
    parser.add_argument("--allow-ai", action="store_true", help="Explicit local permission for this dataset's titles/descriptions to use configured model")
    args = parser.parse_args()
    result = History(Store(Config.from_env().database_path)).import_zip(args.path, args.limit, args.seed, args.allow_ai)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
