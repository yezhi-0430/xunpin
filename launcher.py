"""Start the original Xunpin workbench from any Windows working directory."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent


def port_state(port, instance_id):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
            return 'free'
        except OSError:
            pass
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
            health=json.loads(response.read(4096))
        return 'same' if isinstance(health,dict) and health.get('service')=='xunpin' and health.get('instance_id')==instance_id else 'busy'
    except (OSError, ValueError):
        return 'busy'


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
        sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='Start original Xunpin workbench')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    missing=[name for name in ('fastapi','uvicorn','httpx','pydantic','dotenv') if importlib.util.find_spec(name) is None]
    if missing:
        print('Missing dependencies: '+', '.join(missing),file=sys.stderr)
        print(f'Run uv sync --locked in this project, or "{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"',file=sys.stderr)
        return 1
    try:
        from backend.settings import Config
        from backend.app import create_app
        config=Config.from_env()
    except (ImportError, ValueError):
        print('Project configuration or dependencies are invalid. Check local settings without sharing credentials.',file=sys.stderr)
        return 1
    print('Xunpin workbench: '+str(ROOT),flush=True)
    if args.check:
        print('Startup check passed',flush=True)
        return 0
    instance_id=hashlib.sha256(str(config.database_path.resolve()).encode()).hexdigest()[:16]
    current=port_state(args.port,instance_id)
    url=f'http://127.0.0.1:{args.port}/'
    if current=='busy':
        print(f'Port {args.port} belongs to another service. Close that service yourself or run start.bat --port {8001 if args.port != 8001 else 8002}',file=sys.stderr)
        return 1
    if current=='same':
        print('Already running: '+url,flush=True)
        if not args.no_browser: webbrowser.open(url)
        return 0
    stop=threading.Event()
    def open_when_ready():
        deadline=time.monotonic()+15
        while not stop.wait(.3) and time.monotonic()<deadline:
            if port_state(args.port,instance_id)=='same':
                webbrowser.open(url)
                return
    if not args.no_browser:
        threading.Thread(target=open_when_ready,daemon=True).start()
    print('Open: '+url+' (Ctrl+C to stop)',flush=True)
    import uvicorn
    try:
        uvicorn.run(create_app(config),host='127.0.0.1',port=args.port)
    finally:
        stop.set()
    return 0


if __name__=='__main__': raise SystemExit(main())
