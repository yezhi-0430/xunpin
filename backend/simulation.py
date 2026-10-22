"""Read-only import of Claude's constructed demo, isolated from monitoring."""
import argparse
import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .domain import now_iso


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        return int((amount * 100).quantize(Decimal('1')))
    except (InvalidOperation, TypeError, ValueError):
        return None


class Simulation:
    def __init__(self, store, bundled_path=None):
        self.store = store
        self.bundled_path = Path(bundled_path) if bundled_path else None

    def overview(self):
        saved = self.store.state('simulation:dataset')
        if saved is not None:
            return saved
        if self.bundled_path and self.bundled_path.is_file():
            return json.loads(self.bundled_path.read_text(encoding='utf-8'))
        return dict(mode='simulation', manifest=None, products=[], events=[], tasks=[], analyses=[])

    def import_database(self, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError('simulation database missing')
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('simulation database exceeds size limit')
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = {}
            for name in ('products', 'monitor_alerts', 'strategy_recommendations', 'product_analysis'):
                total = db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                if total > 5000:
                    raise ValueError('simulation row limit exceeded')
                tables[name] = [dict(row) for row in db.execute('SELECT * FROM ' + name + ' ORDER BY id')]
        products = [dict(product_id=p['product_id'], title=p['title'], platform=p['platform'], category=p.get('category'), price_cents=cents(p['price']), payable_cents=None, sales=p.get('sales'), stock=p.get('stock'), rating=p.get('rating')) for p in tables['products']]
        titles = {p['product_id']: p['title'] for p in products}
        events = [dict(id=f"simulation-alert-{r['id']}", product_id=r['product_id'], product_title=titles.get(r['product_id'], '未匹配样例'), title=r['title'], message=r['message'], kind=r['alert_type'], priority=r['severity'], is_read=bool(r.get('is_read')), simulated=True) for r in tables['monitor_alerts']]
        tasks = [dict(id=f"simulation-task-{r['id']}", product_id=r['product_id'], product_title=titles.get(r['product_id'], '未匹配样例'), title=r['title'], action=r['description'], status='executed' if r.get('is_applied') else 'pending', simulated=True) for r in tables['strategy_recommendations']]
        analyses = [dict(product_id=r['product_id'], kind=r['analysis_type'], result=json.loads(r['result'])) for r in tables['product_analysis']]
        payload = dict(mode='simulation', manifest=dict(source='Claude构造模拟数据', source_file=path.name, source_sha256=fingerprint, imported_at=now_iso(), simulated=True, limitation='仅为导入的模拟展示；告警没有本工作台前后快照证据，不代表真实采集或真实模型分析。'), products=products, events=events, tasks=tasks, analyses=analyses)
        self.store.set_state('simulation:dataset', payload)
        return payload


def main():
    from .settings import Config
    from .storage import Store
    parser=argparse.ArgumentParser(description='Import Claude simulation locally, without platform or model requests')
    parser.add_argument('path', type=Path)
    args=parser.parse_args()
    result=Simulation(Store(Config.from_env().database_path)).import_database(args.path)
    print(json.dumps(dict(products=len(result['products']), alerts=len(result['events']), tasks=len(result['tasks']), analyses=len(result['analyses']), source_sha256=result['manifest']['source_sha256']), ensure_ascii=False))


if __name__=='__main__': main()
