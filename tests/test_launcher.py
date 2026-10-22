import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import pytest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows batch launcher; other launcher checks run on all platforms')
def test_windows_launcher_checks_original_workbench_from_other_directory(tmp_path):
    command=f'cmd.exe /d /s /c ""{ROOT / "start.bat"}" --check"'
    result=subprocess.run(command,cwd=tmp_path,input='',capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,env={**os.environ,'XUNPIN_NO_PAUSE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Xunpin' in result.stdout and 'Startup check passed' in result.stdout


def test_missing_dependencies_stop_before_importing_application(tmp_path):
    result=subprocess.run([sys.executable,'-S',str(ROOT/'launcher.py'),'--check'],cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
    assert result.returncode!=0
    assert 'Missing dependencies' in result.stderr
    assert 'pip install' in result.stderr


def test_same_workbench_instance_is_distinguished_from_foreign_application():
    assert (ROOT/'launcher.py').exists()
    spec=importlib.util.spec_from_file_location('launcher_test',ROOT/'launcher.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(dict(service='xunpin',instance_id='fixture')).encode())
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    try:
        assert module.port_state(server.server_port,'fixture')=='same'
        assert module.port_state(server.server_port,'other')=='busy'
    finally:
        server.shutdown(); server.server_close(); worker.join()


@pytest.mark.parametrize('payload',[[],None])
def test_non_object_foreign_health_is_a_port_conflict(monkeypatch,payload):
    spec=importlib.util.spec_from_file_location('launcher_foreign_test',ROOT/'launcher.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class OccupiedSocket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def bind(self,*args): raise OSError('fixture occupied')
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return json.dumps(payload).encode()
    class Opener:
        def open(self,*args,**kwargs): return Response()
    monkeypatch.setattr(module.socket,'socket',lambda:OccupiedSocket())
    monkeypatch.setattr(module,'build_opener',lambda *args:Opener())
    assert module.port_state(8123,'fixture')=='busy'
