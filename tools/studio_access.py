"""Same-origin admin UI gateway. Short-lived server-side sessions; no admin env var."""
from dataclasses import dataclass, field
import json
import secrets
import time
from uuid import UUID

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

COOKIE = 'relay_admin_session'
SESSION_TTL = 900
clock = time.monotonic


@dataclass
class Session:
    credential: str = field(repr=False)
    expires: float
    pending: dict | None = field(default=None, repr=False)


def attach_access(app, settings):
    sessions = {}
    app.state.clear_admin_sessions = lambda: sessions.clear()

    async def call(credential, method, path, body=None):
        try:
            async with app.state.client.stream(method, settings.backend_url + path,
                    headers={'Authorization': 'Bearer ' + credential}, json=body) as result:
                content = bytearray()
                async for chunk in result.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1048576:
                        raise HTTPException(502, 'Access response exceeds the configured limit')
                try:
                    data = json.loads(content)
                except (ValueError, UnicodeError):
                    raise HTTPException(502, 'Invalid access response') from None
                return result.status_code, data
        except httpx.HTTPError:
            raise HTTPException(502, 'Backend connection unavailable') from None

    async def body(request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 32768:
                raise HTTPException(413, 'Access request exceeds the configured limit')
        try:
            data = json.loads(content)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'Invalid access request') from None
        if not isinstance(data, dict):
            raise HTTPException(400, 'Invalid access request')
        return data

    def cleanup():
        for key in list(sessions):
            if sessions[key].expires <= clock():
                del sessions[key]

    async def administrator(request):
        cleanup()
        key = request.cookies.get(COOKIE, '')
        session = sessions.get(key)
        if session is None:
            raise HTTPException(403, 'Administrator session required')
        status, identity = await call(session.credential, 'GET', '/v1/auth/me')
        if status != 200 or 'auth:admin' not in identity.get('permissions', []):
            if status in {401, 403} or status == 200:
                sessions.pop(key, None)
                raise HTTPException(403, 'Administrator session expired or revoked')
            raise HTTPException(502, 'Cannot verify administrator access')
        return session, identity

    @app.post('/access/session')
    async def unlock(request: Request):
        data = await body(request)
        credential = data.get('token')
        if (not set(data) <= {'token', 'confirm_rotation'} or not isinstance(credential, str) or not 32 <= len(credential) <= 16384
                or any(ord(c) < 33 or ord(c) > 126 for c in credential)):
            raise HTTPException(400, 'Invalid administrator credential')
        status, identity = await call(credential, 'GET', '/v1/auth/me')
        if status != 200:
            raise HTTPException(403 if status in {401, 403} else 502, 'Administrator verification failed')
        if data.get('confirm_rotation') is True:
            if not identity.get('pending_administrator'):
                raise HTTPException(409, 'Use the new pending administrator token to confirm')
            code, _ = await call(credential, 'POST', '/v1/auth/tokens/self/confirm')
            if code != 200:
                raise HTTPException(403 if code == 401 else 409 if code == 409 else 502, 'Pending rotation could not be confirmed')
            status, identity = await call(credential, 'GET', '/v1/auth/me')
            if status != 200:
                raise HTTPException(502, 'Administrator verification failed')
        if 'auth:admin' not in identity.get('permissions', []):
            raise HTTPException(403, 'Administrator credential required')
        cleanup()
        previous = request.cookies.get(COOKIE, '')
        if len(sessions) >= 32 and previous not in sessions:
            raise HTTPException(429, 'Administrator session capacity reached')
        sessions.pop(previous, None)
        key = secrets.token_urlsafe(32)
        sessions[key] = Session(credential, clock() + SESSION_TTL)
        response = JSONResponse({'authenticated': True, 'identity': identity, 'session_seconds': SESSION_TTL})
        response.set_cookie(COOKIE, key, max_age=SESSION_TTL, httponly=True,
                            secure=settings.public_origin.startswith('https://'), samesite='strict', path='/access')
        return response

    @app.get('/access/session')
    async def status(request: Request):
        session, identity = await administrator(request)
        pending = {k: v for k, v in (session.pending or {}).items() if k != 'token'} or None
        return {'authenticated': True, 'identity': identity, 'pending': pending,
                'session_seconds': max(0, int(session.expires - clock()))}

    @app.post('/access/logout')
    async def logout(request: Request):
        sessions.pop(request.cookies.get(COOKIE, ''), None)
        response = JSONResponse({'authenticated': False})
        response.delete_cookie(COOKIE, path='/access')
        return response

    @app.get('/access/backend')
    async def backend(request: Request):
        await administrator(request)
        code, identity = await call(settings.backend_token, 'GET', '/v1/auth/me')
        return JSONResponse(identity, status_code=403 if code == 401 else code)

    @app.get('/access/tokens')
    async def tokens(request: Request, offset: int = 0):
        session, _ = await administrator(request)
        if not 0 <= offset <= 1000000:
            raise HTTPException(400, 'Invalid offset')
        code, data = await call(session.credential, 'GET', f'/v1/auth/tokens?limit=50&offset={offset}')
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/tokens')
    async def create(request: Request):
        session, _ = await administrator(request)
        code, data = await call(session.credential, 'POST', '/v1/auth/tokens', await body(request))
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/tokens/{token_id}/revoke')
    async def revoke(token_id: UUID, request: Request):
        session, _ = await administrator(request)
        code, data = await call(session.credential, 'POST', f'/v1/auth/tokens/{token_id}/revoke')
        if code == 200 and session.pending and session.pending.get('id') == str(token_id):
            session.pending = None
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/rotation/start')
    async def rotate(request: Request):
        session, _ = await administrator(request)
        code, data = await call(session.credential, 'POST', '/v1/auth/tokens/self/rotate')
        if code == 200:
            session.pending = data
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/rotation/confirm')
    async def confirm(request: Request):
        session, _ = await administrator(request)
        if not session.pending:
            raise HTTPException(409, 'No pending rotation in this session')
        credential = session.pending['token']
        code, data = await call(credential, 'POST', '/v1/auth/tokens/self/confirm')
        if code == 200:
            session.credential, session.pending = credential, None
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/backend/rotate')
    async def rotate_backend(request: Request):
        await administrator(request)
        code, identity = await call(settings.backend_token, 'GET', '/v1/auth/me')
        if code != 200 or 'auth:admin' in identity.get('permissions', []):
            raise HTTPException(409, 'Configure a delegated GUI credential first')
        code, data = await call(settings.backend_token, 'POST', '/v1/auth/tokens/self/rotate')
        return JSONResponse(data, status_code=403 if code == 401 else code)

    @app.post('/access/backend/confirm')
    async def confirm_backend(request: Request):
        await administrator(request)
        code, identity = await call(settings.backend_token, 'GET', '/v1/auth/me')
        if code != 200 or not identity.get('pending'):
            raise HTTPException(409, 'Update the GUI secret and restart the gateway with the new token first')
        code, data = await call(settings.backend_token, 'POST', '/v1/auth/tokens/self/confirm')
        return JSONResponse(data, status_code=403 if code == 401 else code)
