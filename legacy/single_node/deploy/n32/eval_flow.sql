CREATE TABLE algoos_eval_results (
    judge_id text PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES algoos_submissions(file_id),
    sequence bigint NOT NULL CHECK (sequence > 0),
    result jsonb NOT NULL,
    UNIQUE (file_id, sequence)
);
