import hashlib
import os
import tempfile

import boto3
from django.conf import settings


def store_original(data, content_type):
    digest = hashlib.sha256(data).hexdigest()
    key = f"originals/{digest[:2]}/{digest}"
    if settings.ORIGINAL_STORAGE_BACKEND == "local":
        if not settings.DEBUG:
            raise ValueError("Local original storage is only enabled for development")
        directory = settings.ORIGINAL_STORAGE_ROOT / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / digest
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temp:
            temp.write(data)
            temporary_path = temp.name
        try:
            os.replace(temporary_path, target)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return {"sha256": digest, "object_key": key, "size_bytes": len(data)}
    if settings.ORIGINAL_STORAGE_BACKEND != "s3":
        raise ValueError("Unsupported original storage backend")
    client = boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
    )
    client.put_object(Bucket=settings.S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return {"sha256": digest, "object_key": key, "size_bytes": len(data)}


def read_original(object_key):
    """Read a previously stored original without trusting a caller-provided path."""
    if not object_key.startswith("originals/") or ".." in object_key:
        raise ValueError("Invalid original object key")
    if settings.ORIGINAL_STORAGE_BACKEND == "local":
        digest = object_key.rsplit("/", 1)[-1]
        if len(digest) != 64:
            raise ValueError("Invalid original object key")
        return (settings.ORIGINAL_STORAGE_ROOT / digest[:2] / digest).read_bytes()
    if settings.ORIGINAL_STORAGE_BACKEND != "s3":
        raise ValueError("Unsupported original storage backend")
    client = boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
    )
    return client.get_object(Bucket=settings.S3_BUCKET, Key=object_key)["Body"].read()
