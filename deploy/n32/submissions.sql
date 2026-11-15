CREATE TABLE algoos_submissions (
    file_id uuid PRIMARY KEY,
    problem_id text NOT NULL,
    source_hash text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
