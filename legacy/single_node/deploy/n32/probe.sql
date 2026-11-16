CREATE TABLE n32_probe_receipts (
    msg_id text PRIMARY KEY,
    file_id text NOT NULL,
    source_hash text NOT NULL,
    test_bundle_hash text NOT NULL,
    stream_sequence bigint NOT NULL CHECK (stream_sequence > 0),
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
