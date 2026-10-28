"""Multi-visitor API. All sessions and account data expire in memory."""
import collections
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from family_dashboard_server import App

ORIGINS = set(filter(None, os.getenv('ALLOWED_ORIGINS', 'http://127.0.0.1:8766').split(',')))
SESSIONS = {}
RATE = collections.OrderedDict()
LOCK = threading.RLock()
MAX_SESSIONS = int(os.getenv('MAX_SESSIONS', '12'))
IDLE_TTL = 20 * 60
ABSOLUTE_TTL = 3 * 60 * 60


def dispose(app):
    app.disconnect()
    app.price_cancel.set()
    app.browser_auth.close()


def sweep():
    while True:
        time.sleep(30)
        cutoff = time.monotonic()
        with LOCK:
            expired = [key for key, (app, created, seen) in SESSIONS.items()
                       if cutoff - seen > IDLE_TTL or cutoff - created > ABSOLUTE_TTL]
            apps = [SESSIONS.pop(key)[0] for key in expired]
        for app in apps:
            dispose(app)


class Handler(BaseHTTPRequestHandler):
    server_version = 'FamilyShelf'
    def log_message(self, *args):
        pass  # Never log request bodies, credentials, or URLs.

    def origin(self):
        return self.headers.get('Origin', '')

    def send(self, status, body=None, ctype='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body or {}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if self.origin() in ORIGINS:
            self.send_header('Access-Control-Allow-Origin', self.origin())
            self.send_header('Vary', 'Origin')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def permitted(self):
        if self.origin() not in ORIGINS:
            self.send(403, {'error':'请求来源无效。'})
            return False
        # The reverse proxy replaces X-Forwarded-For on Render.
        address = self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[-1].strip()
        current = time.monotonic()
        with LOCK:
            previous = RATE.pop(address, [])
            previous = [x for x in previous if current - x < 60]
            previous.append(current)
            RATE[address] = previous
            while len(RATE) > 2000:
                RATE.popitem(last=False)
        if len(previous) > 160:
            self.send(429, {'error':'操作过于频繁，请稍后重试。'})
            return False
        return True

    def app(self):
        token = self.headers.get('X-FamilyShelf-Session', '')
        current = time.monotonic()
        with LOCK:
            record = SESSIONS.get(token)
            if record and current - record[2] <= IDLE_TTL and current - record[1] <= ABSOLUTE_TTL:
                SESSIONS[token] = record[0], record[1], current
                return record[0]
        self.send(401, {'error':'本次会话已过期，请刷新网页后重新登录。'})

    def do_OPTIONS(self):
        if self.origin() not in ORIGINS:
            return self.send(403)
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.origin())
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-FamilyShelf-Session, X-FamilyShelf-CSRF')
        self.send_header('Access-Control-Max-Age', '600')
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            return self.send(200, {'ok':True, 'version':'2026-10-28-achievements'})
        if not self.permitted():
            return
        app = self.app()
        if app is None:
            return
        if path == '/api/state':
            with app.lock:
                state = app.status()
                state['cloud'] = {'qr_ready':bool(app.browser_auth.qr_image()), 'expires_in':ABSOLUTE_TTL}
                return self.send(200, state)
        if path == '/api/auth/qr':
            image = app.browser_auth.qr_image()
            return self.send(200, image, 'image/png') if image else self.send(204)
        self.send(404)

    def do_POST(self):
        if not self.permitted():
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 65536:
                raise ValueError()
            body = json.loads(self.rfile.read(size) or '{}')
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, TypeError):
            return self.send(400, {'error':'请求格式无效。'})
        path = urlsplit(self.path).path
        if path == '/api/session':
            with LOCK:
                if len(SESSIONS) >= MAX_SESSIONS:
                    return self.send(503, {'error':'在线人数已达免费服务上限，请稍后重试。'})
                token = secrets.token_urlsafe(32)
                app = App()
                SESSIONS[token] = app, time.monotonic(), time.monotonic()
            return self.send(201, {'session':token})
        app = self.app()
        if app is None:
            return
        if self.headers.get('X-FamilyShelf-CSRF') != app.csrf:
            return self.send(403, {'error':'请求校验失败，请刷新网页。'})
        if path == '/api/login': ok, msg = app.start(True)
        elif path == '/api/login/token': ok, msg = app.start_token(body.get('token'))
        elif path == '/api/refresh': ok, msg = app.start(False)
        elif path == '/api/disconnect':
            app.disconnect(); ok, msg = True, '已清除本次 Steam 认证。'
        elif path == '/api/cancel':
            app.cancel.set(); app.browser_auth.reset(); ok, msg = True, '正在取消…'
        elif path == '/api/friends/login': ok, msg = app.start_friend_auth()
        elif path == '/api/friends/login/cancel':
            app.cancel_friend_auth(); app.browser_auth.reset(); ok, msg = True, '已取消自动连接。'
        elif path == '/api/friends':
            if app.friend_auth['running']:
                ok, msg = False, '请先完成或取消自动连接。'
            else:
                ok, msg = app.friends.start(body)
        elif path == '/api/friends/cancel':
            app.cancel_friends(); ok, msg = True, '正在取消好友同步…'
        elif path == '/api/friends/disconnect':
            app.cancel_friend_auth(); app.friends.disconnect(); ok, msg = True, '已清除内存中的 API Key。'
        elif path == '/api/prices': ok, msg = app.start_prices(body.get('region','cn'), body.get('scope','family'), body.get('ids'))
        elif path == '/api/prices/cancel':
            app.price_cancel.set(); ok, msg = True, '正在暂停价格查询…'
        else:
            return self.send(404)
        self.send(202 if ok else 409, {'ok':ok, 'message':msg})


if __name__ == '__main__':
    threading.Thread(target=sweep, daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '10000'))), Handler)
    server.serve_forever()
