ALTER TABLE submission_cases
    ADD COLUMN dispatch_generation integer NOT NULL DEFAULT 0
    CHECK (dispatch_generation >= 0);
