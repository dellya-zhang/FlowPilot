"""All tests use isolated storage and mock provider; never read paid test state."""
import os
import tempfile
from pathlib import Path

_ROOT = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{Path(_ROOT.name)/'test.db'}"
os.environ['ARTIFACT_DIR'] = str(Path(_ROOT.name)/'artifacts')
os.environ['AI_PROVIDER'] = 'mock'
os.environ['REDIS_URL'] = ''
os.environ['S3_ENDPOINT_URL'] = ''
os.environ['ACCESS_TOKEN'] = ''
