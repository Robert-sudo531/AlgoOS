-- AlgoOS distributed control-plane schema (v1).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE problems (
    problem_id      text PRIMARY KEY,
    title           text NOT NULL,
    time_limit_ms   integer NOT NULL CHECK (time_limit_ms > 0),
    memory_limit_mib integer NOT NULL CHECK (memory_limit_mib > 0),
    judge_mode      text NOT NULL CHECK (judge_mode IN ('normal', 'special')),
    compare_mode    text NOT NULL CHECK (compare_mode IN ('common', 'special')),
    bundle_hash     text NOT NULL,
    checker_hash    text,
    config          jsonb NOT NULL,
    imported_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE submissions (
    file_id             uuid PRIMARY KEY,
    problem_id          text NOT NULL REFERENCES problems(problem_id),
    user_id             text NOT NULL DEFAULT 'anonymous',
    language            text NOT NULL DEFAULT 'cpp17',
    source_hash         text NOT NULL,
    status              text NOT NULL CHECK (status IN
        ('QUEUED','COMPILING','COMPILED','EVALUATING','PENDING_SPECIAL',
         'SHORT_CIRCUITED','CANCELLED','FINAL','CE')),
    verdict             text,
    reason              text,
    compile_log         text NOT NULL DEFAULT '',
    evaluation_mode     text NOT NULL DEFAULT 'ordered_short_circuit',
    short_circuit_window integer NOT NULL DEFAULT 4,
    scheduling_strategy text NOT NULL DEFAULT 'cache_aware',
    cancel_generation   integer NOT NULL DEFAULT 0,
    submitted_at        timestamptz NOT NULL DEFAULT now(),
    finalized_at        timestamptz,
    CONSTRAINT unique_submission_source UNIQUE (file_id, source_hash)
);
CREATE INDEX submissions_status_idx ON submissions (status);
CREATE INDEX submissions_submitted_idx ON submissions (submitted_at DESC);

CREATE TABLE submission_cases (
    file_id        uuid NOT NULL REFERENCES submissions(file_id) ON DELETE CASCADE,
    test_id        text NOT NULL,
    sequence       integer NOT NULL CHECK (sequence > 0),
    attempt        integer NOT NULL DEFAULT 1,
    judge_id       text NOT NULL,
    target_node_id text,
    judge_mode     text NOT NULL DEFAULT 'normal',
    state          text NOT NULL DEFAULT 'READY'
        CHECK (state IN ('READY','DISPATCHED','PENDING_SPECIAL','DONE','CANCELLED')),
    limits         jsonb NOT NULL,
    verdict        text,
    reason         text,
    PRIMARY KEY (file_id, test_id),
    UNIQUE (file_id, sequence),
    UNIQUE (judge_id)
);

CREATE TABLE compile_jobs (
    file_id      uuid PRIMARY KEY REFERENCES submissions(file_id) ON DELETE CASCADE,
    enqueue_seq  bigint GENERATED ALWAYS AS IDENTITY,
    task         jsonb NOT NULL,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_results (
    judge_id    text PRIMARY KEY,
    file_id     uuid NOT NULL REFERENCES submissions(file_id) ON DELETE CASCADE,
    test_id     text NOT NULL,
    sequence    integer NOT NULL,
    judge_mode  text NOT NULL,
    result      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eval_results_file_idx ON eval_results (file_id, sequence);

CREATE TABLE eval_nodes (
    node_id       text PRIMARY KEY,
    role          text NOT NULL CHECK (role IN ('control','compile','eval','special')),
    endpoint      text NOT NULL DEFAULT '',
    version       text NOT NULL DEFAULT '',
    slots_total   integer NOT NULL DEFAULT 1,
    slots_used    integer NOT NULL DEFAULT 0,
    load_score    double precision NOT NULL DEFAULT 0,
    cached_hashes jsonb NOT NULL DEFAULT '[]',
    status        text NOT NULL DEFAULT 'ALIVE',
    last_heartbeat timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eval_nodes_heartbeat_idx ON eval_nodes (last_heartbeat);

CREATE TABLE processed_messages (
    msg_id      text PRIMARY KEY,
    consumer    text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    msg_id       text NOT NULL UNIQUE,
    file_id      uuid NOT NULL,
    subject      text NOT NULL,
    payload      jsonb NOT NULL,
    publish_seq  bigint,
    published_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_unpublished_idx ON outbox (event_id) WHERE published_at IS NULL;

CREATE TABLE cancel_events (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      uuid NOT NULL,
    judge_id     text,
    scope        text NOT NULL CHECK (scope IN ('submission','case')),
    sequence_boundary integer,
    cancel_generation integer NOT NULL,
    reason       text NOT NULL,
    origin       text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE experiments (
    experiment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('simulation','cluster')),
    config        jsonb NOT NULL,
    status        text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','RUNNING','DONE','FAILED')),
    summary       jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);

CREATE TABLE experiment_metrics (
    experiment_id bigint NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
    strategy      text NOT NULL,
    metric        text NOT NULL,
    value         double precision NOT NULL,
    PRIMARY KEY (experiment_id, strategy, metric)
);
