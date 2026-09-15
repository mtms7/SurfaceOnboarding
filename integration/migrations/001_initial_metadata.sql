-- Metadata-only PostgreSQL schema. This file is intentionally not executed
-- by the local scaffold and contains no connection configuration.

BEGIN;

CREATE TABLE workflow_items (
    salesforce_record_id VARCHAR(18) PRIMARY KEY,
    co_number TEXT NOT NULL CHECK (co_number ~ '^CO-[0-9]+$'),
    source_revision TEXT NOT NULL CHECK (source_revision <> ''),
    engine_value TEXT NOT NULL CHECK (engine_value IN (
        'case_1_new_surface_only', 'case_2_new_ce_only',
        'case_3_combined_baseline', 'case_4_renew_surface_new_ce',
        'case_5_renew_ce_new_surface', 'case_6_renew_both'
    )),
    policy_version TEXT NOT NULL CHECK (policy_version <> ''),
    intent_hash CHAR(64) NOT NULL,
    idempotency_key CHAR(64) NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN (
        'discovered', 'validating', 'pending', 'manual_review_required',
        'ready', 'executing', 'verifying', 'scanning', 'finished',
        'complete', 'blocked', 'uncertain_external_result'
    )),
    reason_code TEXT NULL CHECK (reason_code IS NULL OR reason_code IN (
        'source_data_mismatch', 'case_not_mapped_yet', 'mapping_not_approved',
        'manual_review_required', 'authentication_blocked',
        'duplicate_or_collision', 'verification_failed',
        'retryable_read_failure', 'uncertain_external_result'
    )),
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (salesforce_record_id ~ '^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$'),
    CHECK (state <> 'uncertain_external_result' OR reason_code = 'uncertain_external_result')
);

CREATE TABLE sync_jobs (
    job_id UUID PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('all', 'record_id', 'co_number')),
    scope_identifier TEXT NULL,
    requested_by VARCHAR(128) NOT NULL CHECK (requested_by ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
    requested_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    failure_category TEXT NULL CHECK (failure_category IS NULL OR failure_category IN (
        'authentication', 'invalid_response', 'timeout', 'transport', 'validation'
    )),
    CHECK ((scope_kind = 'all' AND scope_identifier IS NULL) OR
           (scope_kind = 'record_id' AND scope_identifier ~ '^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$') OR
           (scope_kind = 'co_number' AND scope_identifier ~ '^CO-[0-9]+$')),
    CHECK ((status = 'queued' AND started_at IS NULL AND completed_at IS NULL) OR
           (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR
           (status IN ('succeeded', 'failed') AND started_at IS NOT NULL AND completed_at IS NOT NULL)),
    CHECK (completed_at IS NULL OR completed_at >= started_at),
    CHECK ((status = 'failed' AND failure_category IS NOT NULL) OR
           (status <> 'failed' AND failure_category IS NULL))
);

CREATE UNIQUE INDEX one_active_sync_job_per_scope
    ON sync_jobs (scope_kind, COALESCE(scope_identifier, ''))
    WHERE status IN ('queued', 'running');

CREATE TABLE manual_review_decisions (
    decision_id UUID PRIMARY KEY,
    workflow_record_id VARCHAR(18) NOT NULL REFERENCES workflow_items(salesforce_record_id),
    source_revision TEXT NOT NULL CHECK (source_revision <> ''),
    intent_hash CHAR(64) NOT NULL,
    reviewer_id VARCHAR(128) NOT NULL CHECK (reviewer_id ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
    approved BOOLEAN NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    CHECK (workflow_record_id ~ '^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$'),
    UNIQUE (workflow_record_id, source_revision, intent_hash)
);

CREATE TABLE audit_events (
    event_id UUID PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'workflow_created', 'workflow_transitioned', 'sync_requested',
        'manual_review_recorded', 'sync_coalesced', 'sync_started', 'sync_finished'
    )),
    actor_id VARCHAR(128) NOT NULL CHECK (actor_id ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
    occurred_at TIMESTAMPTZ NOT NULL,
    workflow_record_id VARCHAR(18) NULL REFERENCES workflow_items(salesforce_record_id),
    sync_job_id UUID NULL REFERENCES sync_jobs(job_id),
    reason_code TEXT NULL CHECK (reason_code IS NULL OR reason_code IN (
        'source_data_mismatch', 'case_not_mapped_yet', 'mapping_not_approved',
        'manual_review_required', 'authentication_blocked',
        'duplicate_or_collision', 'verification_failed',
        'retryable_read_failure', 'uncertain_external_result'
    )),
    CHECK (workflow_record_id IS NOT NULL OR sync_job_id IS NOT NULL)
);

COMMIT;
