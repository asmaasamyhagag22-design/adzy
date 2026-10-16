# -*- coding: utf-8 -*-
"""Baseera schema v1.2 — initial schema.

Schema only. Creates tables, constraints and indexes and touches NO data: it does not read
users.json, it does not read the brand JSON documents, and it loads nothing from
`database_export/`. Legacy migration is a separate, explicit phase.

THIS REVISION IS FROZEN, AND THAT IS THE POINT.

An earlier draft called `Base.metadata.create_all(op.get_bind())`. That is wrong, and the reasoning
behind it — "driving the metadata means the migration cannot drift from the models" — had the
invariant backwards. A revision is a HISTORICAL RECORD of what was applied, not a description of
what the models currently say. Under the dynamic version, adding a column to `db/models.py`
tomorrow would silently change what revision 0001 builds on a FRESH database, while every database
that already ran 0001 would never receive it. The two would diverge permanently, and nothing would
report it.

So the statements below are literal text. They import nothing from `db.models` and cannot be
changed by editing it. Future schema changes get their own revisions, which is how Alembic is meant
to work.

The drift the earlier draft feared is handled where it belongs — by a TEST
(`tests/test_db_migration_parity.py`), which compiles the current models and compares them against
this frozen SQL. Migration and models must agree TODAY; the migration stays frozen for TOMORROW.

Generated once from the approved metadata, then verified by that parity test. Requires PostgreSQL
13+ (`gen_random_uuid()` is in core from 13; no extension needed).

Revision ID: 0001_initial_schema
Revises:
"""
from __future__ import annotations

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


