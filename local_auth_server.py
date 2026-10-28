"""Local-only auth troubleshooting launcher; never log queries or cookie values."""

from pathlib import Path
from http.cookies import SimpleCookie, CookieError
import logging
import os

from dotenv import load_dotenv
import streamlit as st
from starlette.middleware import Middleware


logger = logging.getLogger('querypilot.local_auth')


def has_identity_cookie(headers):
    cookies = SimpleCookie()
    try:
        for name, value in headers:
            if name.lower() == b'cookie':
                cookies.load(value.decode('latin-1'))
    except CookieError:
        return False
    return '_streamlit_user' in cookies


class AuthDiagnostics:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        tracked = path in {'/auth/login', '/auth/logout', '/oauth2callback'}
        if scope['type'] == 'websocket' and path == '/_stcore/stream':
            origin = next((value for name, value in scope.get('headers', [])
                           if name.lower() == b'origin'), b'')
            logger.warning('Auth diagnostic: websocket identity_cookie_present=%s origin_matches=%s',
                           has_identity_cookie(scope.get('headers', [])),
                           origin == b'http://localhost:8501')

        async def safe_send(message):
            if tracked and message['type'] == 'http.response.start':
                # Only fixed event names and booleans: no URL queries, headers,
                # claims, tokens, identity details, or exception messages.
                headers = message.get('headers', [])
                sets_identity = any(
                    name.lower() == b'set-cookie' and value.startswith(b'_streamlit_user=')
                    and b'Max-Age=0' not in value
                    for name, value in headers
                )
                logger.warning('Auth diagnostic: route=%s status=%s sets_identity_cookie=%s',
                               path, message['status'], sets_identity)
            await send(message)

        await self.app(scope, receive, safe_send)


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    os.chdir(root)
    load_dotenv(root / '.env')
    if os.getenv('APP_ENV') != 'development' or os.getenv('DEV_AUTH_BYPASS', 'false').lower() != 'false':
        raise SystemExit('Local diagnostics require development with authentication enabled.')
    app = st.App(root / 'streamlit_app.py', middleware=[Middleware(AuthDiagnostics)])
    app.run(config={'server.address': 'localhost', 'server.port': 8501,
                    'server.headless': True, 'browser.gatherUsageStats': False})
