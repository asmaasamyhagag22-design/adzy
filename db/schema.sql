-- Baseera SCHEMA v1.4 — canonical PostgreSQL DDL
--
-- GENERATED from db/models.py. Do not edit by hand; regenerate instead.
-- Present so the schema can be reviewed, and executed against a real PostgreSQL, without
-- depending on any Python package being installed.
--
-- Requires PostgreSQL 13+ (gen_random_uuid() is in core from 13; no extension needed).

CREATE TABLE users (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	email TEXT NOT NULL, 
	hashed_password TEXT NOT NULL, 
	full_name TEXT, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	legacy_user_id TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_users_email UNIQUE (email), 
	CONSTRAINT uq_users_legacy_user_id UNIQUE (legacy_user_id), 
	CONSTRAINT ck_users_legacy_user_id_is_16_hex CHECK (legacy_user_id IS NULL OR legacy_user_id ~ '^[0-9a-f]{16}$'), 
	CONSTRAINT ck_users_email_is_normalised CHECK (email = btrim(lower(email))), 
	CONSTRAINT ck_users_email_not_blank CHECK (length(email) > 0)
);

CREATE TABLE gallery_blobs (
	checksum TEXT NOT NULL, 
	byte_size BIGINT NOT NULL, 
	mime TEXT DEFAULT 'image/png' NOT NULL, 
	storage_key TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_gallery_blobs PRIMARY KEY (checksum), 
	CONSTRAINT ck_gallery_blobs_blob_has_bytes CHECK (byte_size > 0), 
	CONSTRAINT ck_gallery_blobs_checksum_is_sha256_hex CHECK (length(checksum) = 64)
);

