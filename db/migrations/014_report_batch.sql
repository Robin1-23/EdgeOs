-- The nightly report batch logs to pipeline_runs like the data pipelines; 'partial' = stopped by budget or deadline.
ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_pipeline_check,
    ADD CONSTRAINT pipeline_runs_pipeline_check CHECK (pipeline IN ('amfi_nav', 'nse_bhavcopy', 'nse_indices', 'report_batch'));
ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_status_check,
    ADD CONSTRAINT pipeline_runs_status_check CHECK (status IN ('running', 'loaded', 'no_file', 'failed', 'done', 'partial'));
