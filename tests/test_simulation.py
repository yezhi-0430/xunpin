import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.settings import Config
from backend.storage import Store


def fixture_db(tmp_path):
    path=tmp_path/'claude-demo.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE products(id INTEGER,product_id TEXT,title TEXT,price REAL,platform TEXT,category TEXT);
        CREATE TABLE monitor_alerts(id INTEGER,product_id TEXT,title TEXT,message TEXT,severity TEXT,alert_type TEXT,is_read INTEGER);
        CREATE TABLE strategy_recommendations(id INTEGER,product_id TEXT,title TEXT,description TEXT,confidence REAL,impact TEXT,is_applied INTEGER);
        CREATE TABLE product_analysis(id INTEGER,product_id TEXT,analysis_type TEXT,result TEXT);''')
        db.execute('INSERT INTO products VALUES(1,?,?,?,?,?)',('DEMO1','构造收纳包',19.95,'淘宝','箱包'))
        db.execute('INSERT INTO monitor_alerts VALUES(1,?,?,?,?,?,0)',('DEMO1','构造告警','没有真实快照','high','price'))
        db.execute('INSERT INTO strategy_recommendations VALUES(1,?,?,?,?,?,0)',('DEMO1','构造建议','核实材质后补充说明',.85,'预计销量提升20%'))
        db.execute('INSERT INTO product_analysis VALUES(1,?,?,?)',('DEMO1','price_trend',json.dumps({'price_history':[{'date':'2026-10-20','price':20.5}]})))
    return path


def test_simulation_import_is_traceable_idempotent_and_isolated(tmp_path):
    from backend.simulation import Simulation
    store=Store(tmp_path/'workbench.db')
    source=fixture_db(tmp_path); before=source.read_bytes()
    simulation=Simulation(store)
    first=simulation.import_database(source)
    second=simulation.import_database(source)
    assert first['manifest']['source_sha256']==second['manifest']['source_sha256']
    assert first['mode']=='simulation' and first['manifest']['simulated'] is True
    assert first['products'][0]['price_cents']==1995
    assert first['products'][0]['payable_cents'] is None
    assert len(first['events'])==len(first['tasks'])==len(first['analyses'])==1
    assert 'confidence' not in first['tasks'][0] and 'impact' not in first['tasks'][0]
    assert source.read_bytes()==before
    assert store.overview('live')['products']==store.overview('replay')['products']==[]


def test_simulation_api_exposes_imported_snapshot_without_creating_events(tmp_path):
    from backend.simulation import Simulation
    path=tmp_path/'workbench.db'; store=Store(path)
    Simulation(store).import_database(fixture_db(tmp_path))
    client=TestClient(create_app(Config(database_path=path),start_scheduler=False))
    response=client.get('/api/simulation')
    assert response.status_code==200 and len(response.json()['products'])==1
    dashboard=client.get('/api/dashboard').json()
    assert dashboard['imported_examples'] is True and len(dashboard['products'])==1
    assert client.get('/api/overview?mode=live').json()['events']==[]
    assert client.post('/api/simulation/import',json={'path':'anything'}).status_code in (404,405)


def test_simulation_empty_state_and_missing_input_do_not_create_source_file(tmp_path):
    from backend.simulation import Simulation
    import pytest
    store=Store(tmp_path/'workbench.db')
    assert Simulation(store).overview()['products']==[]
    missing=tmp_path/'missing.db'
    with pytest.raises(ValueError,match='missing'):
        Simulation(store).import_database(missing)
    assert not missing.exists()
    client=TestClient(create_app(Config(database_path=store.path),start_scheduler=False,example_path=None))
    assert client.get('/api/dashboard').json()['products']==[]


def test_bundled_examples_show_on_fresh_checkout_without_creating_live_records(tmp_path):
    from backend.simulation import Simulation
    source_store=Store(tmp_path/'source.db')
    payload=Simulation(source_store).import_database(fixture_db(tmp_path))
    bundle=tmp_path/'demo-data.json'
    bundle.write_text(json.dumps(payload),encoding='utf-8')
    path=tmp_path/'fresh.db'
    client=TestClient(create_app(Config(database_path=path),start_scheduler=False,example_path=bundle))
    assert client.get('/api/dashboard').json()['products'][0]['product_id']=='DEMO1'
    assert client.get('/api/overview?mode=live').json()['products']==[]
    assert Store(path).state('simulation:dataset') is None


def test_local_import_takes_priority_over_bundled_examples(tmp_path):
    from backend.simulation import Simulation
    store=Store(tmp_path/'local.db')
    imported=Simulation(store).import_database(fixture_db(tmp_path))
    bundle=tmp_path/'demo.json'; bundle.write_text(json.dumps({'products':[]}),encoding='utf-8')
    assert Simulation(store,bundled_path=bundle).overview()==imported
