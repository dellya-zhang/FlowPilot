"""Bound API request buffering; this service has no streaming upload endpoints."""
import asyncio
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, max_bytes=65536, read_timeout_seconds=10):
        self.app = app
        self.max_bytes = max_bytes
        self.read_timeout_seconds = read_timeout_seconds

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path','').startswith('/api/'):
            return await self.app(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(self.read_timeout_seconds):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > self.max_bytes:
                        return await JSONResponse({'detail':'Request body exceeds 64 KiB'},status_code=413)(scope,receive,send)
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'detail':'Request body read timed out'},status_code=408)(scope,receive,send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()

        await self.app(scope,replay,send)