CREATE TABLE brands (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	domain_key TEXT NOT NULL, 
	owner_id UUID, 
	current_profile_id UUID, 
	display_name TEXT, 
	source_url TEXT, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_brands PRIMARY KEY (id), 
	CONSTRAINT uq_brands_domain_key UNIQUE (domain_key), 
	CONSTRAINT uq_brands_id_domain_key UNIQUE (id, domain_key), 
	CONSTRAINT fk_brands_owner_id FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_brands_owner_id ON brands (owner_id);

CREATE INDEX ix_brands_live_updated ON brands (updated_at) WHERE deleted_at IS NULL;

CREATE TABLE profile_snapshots (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	profile JSONB NOT NULL, 
	schema_version INTEGER NOT NULL, 
	country_canonical TEXT, 
	country_source TEXT, 
	country_raw TEXT, 
	primary_language VARCHAR(8), 
	language_source TEXT, 
	origin TEXT DEFAULT 'live' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_profile_snapshots PRIMARY KEY (id), 
	CONSTRAINT uq_profile_snapshots_brand_id_version UNIQUE (brand_id, version), 
	CONSTRAINT uq_profile_snapshots_id_brand_id UNIQUE (id, brand_id), 
	CONSTRAINT ck_profile_snapshots_version_is_positive CHECK (version > 0), 
	CONSTRAINT ck_profile_snapshots_origin_is_known CHECK (origin IN ('live','legacy_import')), 
	CONSTRAINT ck_profile_snapshots_country_source_is_known CHECK (country_source IS NULL OR country_source IN ('explicit_country','location_country','website_cctld','service_area','phone_prefix','unknown')), 
	CONSTRAINT ck_profile_snapshots_live_rows_know_their_creation_time CHECK (origin = 'legacy_import' OR created_at IS NOT NULL), 
	CONSTRAINT fk_profile_snapshots_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT
);

CREATE INDEX ix_profile_snapshots_brand_version ON profile_snapshots (brand_id, version);

CREATE TABLE assets (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	storage_key TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	mime TEXT NOT NULL, 
	bytes BIGINT, 
	checksum_sha256 VARCHAR(64), 
	width INTEGER, 
	height INTEGER, 
	created_by_user_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_assets PRIMARY KEY (id), 
	CONSTRAINT uq_assets_storage_key UNIQUE (storage_key), 
	CONSTRAINT uq_assets_id_brand_id UNIQUE (id, brand_id), 
	CONSTRAINT ck_assets_bytes_is_not_negative CHECK (bytes IS NULL OR bytes >= 0), 
	CONSTRAINT fk_assets_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_assets_created_by_user_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_assets_brand_created ON assets (brand_id, created_at);

CREATE TABLE brand_owner_inputs (
	brand_id UUID NOT NULL, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	schema_version INTEGER DEFAULT 1 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_brand_owner_inputs PRIMARY KEY (brand_id), 
	CONSTRAINT fk_brand_owner_inputs_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE
);

CREATE TABLE brand_competitors (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	key TEXT NOT NULL, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_brand_competitors PRIMARY KEY (id), 
	CONSTRAINT uq_brand_competitors_brand_id_key UNIQUE (brand_id, key), 
	CONSTRAINT fk_brand_competitors_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE
);

CREATE TABLE gallery_entries (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	owner_user_id UUID, 
	brand_id UUID, 
	checksum TEXT NOT NULL, 
	filename TEXT NOT NULL, 
	domain_key TEXT, 
	brand_name TEXT, 
	content_type TEXT, 
	style TEXT, 
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL, 
	origin TEXT DEFAULT 'live' NOT NULL, 
	legacy_seq INTEGER, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_gallery_entries PRIMARY KEY (id), 
	CONSTRAINT ck_gallery_entries_gallery_origin_is_known CHECK (origin IN ('live','legacy_import')), 
	CONSTRAINT ck_gallery_entries_legacy_gallery_imports_are_positioned CHECK (origin <> 'legacy_import' OR (legacy_seq IS NOT NULL AND imported_at IS NOT NULL)), 
	CONSTRAINT ck_gallery_entries_live_gallery_entries_are_not_imports CHECK (origin <> 'live' OR (legacy_seq IS NULL AND imported_at IS NULL)), 
	CONSTRAINT fk_gallery_entries_owner_user_id FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_gallery_entries_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_gallery_entries_checksum FOREIGN KEY(checksum) REFERENCES gallery_blobs (checksum) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_gallery_entries_legacy_seq ON gallery_entries (legacy_seq) WHERE origin = 'legacy_import';

CREATE INDEX ix_gallery_entries_owner_created ON gallery_entries (owner_user_id, created_at);

CREATE TABLE runs (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	kind TEXT NOT NULL, 
	status TEXT DEFAULT 'queued' NOT NULL, 
	input_profile_id UUID, 
	calendar_post_id UUID, 
	selected_attempt_id UUID, 
	requested_config JSONB DEFAULT '{}'::jsonb NOT NULL, 
	effective_config JSONB, 
	config_schema_version INTEGER DEFAULT 1 NOT NULL, 
	output JSONB, 
	output_schema_version INTEGER, 
	error TEXT, 
	kind_seq INTEGER, 
	origin TEXT DEFAULT 'live' NOT NULL, 
	lineage_complete BOOLEAN DEFAULT true NOT NULL, 
	legacy_source_key TEXT, 
	created_by_user_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_runs PRIMARY KEY (id), 
	CONSTRAINT uq_runs_id_brand_id UNIQUE (id, brand_id), 
	CONSTRAINT uq_runs_id_brand_id_kind UNIQUE (id, brand_id, kind), 
	CONSTRAINT fk_runs_input_profile FOREIGN KEY(input_profile_id, brand_id) REFERENCES profile_snapshots (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_runs_kind_is_known CHECK (kind IN ('scrape', 'trends', 'swot', 'strategy', 'calendar', 'competitors', 'poster', 'social', 'reel', 'influencer_poster')), 
	CONSTRAINT ck_runs_status_is_known CHECK (status IN ('queued', 'running', 'succeeded', 'failed')), 
	CONSTRAINT ck_runs_only_creative_runs_target_a_post CHECK (calendar_post_id IS NULL OR kind IN ('poster', 'social', 'reel', 'influencer_poster')), 
	CONSTRAINT ck_runs_origin_is_known CHECK (origin IN ('live','adopted','legacy_import')), 
	CONSTRAINT ck_runs_live_lineage_is_complete CHECK (origin <> 'live' OR lineage_complete), 
	CONSTRAINT ck_runs_live_runs_know_their_creator CHECK (origin <> 'live' OR created_by_user_id IS NOT NULL), 
	CONSTRAINT ck_runs_live_rows_know_their_creation_time CHECK (origin = 'legacy_import' OR created_at IS NOT NULL), 
	CONSTRAINT ck_runs_runtime_runs_allocate_a_sequence CHECK (origin = 'legacy_import' OR kind_seq IS NOT NULL), 
	CONSTRAINT ck_runs_kind_seq_starts_at_one CHECK (kind_seq IS NULL OR kind_seq >= 1), 
	CONSTRAINT uq_runs_brand_id_kind_kind_seq UNIQUE (brand_id, kind, kind_seq), 
	CONSTRAINT fk_runs_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_runs_created_by_user_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_runs_calendar_post_id ON runs (calendar_post_id);

CREATE INDEX ix_runs_current_by_kind ON runs (brand_id, kind, kind_seq);

CREATE INDEX ix_runs_open ON runs (status) WHERE status IN ('queued','running');

CREATE INDEX ix_runs_brand_kind_created ON runs (brand_id, kind, created_at);

CREATE TABLE run_dependencies (
	run_id UUID NOT NULL, 
	depends_on_run_id UUID NOT NULL, 
	brand_id UUID NOT NULL, 
	relation TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_run_dependencies PRIMARY KEY (run_id, depends_on_run_id), 
	CONSTRAINT fk_run_dependencies_run FOREIGN KEY(run_id, brand_id) REFERENCES runs (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT fk_run_dependencies_depends_on FOREIGN KEY(depends_on_run_id, brand_id) REFERENCES runs (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_run_dependencies_no_self_dependency CHECK (run_id <> depends_on_run_id), 
	CONSTRAINT ck_run_dependencies_relation_is_known CHECK (relation IN ('uses_trends','uses_swot','uses_strategy'))
);

CREATE INDEX ix_run_dependencies_depends_on_run_id ON run_dependencies (depends_on_run_id);

CREATE TABLE calendar_posts (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	calendar_run_id UUID NOT NULL, 
	brand_id UUID NOT NULL, 
	calendar_run_kind TEXT DEFAULT 'calendar' NOT NULL, 
	ordinal INTEGER NOT NULL, 
	scheduled_date DATE, 
	channel TEXT, 
	content_type TEXT, 
	funnel_stage TEXT, 
	topic TEXT, 
	hook TEXT, 
	cta TEXT, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	schema_version INTEGER DEFAULT 1 NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_calendar_posts PRIMARY KEY (id), 
	CONSTRAINT uq_calendar_posts_calendar_run_id_ordinal UNIQUE (calendar_run_id, ordinal), 
	CONSTRAINT uq_calendar_posts_id_brand_id UNIQUE (id, brand_id), 
	CONSTRAINT fk_calendar_posts_calendar_run FOREIGN KEY(calendar_run_id, brand_id, calendar_run_kind) REFERENCES runs (id, brand_id, kind) ON DELETE RESTRICT, 
	CONSTRAINT ck_calendar_posts_parent_is_a_calendar_run CHECK (calendar_run_kind = 'calendar'), 
	CONSTRAINT ck_calendar_posts_ordinal_is_not_negative CHECK (ordinal >= 0), 
	CONSTRAINT ck_calendar_posts_funnel_stage_is_known CHECK (funnel_stage IS NULL OR funnel_stage IN ('tofu','mofu','bofu'))
);

CREATE INDEX ix_calendar_posts_brand_scheduled_date ON calendar_posts (brand_id, scheduled_date);

CREATE TABLE attempts (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	run_id UUID NOT NULL, 
	brand_id UUID NOT NULL, 
	ordinal INTEGER NOT NULL, 
	status TEXT DEFAULT 'running' NOT NULL, 
	asset_id UUID, 
	qa JSONB DEFAULT '{}'::jsonb NOT NULL, 
	qa_passed BOOLEAN, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_attempts PRIMARY KEY (id), 
	CONSTRAINT uq_attempts_id_run_id UNIQUE (id, run_id), 
	CONSTRAINT uq_attempts_id_brand_id UNIQUE (id, brand_id), 
	CONSTRAINT uq_attempts_run_id_ordinal UNIQUE (run_id, ordinal), 
	CONSTRAINT fk_attempts_run FOREIGN KEY(run_id, brand_id) REFERENCES runs (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT fk_attempts_asset FOREIGN KEY(asset_id, brand_id) REFERENCES assets (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_attempts_status_is_known CHECK (status IN ('running','succeeded','failed')), 
	CONSTRAINT ck_attempts_succeeded_attempts_have_an_asset CHECK (status <> 'succeeded' OR asset_id IS NOT NULL), 
	CONSTRAINT ck_attempts_ordinal_is_not_negative CHECK (ordinal >= 0)
);

CREATE INDEX ix_attempts_run_id_ordinal ON attempts (run_id, ordinal);

CREATE TABLE jobs (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	run_id UUID, 
	brand_id UUID, 
	requested_domain_key TEXT, 
	status TEXT DEFAULT 'queued' NOT NULL, 
	execution_no INTEGER DEFAULT 1 NOT NULL, 
	access_mode TEXT NOT NULL, 
	owner_user_id UUID, 
	capability_digest VARCHAR(64), 
	current_stage TEXT, 
	error TEXT, 
	warning TEXT, 
	claimed_by TEXT, 
	claimed_at TIMESTAMP WITH TIME ZONE, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_jobs_run_id_execution_no UNIQUE (run_id, execution_no), 
	CONSTRAINT ck_jobs_status_is_known CHECK (status IN ('queued','running','succeeded','failed')), 
	CONSTRAINT ck_jobs_access_mode_is_known CHECK (access_mode IN ('user','capability')), 
	CONSTRAINT ck_jobs_exactly_one_principal CHECK ((access_mode = 'user' AND owner_user_id IS NOT NULL AND capability_digest IS NULL) OR (access_mode = 'capability' AND capability_digest IS NOT NULL     AND owner_user_id IS NULL)), 
	CONSTRAINT ck_jobs_running_jobs_name_their_claimant CHECK (status <> 'running' OR claimed_by IS NOT NULL), 
	CONSTRAINT ck_jobs_execution_no_starts_at_one CHECK (execution_no >= 1), 
	CONSTRAINT fk_jobs_run_id FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_jobs_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_jobs_owner_user_id FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_jobs_expired_leases ON jobs (lease_expires_at) WHERE status = 'running';

CREATE INDEX ix_jobs_owner_user_id ON jobs (owner_user_id);

CREATE INDEX ix_jobs_open ON jobs (status) WHERE status IN ('queued','running');

CREATE INDEX ix_jobs_requested_domain_key ON jobs (requested_domain_key);

CREATE TABLE approvals (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	subject_run_id UUID NOT NULL, 
	subject_kind TEXT DEFAULT 'swot' NOT NULL, 
	decision TEXT NOT NULL, 
	decided_by_user_id UUID, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	origin TEXT DEFAULT 'live' NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	note TEXT, 
	CONSTRAINT pk_approvals PRIMARY KEY (id), 
	CONSTRAINT fk_approvals_subject_run FOREIGN KEY(subject_run_id, brand_id, subject_kind) REFERENCES runs (id, brand_id, kind) ON DELETE RESTRICT, 
	CONSTRAINT ck_approvals_subject_kind_is_known CHECK (subject_kind IN ('swot','strategy')), 
	CONSTRAINT ck_approvals_decision_is_known CHECK (decision IN ('approved','rejected')), 
	CONSTRAINT ck_approvals_origin_is_known CHECK (origin IN ('live','legacy_import')), 
	CONSTRAINT ck_approvals_live_approvals_name_actor_and_time CHECK (origin <> 'live' OR (decided_at IS NOT NULL AND decided_by_user_id IS NOT NULL AND imported_at IS NULL)), 
	CONSTRAINT ck_approvals_legacy_imports_have_no_fabricated_metadata CHECK (origin <> 'legacy_import' OR (decided_by_user_id IS NULL AND decided_at IS NULL AND imported_at IS NOT NULL)), 
	CONSTRAINT uq_approvals_subject_run_id_decided_at UNIQUE (subject_run_id, decided_at), 
	CONSTRAINT fk_approvals_decided_by_user_id FOREIGN KEY(decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX uq_approvals_one_import_per_run ON approvals (subject_run_id) WHERE origin = 'legacy_import';

CREATE INDEX ix_approvals_subject_run_id ON approvals (subject_run_id);

CREATE TABLE usage_records (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	run_id UUID NOT NULL, 
	brand_id UUID NOT NULL, 
	attempt_id UUID, 
	provider TEXT NOT NULL, 
	model TEXT NOT NULL, 
	input_tokens INTEGER DEFAULT 0 NOT NULL, 
	output_tokens INTEGER DEFAULT 0 NOT NULL, 
	thoughts_tokens INTEGER DEFAULT 0 NOT NULL, 
	cost_usd NUMERIC(12, 6) DEFAULT 0 NOT NULL, 
	duration_ms INTEGER, 
	stage TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_usage_records PRIMARY KEY (id), 
	CONSTRAINT fk_usage_records_run FOREIGN KEY(run_id, brand_id) REFERENCES runs (id, brand_id) ON DELETE RESTRICT, 
	CONSTRAINT fk_usage_records_attempt FOREIGN KEY(attempt_id, run_id) REFERENCES attempts (id, run_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_usage_records_cost_is_not_negative CHECK (cost_usd >= 0)
);

CREATE INDEX ix_usage_records_brand_created ON usage_records (brand_id, created_at);

CREATE INDEX ix_usage_records_run_id ON usage_records (run_id);

CREATE TABLE job_events (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	job_id UUID NOT NULL, 
	seq INTEGER NOT NULL, 
	stage TEXT NOT NULL, 
	status TEXT NOT NULL, 
	is_terminal BOOLEAN DEFAULT false NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	duration_ms INTEGER DEFAULT 0 NOT NULL, 
	error TEXT, 
	payload JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_job_events PRIMARY KEY (id), 
	CONSTRAINT uq_job_events_job_id_seq UNIQUE (job_id, seq), 
	CONSTRAINT ck_job_events_seq_starts_at_one CHECK (seq >= 1), 
	CONSTRAINT ck_job_events_duration_is_not_negative CHECK (duration_ms >= 0), 
	CONSTRAINT fk_job_events_job_id FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE INDEX ix_job_events_job_id_seq ON job_events (job_id, seq);

CREATE UNIQUE INDEX uq_job_events_one_terminal ON job_events (job_id) WHERE is_terminal;

CREATE TABLE job_results (
	job_id UUID NOT NULL, 
	result_kind TEXT NOT NULL, 
	schema_version INTEGER DEFAULT 1 NOT NULL, 
	payload JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_job_results PRIMARY KEY (job_id), 
	CONSTRAINT ck_job_results_schema_version_starts_at_one CHECK (schema_version >= 1), 
	CONSTRAINT ck_job_results_result_kind_is_not_blank CHECK (length(btrim(result_kind)) > 0), 
	CONSTRAINT fk_job_results_job_id FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
);

ALTER TABLE runs ADD CONSTRAINT fk_runs_calendar_post FOREIGN KEY(calendar_post_id, brand_id) REFERENCES calendar_posts (id, brand_id) ON DELETE RESTRICT;

ALTER TABLE runs ADD CONSTRAINT fk_runs_selected_attempt FOREIGN KEY(selected_attempt_id, id) REFERENCES attempts (id, run_id) ON DELETE RESTRICT;

ALTER TABLE brands ADD CONSTRAINT fk_brands_current_profile FOREIGN KEY(current_profile_id, id) REFERENCES profile_snapshots (id, brand_id) ON DELETE RESTRICT;
