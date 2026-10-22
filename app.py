"""AURA Etiquetas: servidor privado de un solo usuario, sin base de datos.

En Render: AURA_MODE=render y credenciales en variables de entorno.
En local: AURA_MODE=local explícito, accesible solo por 127.0.0.1.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest, new as hmac_new
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import BoundedSemaphore, Lock, Timer
from time import time
from urllib.parse import parse_qs, urlsplit
import base64
import html
import json
import mimetypes
import os
import secrets
import sys
import traceback
import webbrowser

from engine import MAX_UPLOAD_MB, build_export, preview_info, read_table, page_count, normalize_products, sheet_config, artwork_format, blank_labels_count

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
MAX_JSON_BYTES = 12 * 1024 * 1024
SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
MAX_RENDER_EXPORT_PAGES = 12
SESSIONS = {}  # sha256(token) -> {expires, csrf}; reiniciar servidor cierra sesiones
ATTEMPTS = defaultdict(deque)
SECURITY_LOCK = Lock()
EXPORT_LOCK = BoundedSemaphore(value=1)


def load_config():
    mode = os.environ.get('AURA_MODE', 'local').strip().lower()
    if mode not in ('local', 'render'):
        raise RuntimeError('AURA_MODE debe ser local o render.')
    if mode == 'local' and os.getenv('RENDER', '').lower() == 'true':
        raise RuntimeError('En Render debes usar AURA_MODE=render. Se bloqueó el inicio inseguro.')
    password_hash = os.environ.get('AURA_PASSWORD_HASH', '')
    session_secret = os.environ.get('AURA_SESSION_SECRET', '')
    if mode == 'render':
        if not password_hash.startswith('pbkdf2_sha256$'):
            raise RuntimeError('Falta AURA_PASSWORD_HASH: genera tus claves con crear_credenciales.py.')
        if len(session_secret) < 40:
            raise RuntimeError('Falta AURA_SESSION_SECRET de al menos 40 caracteres.')
        if not verify_hash_format(password_hash):
            raise RuntimeError('AURA_PASSWORD_HASH es inválido.')
    return {'mode': mode, 'protected': mode == 'render', 'hash': password_hash}


def verify_hash_format(stored: str) -> bool:
    try:
        label, rounds, salt, digest = stored.split('$')
        return (label == 'pbkdf2_sha256' and int(rounds) >= 200_000
                and len(base64.urlsafe_b64decode(salt + '===')) >= 16
                and len(base64.urlsafe_b64decode(digest + '===')) == 32)
    except (ValueError, IndexError, TypeError):
        return False


def verify_password(password: str, stored: str) -> bool:
    try:
        label, rounds, salt, digest = stored.split('$')
        if label != 'pbkdf2_sha256' or len(password) > 256 or not verify_hash_format(stored):
            return False
        raw_salt = base64.urlsafe_b64decode(salt + '===')
        expected = base64.urlsafe_b64decode(digest + '===')
        candidate = pbkdf2_hmac('sha256', password.encode('utf-8'), raw_salt, int(rounds))
        return compare_digest(candidate, expected)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return False


def cookie_name(config):
    return '__Host-aura_session' if config['protected'] else 'aura_session_local'


def login_cookie_name(config):
    return '__Host-aura_login_csrf' if config['protected'] else 'aura_login_csrf_local'


def cookie_header(name, value, config, max_age=SESSION_SECONDS):
    # __Host- exige Secure, Path=/, sin Domain; el HTTPS lo termina Render.
    flags = '; Path=/; HttpOnly; SameSite=Strict'
    if config['protected']:
        flags += '; Secure'
    return f'{name}={value}; Max-Age={max_age}{flags}'


def register_login_attempt(client_ip):
    now = time()
    with SECURITY_LOCK:
        record = ATTEMPTS[client_ip]
        while record and record[0] < now - LOGIN_WINDOW:
            record.popleft()
        if len(record) >= LOGIN_MAX_ATTEMPTS:
            return False, int(LOGIN_WINDOW - (now - record[0])) + 1
        record.append(now)  # contar también los éxitos; dificulta fuerza bruta
        return True, 0


def session_key(raw: str) -> str:
    key = os.environ.get('AURA_SESSION_SECRET', 'local-development-only').encode('utf-8')
    return hmac_new(key, raw.encode('utf-8'), sha256).hexdigest()


def create_session():
    raw = secrets.token_urlsafe(32)
    with SECURITY_LOCK:
        now = time()
        # Depuración de sesiones caducadas y máximo 30 dispositivos/sesiones abiertos.
        expired = [key for key, item in SESSIONS.items() if item['expires'] < now]
        for key in expired:
            SESSIONS.pop(key, None)
        if len(SESSIONS) >= 30:
            oldest = min(SESSIONS, key=lambda k: SESSIONS[k]['expires'])
            SESSIONS.pop(oldest, None)
        SESSIONS[session_key(raw)] = {
            'expires': now + SESSION_SECONDS,
            'csrf': secrets.token_urlsafe(32),
        }
    return raw


def session_from_token(raw):
    if not raw:
        return None
    key = session_key(raw)
    with SECURITY_LOCK:
        item = SESSIONS.get(key)
        if item and item['expires'] > time():
            return item.copy()
        SESSIONS.pop(key, None)
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = 'AURA/1.5'
    sys_version = ''

    @property
    def config(self):
        return self.server.aura_config

    def log_message(self, fmt, *args):
        # Nunca registrar cuerpos, cookies, nombres de Excel ni parámetros de consulta.
        print(f'[{self.log_date_time_string()}] {self.command} {urlsplit(self.path).path}', flush=True)

    def _cookies(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            return {key: v.value for key, v in cookies.items()}
        except Exception:
            return {}

    def _session(self):
        if not self.config['protected']:
            return {'csrf': 'local'}
        return session_from_token(self._cookies().get(cookie_name(self.config)))

    def _reply(self, body: bytes, mime: str, code=200, filename=None, cookies=(), retry=None):
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'private, no-store, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        ))
        if self.config['protected']:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for item in cookies:
            self.send_header('Set-Cookie', item)
        if retry is not None:
            self.send_header('Retry-After', str(retry))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, status=200, **kwargs):
        return self._reply(json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                           'application/json; charset=utf-8', status, **kwargs)

    def _redirect(self, path, cookies=()):
        self.send_response(303)
        self.send_header('Location', path)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        for item in cookies:
            self.send_header('Set-Cookie', item)
        self.end_headers()

    def _login_page(self, error='', status=200):
        if not self.config['protected']:
            return self._redirect('/')
        csrf = secrets.token_urlsafe(24)
        page = (STATIC / 'login.html').read_text(encoding='utf-8')
        page = (page.replace('%%LOGIN_CSRF%%', csrf)
                .replace('%%LOGIN_ERROR%%', html.escape(error))
                .replace('%%LOGIN_HIDDEN%%', '' if error else 'hidden'))
        cookies = [cookie_header(login_cookie_name(self.config), csrf, self.config, 900)]
        self._reply(page.encode('utf-8'), 'text/html; charset=utf-8', status, cookies=cookies)

    def _fail_unauthenticated(self, path):
        if path.startswith('/api/'):
            return self._json({'error': 'Sesión expirada. Recarga e inicia sesión.'}, 401)
        if path in ('/', '/index.html'):
            return self._redirect('/login')
        return self._reply(b'Acceso privado.', 'text/plain; charset=utf-8', 403)

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        if origin is None:
            return True
        try:
            from urllib.parse import urlsplit as split
            parsed = split(origin)
            return (parsed.netloc == self.headers.get('Host', '')
                    and parsed.scheme == ('https' if self.config['protected'] else 'http'))
        except ValueError:
            return False

    def _body(self, limit):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('Transfer-Encoding no permitido.')
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError as exc:
            raise ValueError('Tamaño de solicitud inválido.') from exc
        if not 0 < size <= limit:
            raise ValueError('Tamaño de solicitud inválido o demasiado grande.')
        return self.rfile.read(size)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            return self._json({'ok': True})
        if path == '/login':
            if self._session():
                return self._redirect('/')
            return self._login_page()
        session = self._session()
        if not session:
            return self._fail_unauthenticated(path)
        if path == '/api/session':
            return self._json({'ok': True})
        routes = {'/': 'index.html', '/index.html': 'index.html',
                  '/app.js': 'app.js', '/style.css': 'style.css'}
        if path not in routes:
            return self._json({'error': 'Ruta no encontrada.'}, 404)
        content = (STATIC / routes[path]).read_bytes()
        if path in ('/', '/index.html'):
            content = content.replace(b'%%AURA_CSRF%%', session['csrf'].encode('ascii'))
        mime = mimetypes.guess_type(routes[path])[0] or 'application/octet-stream'
        if mime.startswith('text/') or mime == 'application/javascript':
            mime += '; charset=utf-8'
        return self._reply(content, mime)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/login':
            if not self.config['protected']:
                return self._redirect('/')
            # No intentamos verificar contraseñas una vez agotado el límite.
            client_ip = self.client_address[0]
            allowed, retry = register_login_attempt(client_ip)
            if not allowed:
                return self._reply(b'Demasiados intentos. Espera 15 minutos.',
                                   'text/plain; charset=utf-8', 429, retry=retry)
            if not self._origin_ok():
                return self._login_page('No se pudo validar el formulario.', 403)
            try:
                body = self._body(2048).decode('utf-8')
                fields = parse_qs(body, keep_blank_values=True)
                password = fields.get('password', [''])[0]
                csrf_form = fields.get('csrf', [''])[0]
                csrf_cookie = self._cookies().get(login_cookie_name(self.config), '')
                valid_form = bool(csrf_form and csrf_cookie and compare_digest(csrf_form, csrf_cookie))
                if valid_form and verify_password(password, self.config['hash']):
                    token = create_session()
                    return self._redirect('/', cookies=[
                        cookie_header(login_cookie_name(self.config), '', self.config, 0),
                        cookie_header(cookie_name(self.config), token, self.config),
                    ])
            except (ValueError, UnicodeError):
                pass
            return self._login_page('Contraseña incorrecta o formulario caducado.', 401)

        session = self._session()
        if not session:
            return self._fail_unauthenticated(path)
        if not self._origin_ok():
            return self._json({'error': 'Origen no permitido.'}, 403)
        csrf = self.headers.get('X-CSRF-Token', '')
        if self.config['protected'] and not compare_digest(csrf, session['csrf']):
            return self._json({'error': 'Token de seguridad no válido. Recarga la página.'}, 403)
        if path == '/logout':
            raw_token = self._cookies().get(cookie_name(self.config), '')
            if raw_token:
                with SECURITY_LOCK:
                    SESSIONS.pop(session_key(raw_token), None)
            cookies = [cookie_header(cookie_name(self.config), '', self.config, 0)]
            return self._redirect('/login' if self.config['protected'] else '/', cookies)

        allowed = ('/api/import', '/api/preview', '/api/export/png', '/api/export/artwork-svg', '/api/export/svg', '/api/export/zip')
        if path not in allowed:
            return self._json({'error': 'Ruta no encontrada.'}, 404)
        try:
            max_bytes = MAX_UPLOAD_MB*1024*1024 if path == '/api/import' else MAX_JSON_BYTES
            data = self._body(max_bytes)
            if path == '/api/import':
                params = parse_qs(urlsplit(self.path).query)
                filename = params.get('filename', [''])[0]
                sheet = params.get('sheet', [None])[0]
                return self._json(read_table(data, filename, sheet))
            payload = json.loads(data.decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('El contenido debe ser un objeto JSON.')
            if path == '/api/preview':
                return self._json(preview_info(payload))
            kind = path.rsplit('/', 1)[-1]
            if self.config['protected']:
                cfg = sheet_config(payload.get('settings', {}))
                products = normalize_products(payload.get('products', []))
                if page_count(products, cfg, blank_labels_count(payload.get('settings', {}))) > MAX_RENDER_EXPORT_PAGES:
                    raise ValueError(f'En Render se permiten hasta {MAX_RENDER_EXPORT_PAGES} pliegos por descarga. Divide el lote para exportarlo.')
                if (kind == 'png' or (kind == 'zip' and artwork_format(payload.get('settings', {})) == 'png')) and round(cfg.width / 25.4 * cfg.dpi) * round(cfg.height / 25.4 * cfg.dpi) > 90_000_000:
                    raise ValueError('El tamaño y resolución del PNG superan la memoria prevista en Render. Reduce el papel o los DPI.')
            if not EXPORT_LOCK.acquire(blocking=False):
                return self._json({'error': 'Ya se está procesando otra descarga. Intenta de nuevo en unos segundos.'}, 429)
            try:
                content, filename, mime = build_export(payload, kind)
            finally:
                EXPORT_LOCK.release()
            return self._reply(content, mime, filename=filename)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return self._json({'error': str(exc)}, 400)
        except MemoryError:
            return self._json({'error': 'Memoria insuficiente. Intenta con 300 DPI o menos pliegos.'}, 503)
        except Exception:
            traceback.print_exc()
            return self._json({'error': 'Error de procesamiento. Revisa los datos o usa un lote más pequeño.'}, 500)


def main():
    config = load_config()  # sin credenciales, Render no inicia: fallo seguro
    if config['protected']:
        port = int(os.environ.get('PORT', '10000'))
        bind = '0.0.0.0'
    else:
        port = 0
        bind = '127.0.0.1'
    server = ThreadingHTTPServer((bind, port), Handler)
    server.aura_config = config
    address = (f'https://{os.getenv("RENDER_EXTERNAL_HOSTNAME", "TU-SERVICIO.onrender.com")}'
               if config['protected'] else f'http://127.0.0.1:{server.server_port}')
    print(f'\nAURA Etiquetas · {"Render (privado)" if config["protected"] else "local"}\n{address}', flush=True)
    if not config['protected']:
        Timer(0.7, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Servidor detenido.')
    finally:
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(f'CONFIGURACIÓN INCORRECTA: {exc}', file=sys.stderr)
        sys.exit(1)
