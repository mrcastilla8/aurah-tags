"""Prueba HTTP real, con Render simulado en localhost y cookies seguras."""
from __future__ import annotations
import http.client
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import Handler, ThreadingHTTPServer, create_session, load_config, verify_password, register_login_attempt, SESSIONS, ATTEMPTS
from crear_credenciales import make_hash


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'ClaveSegura!98765'
        cls.original = {k: os.environ.get(k) for k in ('AURA_MODE', 'AURA_PASSWORD_HASH', 'AURA_SESSION_SECRET', 'RENDER')}
        os.environ['AURA_MODE'] = 'render'
        os.environ['AURA_PASSWORD_HASH'] = make_hash(cls.password)
        os.environ['AURA_SESSION_SECRET'] = 'x' * 48
        os.environ['RENDER'] = 'true'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.aura_config = load_config()
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        for key, val in cls.original.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        with __import__('app').SECURITY_LOCK:
            SESSIONS.clear()
            ATTEMPTS.clear()

    def setUp(self):
        with __import__('app').SECURITY_LOCK:
            ATTEMPTS.clear()

    def query(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        conn.close()
        return result

    def login(self):
        code, headers, html = self.query('GET', '/login')
        self.assertEqual(code, 200)
        self.assertIn(b'type="password"', html)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', html).group(1).decode()
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        body = urlencode({'csrf': csrf, 'password': self.password}).encode()
        code, headers, _ = self.query('POST', '/login', body, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': cookie,
            'Origin': f'https://127.0.0.1:{self.port}',
        })
        self.assertEqual(code, 303)
        self.assertIn('Secure', headers['Set-Cookie'])
        auth_cookie = headers['Set-Cookie'].split(';', 1)[0]
        _, _, page = self.query('GET', '/', headers={'Cookie': auth_cookie})
        csrf = re.search(rb'name="aura-csrf" content="([^"]+)"', page).group(1).decode()
        return auth_cookie, csrf

    def test_unauthenticated_routes_are_blocked(self):
        for path in ('/', '/index.html', '/style.css', '/app.js', '/api/session'):
            code, _, body = self.query('GET', path)
            self.assertIn(code, (303, 403, 401), (path, code))
            self.assertNotIn(b'function init()', body)
        for path in ('/api/preview', '/api/export/svg', '/api/export/artwork-svg', '/api/import'):
            code, _, _ = self.query('POST', path, b'{}', {'Content-Type': 'application/json'})
            self.assertEqual(code, 401)
        self.assertEqual(self.query('GET', '/health')[0], 200)

    def test_wrong_password(self):
        code, headers, html = self.query('GET', '/login')
        csrf = re.search(rb'name="csrf" value="([^"]+)"', html).group(1).decode()
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        body = urlencode({'csrf': csrf, 'password': 'la-contrasena-no-es'}).encode()
        code, _, body = self.query('POST', '/login', body, {'Cookie': cookie})
        self.assertEqual(code, 401)
        self.assertIn(b'Contrase', body)

    def test_login_csv_preview_export_and_logout(self):
        cookie, csrf = self.login()
        headers = {'Cookie': cookie, 'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}
        self.assertEqual(self.query('GET', '/app.js', headers={'Cookie': cookie})[0], 200)
        self.assertEqual(self.query('GET', '/style.css', headers={'Cookie': cookie})[0], 200)
        code, _, _ = self.query('POST', '/api/preview', b'{}', {'Cookie': cookie})
        self.assertEqual(code, 403, 'Sin token CSRF debe fallar')
        example = (ROOT / 'ejemplos/productos_ejemplo.csv').read_bytes()
        code, _, body = self.query('POST','/api/import?filename=joyas.csv', example, {'Cookie':cookie, 'X-CSRF-Token':csrf})
        self.assertEqual(code, 200)
        self.assertIn(b'headers', body)
        import json
        payload = {'products': [{'code': 'AU-01', 'name': 'Aretes', 'price': 30, 'copies': 1}],
                   'settings': {'format': 'A4', 'dpi': 150}}
        data = json.dumps(payload).encode()
        code, _, preview = self.query('POST', '/api/preview', data, headers)
        self.assertEqual(code, 200)
        self.assertIn(b'svg', preview)
        code, headers2, cut = self.query('POST', '/api/export/svg', data, headers)
        self.assertEqual(code, 200)
        self.assertIn(b'<svg', cut)
        self.assertNotIn(b'Aretes', cut)
        self.assertIn('attachment', headers2['Content-Disposition'])
        code, headers3, img = self.query('POST', '/api/export/png', data, headers)
        self.assertEqual(code, 200)
        self.assertEqual(img[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(headers3['Content-Type'], 'image/png')
        svg_settings = {**payload, 'settings': {'format':'A4','artworkFormat':'svg'}}
        svg_data=json.dumps(svg_settings).encode()
        code, svg_headers, design_svg = self.query('POST', '/api/export/artwork-svg', svg_data, headers)
        self.assertEqual(code, 200)
        self.assertEqual(svg_headers['Content-Type'], 'image/svg+xml')
        self.assertIn(b'ARTE_PARA_IMPRIMIR', design_svg)
        self.assertNotIn(b'<text ', design_svg)  # Contornos vectoriales de glifos
        self.assertNotIn(b'#ff0000', design_svg)
        code, bundle_headers, bundle = self.query('POST', '/api/export/zip', svg_data, headers)
        self.assertEqual(code, 200)
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn('AURA_pliego_01_impresion.svg', archive.namelist())
            self.assertIn('AURA_pliego_01_troquel.svg', archive.namelist())
        self.assertEqual(self.query('POST', '/logout', b'', headers)[0], 303)
        self.assertEqual(self.query('GET', '/api/session', headers={'Cookie': cookie})[0], 401)

    def test_bad_origin_and_login_csrf(self):
        code, _, _ = self.query('POST', '/login', urlencode({'password': self.password}), {})
        self.assertEqual(code, 401)
        cookie, csrf = self.login()
        code, _, _ = self.query('POST', '/api/preview', b'{}', {
            'Cookie': cookie, 'X-CSRF-Token': csrf, 'Origin':'https://malicioso.example'})
        self.assertEqual(code, 403)

    def test_rate_limit(self):
        with __import__('app').SECURITY_LOCK:
            ATTEMPTS.clear()
        for _ in range(8):
            self.assertTrue(register_login_attempt('198.51.100.1')[0])
        allowed, delay = register_login_attempt('198.51.100.1')
        self.assertFalse(allowed)
        self.assertGreater(delay, 0)

    def test_secure_password_hash(self):
        self.assertTrue(verify_password(self.password, os.environ['AURA_PASSWORD_HASH']))
        self.assertFalse(verify_password('wrong', os.environ['AURA_PASSWORD_HASH']))


if __name__ == '__main__':
    unittest.main()
