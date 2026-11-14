from pathlib import Path
import hashlib
import os
import tempfile
from fastapi import HTTPException
from botocore.exceptions import BotoCoreError
import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings


def _client():
    cfg = get_settings()
    return boto3.client("s3", endpoint_url=cfg.s3_endpoint_url, aws_access_key_id=cfg.s3_access_key,
                        aws_secret_access_key=cfg.s3_secret_key, region_name="us-east-1")


def put_blob(key: str, body: bytes):
    cfg = get_settings()
    if cfg.s3_endpoint_url:
        client = _client()
        try:
            client.head_bucket(Bucket=cfg.s3_bucket)
        except ClientError:
            client.create_bucket(Bucket=cfg.s3_bucket)
        client.put_object(Bucket=cfg.s3_bucket, Key=key, Body=body, ContentType="text/csv")
    else:
        root = Path(cfg.artifact_dir).resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Invalid artifact key")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace atomically so a concurrent reader never observes a partial CSV.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
                temporary = Path(out.name)
                out.write(body)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def get_blob(key: str) -> bytes:
    cfg = get_settings()
    if cfg.s3_endpoint_url:
        return _client().get_object(Bucket=cfg.s3_bucket, Key=key)["Body"].read()
    root = Path(cfg.artifact_dir).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid artifact key")
    return path.read_bytes()


def get_verified_blob(artifact):
    try:
        body = get_blob(artifact.key)
    except (OSError, BotoCoreError) as exc:
        raise HTTPException(503, "Artifact storage unavailable", headers={"Retry-After":"2"}) from exc
    if hashlib.sha256(body).hexdigest() != artifact.sha256:
        raise HTTPException(503, "Artifact integrity check failed")
    return body
