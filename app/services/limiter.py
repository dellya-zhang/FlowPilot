"""Optional Redis fixed-window request throttling, not a distributed job queue."""
import time
from fastapi import HTTPException, Request
from redis.asyncio import Redis
from app.core.config import get_settings


async def limit(request: Request, scope: str, max_requests: int = 20):
    url = get_settings().redis_url
    if not url:
        return
    client = Redis.from_url(url, decode_responses=True)
    # Remote IP is only suitable for a demo without an untrusted reverse proxy.
    ip = request.client.host if request.client else "anonymous"
    slot = int(time.time()) // 60
    key = f"limit:{scope}:{ip}:{slot}"
    try:
        # Single script executes increment + expiration atomically.
        n = await client.eval("local n=redis.call('INCR', KEYS[1]); "
                              "if n==1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end; return n",
                              1, key, 75)
        if n > max_requests:
            raise HTTPException(429, "Request rate exceeded")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Rate limiter temporarily unavailable") from exc
    finally:
        await client.aclose()
