"""Portable ASGI gateway. Networking and secret delivery belong to the operator."""
import argparse
import base64
import binascii
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hmac
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
import httpx
import uvicorn
from tools.studio_access import attach_access


def secret(env, name):
    direct, filename = env.get(name), env.get(name + '_FILE', '')
    value = direct
    if value is None:
        value = Path(filename).read_text().rstrip('\r\n') if filename else ''
    if not value or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError(f'{name} is required and must contain printable ASCII')
    return value


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == 'localhost'


def endpoint(value, *, origin=False):
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port is None or 0 < parsed.port < 65536
    except ValueError:
        valid_port = False
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or not valid_port
            or parsed.username or parsed.password or '?' in value or '#' in value
            or any(c.isspace() or ord(c) < 32 for c in value)
            or '\\' in value or (origin and parsed.path not in {'', '/'})):
        raise ValueError('Expected an HTTP(S) URL without credentials, query or fragment')
    return value.rstrip('/')


@dataclass(frozen=True)
class Settings:
    backend_url: str
    public_origin: str
    backend_token: str = field(repr=False)
    auth_mode: str = 'basic'
    username: str = ''
    password: str = field(default='', repr=False)
    host: str = '127.0.0.1'
    port: int = 8080
    root: Path = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        host = env.get('STUDIO_HOST', '127.0.0.1')
        port = int(env.get('STUDIO_PORT', '8080'))
        if not 0 < port < 65536:
            raise ValueError('STUDIO_PORT must be between 1 and 65535')
        backend = endpoint(env.get('STUDIO_BACKEND_URL', ''))
        origin = endpoint(env.get('STUDIO_PUBLIC_ORIGIN', ''), origin=True)
        mode = env.get('STUDIO_AUTH_MODE', 'basic')
        if mode not in {'basic', 'local'}:
            raise ValueError('STUDIO_AUTH_MODE must be basic or local')
        if mode == 'local' and (not loopback(host) or not loopback(urlsplit(origin).hostname)):
            raise ValueError('Local access requires a loopback bind address and public origin')
        username = env.get('STUDIO_LOGIN_USERNAME', '')
        password = secret(env, 'STUDIO_LOGIN_PASSWORD') if mode == 'basic' else ''
        if mode == 'basic':
            if not username or ':' in username or not username.isascii() or any(ord(c) < 32 for c in username):
                raise ValueError('STUDIO_LOGIN_USERNAME is required and must be a valid Basic username')
            if urlsplit(origin).scheme != 'https' and not loopback(urlsplit(origin).hostname):
                raise ValueError('Shared access requires HTTPS at STUDIO_PUBLIC_ORIGIN')
            if len(password) < 16:
                raise ValueError('STUDIO_LOGIN_PASSWORD must contain at least 16 characters')
        return cls(backend, origin, secret(env, 'STUDIO_BACKEND_TOKEN'), mode, username,
                   password, host, port, Path(env.get('STUDIO_STATIC_DIR', str(cls.root))).resolve())


def authorized(header, settings):
    try:
        scheme, encoded = header.split(' ', 1)
        decoded = base64.b64decode(encoded, validate=True).decode('ascii')
        username, password = decoded.split(':', 1)
        user_ok = hmac.compare_digest(username, settings.username)
        pass_ok = hmac.compare_digest(password, settings.password)
        return scheme.lower() == 'basic' and user_ok and pass_ok
    except (ValueError, UnicodeError, binascii.Error):
        return False


