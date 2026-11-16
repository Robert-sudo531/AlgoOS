"""Local control-side submission and lookup; queue dispatch is a separate step."""
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

MAX_SOURCE_BYTES = 256 * 1024


def submit(pg, storage, bucket, problem_id, source):
    if problem_id not in {"a_plus_b", "lazy_gold"}:
        raise ValueError("unknown problem_id")
    if not 0 < len(source) <= MAX_SOURCE_BYTES:
        raise ValueError("invalid source length")
    source.decode("utf-8")
    digest = sha256(source).hexdigest()
    file_id = uuid4()
    storage.put_object(Bucket=bucket, Key=f"source/{digest}.cpp", Body=source)
    with psycopg.connect(**pg) as db:
        db.execute(
            "INSERT INTO algoos_submissions(file_id, problem_id, source_hash, status) VALUES (%s,%s,%s,%s)",
            (file_id, problem_id, "sha256:" + digest, "RECEIVED"),
        )
    return file_id


def lookup(pg, file_id):
    with psycopg.connect(**pg, row_factory=dict_row) as db:
        return db.execute(
            "SELECT file_id, problem_id, source_hash, status FROM algoos_submissions WHERE file_id=%s",
            (file_id,),
        ).fetchone()
