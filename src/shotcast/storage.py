"""Tiny storage layer so the same pipeline code runs on a laptop or on S3.

Keys are relative paths such as ``raw/events/123.json``. A store is chosen from one root string:
``data`` (local folder) or ``s3://bucket/prefix``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class Store(Protocol):
    root: str

    def exists(self, key: str) -> bool: ...
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def fingerprints(self, prefix: str) -> dict[str, str]: ...
    def uri(self, key: str) -> str: ...


class LocalStore:
    def __init__(self, root: str | Path):
        self.root = str(root)
        self._path = Path(root)

    def _p(self, key: str) -> Path:
        return self._path / key

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def read_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        # Write to a temp file, then rename, so a crash never leaves a half-written file behind.
        target = self._p(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._p(prefix)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self._path)) for p in base.rglob("*") if p.is_file())

    def fingerprints(self, prefix: str) -> dict[str, str]:
        """key -> a short string that changes whenever the file's content changes (size + mtime here)."""
        out = {}
        for k in self.list_keys(prefix):
            st = self._p(k).stat()
            out[k] = f"{st.st_size}-{st.st_mtime_ns}"
        return out

    def uri(self, key: str) -> str:
        return str(self._p(key).resolve())


class S3Store:
    def __init__(self, bucket: str, prefix: str = "", client=None):
        import boto3  # imported lazily so local-only use needs no AWS setup

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.root = f"s3://{bucket}/{self.prefix}".rstrip("/")
        self._s3 = client or boto3.client("s3")

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def read_bytes(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def write_bytes(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        pager = self._s3.get_paginator("list_objects_v2")
        for page in pager.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                keys.append(k[len(self.prefix) + 1 :] if self.prefix else k)
        return sorted(keys)

    def fingerprints(self, prefix: str) -> dict[str, str]:
        """key -> ETag. S3 returns it in the listing itself, so no file has to be downloaded to compare."""
        out: dict[str, str] = {}
        pager = self._s3.get_paginator("list_objects_v2")
        for page in pager.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                out[k[len(self.prefix) + 1 :] if self.prefix else k] = obj["ETag"].strip('"')
        return out

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._k(key)}"


def get_store(root: str | None = None) -> Store:
    root = root or os.environ.get("SHOTCAST_ROOT", "data")
    if root.startswith("s3://"):
        bucket, _, prefix = root[5:].partition("/")
        return S3Store(bucket, prefix)
    return LocalStore(root)