# --- frozen: literal DDL, exactly as approved in DB Phase 0D/1A ------------------------
UPGRADE_SQL: tuple[str, ...] = (
    """CREATE TABLE users (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	email TEXT NOT NULL, 
	hashed_password TEXT NOT NULL, 
	full_name TEXT, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_users_email UNIQUE (email), 
	CONSTRAINT ck_users_email_is_normalised CHECK (email = btrim(lower(email))), 
	CONSTRAINT ck_users_email_not_blank CHECK (length(email) > 0)
)""",
    """CREATE TABLE brands (
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
)""",
    """CREATE INDEX ix_brands_live_updated ON brands (updated_at) WHERE deleted_at IS NULL""",
    """CREATE INDEX ix_brands_owner_id ON brands (owner_id)""",
    """CREATE TABLE profile_snapshots (
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
)""",
    """CREATE INDEX ix_profile_snapshots_brand_version ON profile_snapshots (brand_id, version)""",
    """CREATE TABLE assets (
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
)""",
    """CREATE INDEX ix_assets_brand_created ON assets (brand_id, created_at)""",
    """CREATE TABLE brand_owner_inputs (
	brand_id UUID NOT NULL, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	schema_version INTEGER DEFAULT 1 NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_brand_owner_inputs PRIMARY KEY (brand_id), 
	CONSTRAINT fk_brand_owner_inputs_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE
)""",
    """CREATE TABLE brand_competitors (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	key TEXT NOT NULL, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_brand_competitors PRIMARY KEY (id), 
	CONSTRAINT uq_brand_competitors_brand_id_key UNIQUE (brand_id, key), 
	CONSTRAINT fk_brand_competitors_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE
)""",
    """CREATE TABLE runs (
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
	CONSTRAINT fk_runs_brand_id FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_runs_created_by_user_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_runs_calendar_post_id ON runs (calendar_post_id)""",
    """CREATE INDEX ix_runs_open ON runs (status) WHERE status IN ('queued','running')""",
    """CREATE INDEX ix_runs_brand_kind_created ON runs (brand_id, kind, created_at)""",
    """CREATE TABLE run_dependencies (
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
)""",
    """CREATE INDEX ix_run_dependencies_depends_on_run_id ON run_dependencies (depends_on_run_id)""",
    """CREATE TABLE calendar_posts (
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
)""",
    """CREATE INDEX ix_calendar_posts_brand_scheduled_date ON calendar_posts (brand_id, scheduled_date)""",
    """CREATE TABLE attempts (
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
)""",
    """CREATE INDEX ix_attempts_run_id_ordinal ON attempts (run_id, ordinal)""",
    """CREATE TABLE jobs (
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
)""",
    """CREATE INDEX ix_jobs_requested_domain_key ON jobs (requested_domain_key)""",
    """CREATE INDEX ix_jobs_open ON jobs (status) WHERE status IN ('queued','running')""",
    """CREATE INDEX ix_jobs_expired_leases ON jobs (lease_expires_at) WHERE status = 'running'""",
    """CREATE INDEX ix_jobs_owner_user_id ON jobs (owner_user_id)""",
    """CREATE TABLE approvals (
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	brand_id UUID NOT NULL, 
	subject_run_id UUID NOT NULL, 
	subject_kind TEXT DEFAULT 'swot' NOT NULL, 
	decision TEXT NOT NULL, 
	decided_by_user_id UUID, 
	decided_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	note TEXT, 
	CONSTRAINT pk_approvals PRIMARY KEY (id), 
	CONSTRAINT fk_approvals_subject_run FOREIGN KEY(subject_run_id, brand_id, subject_kind) REFERENCES runs (id, brand_id, kind) ON DELETE RESTRICT, 
	CONSTRAINT ck_approvals_subject_kind_is_known CHECK (subject_kind IN ('swot')), 
	CONSTRAINT ck_approvals_decision_is_known CHECK (decision IN ('approved','rejected')), 
	CONSTRAINT uq_approvals_subject_run_id_decided_at UNIQUE (subject_run_id, decided_at), 
	CONSTRAINT fk_approvals_decided_by_user_id FOREIGN KEY(decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_approvals_subject_run_id ON approvals (subject_run_id)""",
    """CREATE TABLE usage_records (
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
)""",
    """CREATE INDEX ix_usage_records_brand_created ON usage_records (brand_id, created_at)""",
    """CREATE INDEX ix_usage_records_run_id ON usage_records (run_id)""",
    """ALTER TABLE brands ADD CONSTRAINT fk_brands_current_profile FOREIGN KEY(current_profile_id, id) REFERENCES profile_snapshots (id, brand_id) ON DELETE RESTRICT""",
    """ALTER TABLE runs ADD CONSTRAINT fk_runs_calendar_post FOREIGN KEY(calendar_post_id, brand_id) REFERENCES calendar_posts (id, brand_id) ON DELETE RESTRICT""",
    """ALTER TABLE runs ADD CONSTRAINT fk_runs_selected_attempt FOREIGN KEY(selected_attempt_id, id) REFERENCES attempts (id, run_id) ON DELETE RESTRICT""",
)

# Drops ONLY what this revision created, in reverse dependency order, with no CASCADE — so it
# cannot reach an object some later revision added. No legacy file is touched and no external asset
# is removed: the filesystem media this schema only REFERENCES is never in scope for a schema
# downgrade.
DOWNGRADE_SQL: tuple[str, ...] = (
    """ALTER TABLE brands DROP CONSTRAINT fk_brands_current_profile""",
    """ALTER TABLE runs DROP CONSTRAINT fk_runs_calendar_post""",
    """ALTER TABLE runs DROP CONSTRAINT fk_runs_selected_attempt""",
    """DROP TABLE usage_records""",
    """DROP TABLE run_dependencies""",
    """DROP TABLE jobs""",
    """DROP TABLE calendar_posts""",
    """DROP TABLE attempts""",
    """DROP TABLE approvals""",
    """DROP TABLE runs""",
    """DROP TABLE profile_snapshots""",
    """DROP TABLE brand_owner_inputs""",
    """DROP TABLE brand_competitors""",
    """DROP TABLE assets""",
    """DROP TABLE brands""",
    """DROP TABLE users""",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
