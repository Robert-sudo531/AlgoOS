"""Content-addressed object storage (MinIO / any S3-compatible endpoint)."""

from __future__ import annotations

import io
from contextlib import closing
from hashlib import sha256

import boto3
from botocore.config import Config

from .config import Settings

SOURCE_SUFFIX = ".cpp"
BUNDLE_SUFFIX = ".zip"
CHECKER_SUFFIX = ".bin"


class ObjectStoreError(RuntimeError):
    pass


def digest_bytes(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def hex_of(digest: str) -> str:
    if not digest.startswith("sha256:"):
        raise ObjectStoreError("hash must use the sha256: prefix")
    return digest.removeprefix("sha256:")


class ObjectStore:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(connect_timeout=2, read_timeout=10, proxies={},
                          retries={"total_max_attempts": 3},
                          s3={"addressing_style": "path"},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception as error:  # pragma: no cover - startup
                raise ObjectStoreError(f"cannot create bucket {self.bucket}: {error}") from error

    # ---- key helpers -------------------------------------------------
    def source_key(self, digest: str) -> str:
        return f"source/{hex_of(digest)}{SOURCE_SUFFIX}"

    def binary_key(self, digest: str) -> str:
        return f"bin/{hex_of(digest)}"

    def bundle_key(self, digest: str) -> str:
        return f"tests/{hex_of(digest)}{BUNDLE_SUFFIX}"

    def checker_key(self, digest: str) -> str:
        return f"checker/{hex_of(digest)}{CHECKER_SUFFIX}"

    def answer_key(self, digest: str) -> str:
        return f"answer/{hex_of(digest)}.out"

    def input_key(self, digest: str) -> str:
        return f"input/{hex_of(digest)}.in"

    def output_key(self, file_id: str, test_id: str, attempt: int, digest: str) -> str:
        return f"out/{file_id}/{test_id}/{attempt}/{hex_of(digest)}.out"

    def shared_output_key(self, digest: str) -> str:
        return f"outobj/{hex_of(digest)}.out"

    def compile_log_key(self, file_id: str) -> str:
        return f"logs/compile/{file_id}.log"

    # ---- operations --------------------------------------------------
    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return digest_bytes(data)


    def put_content(self, kind: str, data: bytes, *, suffix: str = "") -> str:
        digest = digest_bytes(data)
        key = f"{kind}/{hex_of(digest)}{suffix}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return digest

    def get(self, key: str, cap: int) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        with closing(response["Body"]) as body:
            data = body.read(cap + 1)
        if len(data) > cap:
            raise ObjectStoreError(f"object {key} exceeds local cap {cap}")
        return data

    def fetch_verified(self, kind: str, digest: str, *, suffix: str, cap: int) -> bytes:
        data = self.get(f"{kind}/{hex_of(digest)}{suffix}", cap)
        if digest_bytes(data) != digest:
            raise ObjectStoreError(f"{kind} object failed SHA-256 verification")
        return data

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def put_zip(self, data: bytes) -> str:
        return self.put_content("tests", data, suffix=BUNDLE_SUFFIX)

    def put_checker(self, data: bytes) -> str:
        return self.put_content("checker", data, suffix=CHECKER_SUFFIX)

    def put_answer(self, data: bytes) -> str:
        return self.put_content("answer", data, suffix=".out")

    def put_input(self, data: bytes) -> str:
        return self.put_content("input", data, suffix=".in")

    def put_output(self, file_id: str, test_id: str, attempt: int, data: bytes) -> str:
        digest = digest_bytes(data)
        self.client.put_object(Bucket=self.bucket,
                               Key=self.output_key(file_id, test_id, attempt, digest),
                               Body=data)
        self.client.put_object(Bucket=self.bucket, Key=self.shared_output_key(digest),
                               Body=data)
        return digest


def make_zip(entries: dict[str, bytes]) -> bytes:
    """Deterministic zip (sorted names, fixed timestamp) for stable bundle hashes."""
    from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, entries[name])
    return buffer.getvalue()
