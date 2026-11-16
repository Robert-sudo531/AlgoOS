-- Step 5: dispatch timestamps for dead-node reaping and a small session audit table.
ALTER TABLE submission_cases
    ADD COLUMN dispatched_at timestamptz;

CREATE TABLE special_sessions (
    session_id   text PRIMARY KEY,
    judge_id     text NOT NULL,
    eval_node_id text NOT NULL,
    state        text NOT NULL DEFAULT 'OPEN'
        CHECK (state IN ('OPEN','REPLIED','EXPIRED')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
