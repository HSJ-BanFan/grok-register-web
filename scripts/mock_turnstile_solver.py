"""Minimal local Turnstile solver for zero-browser protocol path smoke tests.

Implements the same HTTP surface as Asset/grok1 api_solver:
  GET /turnstile?url=...&sitekey=...[&proxy=...] -> {taskId}
  GET /result?id=... -> {solution: {token}} | {}

Does NOT solve real Cloudflare challenges. Use only for wiring / strict-external
path validation. For production use YesCaptcha or a real browser solver.
"""

from __future__ import annotations

import argparse
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockTurnstileServer(ThreadingHTTPServer):
    def __init__(self, *args, delay: float = 0.5, token_prefix: str = 'mock-cf', **kwargs):
        super().__init__(*args, **kwargs)
        self.tasks: dict[str, dict] = {}
        self.delay = max(0.0, float(delay))
        self.token_prefix = token_prefix
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: MockTurnstileServer

    def log_message(self, fmt, *args):
        print(f'[mock-solver] {self.address_string()} - {fmt % args}')

    def _json(self, code: int, payload: dict):
        import json
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        qs = parse_qs(parsed.query)

        if path == '/':
            self._json(200, {'ok': True, 'service': 'mock-turnstile-solver'})
            return

        if path == '/turnstile':
            sitekey = (qs.get('sitekey') or [''])[0]
            url = (qs.get('url') or [''])[0]
            proxy = (qs.get('proxy') or [''])[0]
            if not sitekey:
                self._json(400, {'error': 'missing sitekey'})
                return
            task_id = str(uuid.uuid4())
            ready_at = time.time() + self.server.delay
            with self.server.lock:
                self.server.tasks[task_id] = {
                    'ready_at': ready_at,
                    'sitekey': sitekey,
                    'url': url,
                    'proxy': proxy,
                    'token': f'{self.server.token_prefix}-{task_id[:8]}',
                }
            self._json(200, {'taskId': task_id})
            return

        if path == '/result':
            task_id = (qs.get('id') or [''])[0]
            with self.server.lock:
                task = self.server.tasks.get(task_id)
            if not task:
                self._json(404, {'error': 'unknown task'})
                return
            if time.time() < task['ready_at']:
                self._json(200, {'status': 'pending'})
                return
            self._json(200, {
                'status': 'ready',
                'solution': {'token': task['token']},
            })
            return

        self._json(404, {'error': 'not found'})


def main():
    parser = argparse.ArgumentParser(description='Mock Turnstile solver for wiring tests')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5072)
    parser.add_argument('--delay', type=float, default=0.3)
    args = parser.parse_args()
    server = MockTurnstileServer(
        (args.host, args.port),
        Handler,
        delay=args.delay,
    )
    print(f'[mock-solver] listening on http://{args.host}:{args.port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('[mock-solver] stopped')


if __name__ == '__main__':
    main()
