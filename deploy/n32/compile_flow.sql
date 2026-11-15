CREATE TABLE algoos_compile_jobs (
    file_id uuid PRIMARY KEY REFERENCES algoos_submissions(file_id),
    enqueue_seq bigint GENERATED ALWAYS AS IDENTITY,
    task jsonb,
    eval_plan jsonb NOT NULL,
    result jsonb
);

CREATE TABLE algoos_outbox (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    msg_id text NOT NULL UNIQUE,
    file_id uuid NOT NULL REFERENCES algoos_submissions(file_id),
    subject text NOT NULL,
    payload jsonb NOT NULL,
    published_at timestamptz
);