def create_app(settings, *, transport=None):
    root = settings.root.resolve()
    if not (root / 'index.html').is_file():
        raise ValueError('STUDIO_STATIC_DIR must contain the compiled frontend')

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                                     trust_env=False, transport=transport,
                                     limits=httpx.Limits(max_connections=50, max_keepalive_connections=10)) as client:
            app.state.client = client
            try:
                yield
            finally:
                app.state.clear_admin_sessions()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=5)

    @app.middleware('http')
    async def security(request, call_next):
        path = request.url.path
        if path in {'/healthz', '/readyz'}:
            response = await call_next(request)
        elif (request.headers.get('host', '').lower() != urlsplit(settings.public_origin).netloc.lower()
              or request.headers.get('origin', '') not in {'', settings.public_origin}
              or request.headers.get('sec-fetch-site') == 'cross-site'):
            response = JSONResponse({'detail': 'Request origin is not allowed'}, status_code=403)
        elif settings.auth_mode == 'basic' and not authorized(request.headers.get('authorization', ''), settings):
            response = JSONResponse({'detail': 'Authentication required'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Basic realm="jira-relay-studio", charset="UTF-8"'})
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = ('public, max-age=31536000, immutable'
            if path.startswith('/assets/') and response.status_code == 200 else 'no-store')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/healthz')
    async def health():
        return {'status': 'ok'}

    @app.get('/readyz')
    async def ready(request: Request):
        try:
            result = await request.app.state.client.get(settings.backend_url + '/readyz',
                headers={'Authorization': 'Bearer ' + settings.backend_token})
            good = result.status_code == 200
        except httpx.HTTPError:
            good = False
        return JSONResponse({'status': 'ready' if good else 'unavailable'}, status_code=200 if good else 503)

    @app.get('/runtime-config')
    async def runtime_config():
        return {'backend_url': settings.backend_url, 'api_path': '/api', 'auth_mode': settings.auth_mode}

    attach_access(app, settings)

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
    async def proxy(path: str, request: Request):
        route = '/' + path
        if route.startswith('/v1/auth/') and not (route == '/v1/auth/me' and request.method == 'GET'):
            return JSONResponse({'detail': 'Use the administrator access session'}, status_code=403)
        if (not (route.startswith('/v1/') or route in {'/metrics', '/readyz', '/healthz'})
                or any(p in {'.', '..'} for p in path.split('/')) or '\\' in path):
            return JSONResponse({'detail': 'Not found'}, status_code=404)
        if request.method not in {'GET', 'POST'}:
            return JSONResponse({'detail': 'Method not allowed'}, status_code=405)
        try:
            size = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
        if size < 0 or size > 10485760:
            return JSONResponse({'detail': 'Body exceeds the configured limit'}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 10485760:
                return JSONResponse({'detail': 'Body exceeds the configured limit'}, status_code=413)
        headers = {'Authorization': 'Bearer ' + settings.backend_token,
                   'Content-Type': request.headers.get('content-type', 'application/json')}
        if 'idempotency-key' in request.headers:
            headers['Idempotency-Key'] = request.headers['idempotency-key']
        query = request.scope.get('query_string', b'').decode('ascii')
        url = settings.backend_url + route + ('?' + query if query else '')
        try:
            async with request.app.state.client.stream(request.method, url, headers=headers, content=bytes(body)) as result:
                if result.status_code == 401:
                    # A backend credential failure must not reopen the browser's Basic login dialog.
                    return JSONResponse({'detail': 'GUI backend credential is invalid or expired'}, status_code=502)
                content = bytearray()
                async for chunk in result.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 33554432:
                        return JSONResponse({'detail': 'Backend response exceeds the configured limit'}, status_code=502)
                return Response(bytes(content), status_code=result.status_code,
                                media_type=result.headers.get('content-type', 'application/json'))
        except httpx.HTTPError:
            return JSONResponse({'detail': 'Backend connection unavailable'}, status_code=502)

    @app.get('/{path:path}')
    async def static(path: str):
        if any(part.startswith('.') for part in path.split('/') if part):
            return Response(status_code=404)
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            return Response(status_code=404)
        if target.is_file():
            return FileResponse(target)
        if path.startswith('assets/') or Path(path).suffix:
            return Response(status_code=404)
        return FileResponse(root / 'index.html')

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help='Override STUDIO_PORT')
    args = parser.parse_args()
    env = dict(os.environ)
    if args.port is not None:
        env['STUDIO_PORT'] = str(args.port)
    try:
        settings = Settings.from_env(env)
        app = create_app(settings)
    except (ValueError, OSError):
        raise SystemExit('Invalid studio configuration. Check STUDIO_* variables and mounted secret files.') from None
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, proxy_headers=False,
                limit_concurrency=100, timeout_graceful_shutdown=30)


if __name__ == '__main__':
    main()
