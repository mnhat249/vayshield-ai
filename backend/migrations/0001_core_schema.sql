-- ============================================================================
-- VAYSHIELD AI - 0001 CORE SCHEMA (PostgreSQL 16 + pgvector + pg_trgm)
-- Nguon: File 4 §2.2. Idempotent: chay nhieu lan khong loi. Chay bang role ADMIN.
-- Luat 91/2025/QH15: khong luu PII tho (SDT/STK chi o dang HMAC blind index / envelope).
-- ============================================================================
BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- ---------------------------------------------------------------------------
-- Ham tien ich
-- ---------------------------------------------------------------------------
-- UUIDv7 (48-bit unix ms + ngau nhien); PG16 chua co uuidv7() native.
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid
LANGUAGE sql VOLATILE
SET search_path = pg_catalog, public
AS $$
    SELECT encode(
        set_bit(
            set_bit(
                overlay(
                    uuid_send(gen_random_uuid())
                    PLACING substring(int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
                    FROM 1 FOR 6
                ),
                52, 1
            ),
            53, 1
        ),
        'hex'
    )::uuid
$$;

-- Do sau long nhau toi da cua JSONB (container goc = 1, scalar = 0). EC-DB18.
CREATE OR REPLACE FUNCTION jsonb_max_depth(j jsonb) RETURNS integer
LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, public
AS $$
DECLARE
    child jsonb;
    best integer := 0;
    d integer;
BEGIN
    IF jsonb_typeof(j) = 'object' THEN
        FOR child IN SELECT value FROM jsonb_each(j) LOOP
            d := jsonb_max_depth(child);
            IF d > best THEN best := d; END IF;
        END LOOP;
        RETURN 1 + best;
    ELSIF jsonb_typeof(j) = 'array' THEN
        FOR child IN SELECT value FROM jsonb_array_elements(j) LOOP
            d := jsonb_max_depth(child);
            IF d > best THEN best := d; END IF;
        END LOOP;
        RETURN 1 + best;
    END IF;
    RETURN 0;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. licensed_lender_registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS licensed_lender_registry (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    entity_code         VARCHAR(64) UNIQUE NOT NULL,
    legal_category      VARCHAR(32) NOT NULL CHECK (legal_category IN (
        'COMMERCIAL_BANK', 'LICENSED_FINCO', 'P2P_SANDBOX', 'LICENSED_PAWN_CHAIN', 'BNPL_WALLET_PROVIDER'
    )),
    official_short_name VARCHAR(120) NOT NULL,
    official_legal_name VARCHAR(255) NOT NULL,
    normalized_name     VARCHAR(255) NOT NULL,
    sbv_license_no      VARCHAR(100),
    official_domains    TEXT[] NOT NULL DEFAULT '{}',
    official_hotlines   TEXT[] NOT NULL DEFAULT '{}',
    android_packages    JSONB NOT NULL DEFAULT '[]'::jsonb,
    sandbox_cap_vnd     BIGINT,
    effective_from      DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to        DATE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lender_trgm_name
    ON licensed_lender_registry USING GIN (normalized_name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 2. anonymous_devices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS anonymous_devices (
    id                  UUID PRIMARY KEY,
    pubkey_ecdsa_p256   BYTEA,
    phone_blind_hmac    VARCHAR(64) UNIQUE,
    pepper_version      SMALLINT NOT NULL DEFAULT 1,
    phone_optin_exp_at  TIMESTAMPTZ,
    platform_type       VARCHAR(24) NOT NULL CHECK (platform_type IN ('ANDROID_NATIVE', 'WEB_PWA', 'JUDGE_SANDBOX')),
    daily_wage_vnd      INTEGER NOT NULL DEFAULT 320000 CHECK (daily_wage_vnd BETWEEN 50000 AND 10000000),
    policy_version      INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 3. family_guardian_links
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS family_guardian_links (
    id                          UUID PRIMARY KEY,
    borrower_device_id          UUID NOT NULL REFERENCES anonymous_devices(id) ON DELETE CASCADE,
    guardian_alias              VARCHAR(64) NOT NULL,
    pairing_code_hash           VARCHAR(64),
    pairing_expires_at          TIMESTAMPTZ,
    webpush_subscription_cipher BYTEA,
    fallback_contact_cipher     BYTEA,
    status                      VARCHAR(24) NOT NULL CHECK (status IN (
        'PENDING_CONFIRM', 'ACTIVE', 'REVOKED_BY_BORROWER', 'REVOKED_BY_GUARDIAN', 'PUSH_EXPIRED_410'
    )),
    consented_at                TIMESTAMPTZ,
    revoked_at                  TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_family_active_borrower
    ON family_guardian_links(borrower_device_id) WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- 4. protection_sessions  (sandbox_namespace NOT NULL DEFAULT 'live')
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS protection_sessions (
    id                  UUID PRIMARY KEY,
    device_id           UUID NOT NULL REFERENCES anonymous_devices(id) ON DELETE CASCADE,
    fiber_key           VARCHAR(80) NOT NULL DEFAULT 'default',
    sandbox_namespace   VARCHAR(64) NOT NULL DEFAULT 'live',
    current_hsm_state   VARCHAR(40) NOT NULL CHECK (current_hsm_state IN (
        'S0_IDLE', 'S1_LOAN_EXPLORATION', 'S2_CONTRACT_AUDITING',
        'S3_ADVANCE_FEE_HARD_STOP', 'S4_SHIELD_ARMED_24H',
        'S5_CRITICAL_INTERCEPTION', 'S6_COOLING_OFF_LOCK',
        'S7_ESCALATED_SCAM_CONFIRMED', 'S8_SAFE_ROUTINE_TX', 'S9_GOLDEN_HOUR_TRIAGE'
    )),
    risk_log_odds       DOUBLE PRECISION NOT NULL DEFAULT -3.5,
    trigger_origin      VARCHAR(32) NOT NULL CHECK (trigger_origin IN (
        'STATION_1_APK', 'STATION_2_SCAN', 'STATION_2_VOICE', 'STATION_3_NOTIF',
        'B2B_PARTNER_WEBHOOK', 'SANDBOX_SIM'
    )),
    amount_bucket_tier  VARCHAR(24),
    smurf_open_count    SMALLINT NOT NULL DEFAULT 0,
    disbursement_at     TIMESTAMPTZ,
    shield_expires_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_active_shield
    ON protection_sessions(device_id, shield_expires_at)
    WHERE current_hsm_state IN (
        'S3_ADVANCE_FEE_HARD_STOP', 'S4_SHIELD_ARMED_24H', 'S6_COOLING_OFF_LOCK',
        'S7_ESCALATED_SCAM_CONFIRMED', 'S9_GOLDEN_HOUR_TRIAGE'
    );
CREATE INDEX IF NOT EXISTS idx_sessions_sandbox_ns ON protection_sessions(sandbox_namespace);

-- ---------------------------------------------------------------------------
-- 5. contract_audits  (CHECK kich thuoc < 64KB, do sau <= 5: EC-DB18)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contract_audits (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES protection_sessions(id) ON DELETE CASCADE,
    image_count             SMALLINT NOT NULL CHECK (image_count BETWEEN 0 AND 4),
    verdict_code            VARCHAR(40) NOT NULL CHECK (verdict_code IN (
        'V_RED_ADVANCE_FEE', 'V_RED_BRAND_SPOOF', 'V_RED_BNPL_OR_STRAW_SCAM', 'V_RED_CRIMINAL_USURY',
        'V_ORANGE_CIVIL_CAP_EXCEEDED', 'V_ORANGE_PAWN_HIGH_FEE', 'V_YELLOW_HIGH_COST_LICENSED',
        'V_AMBER_INCOMPLETE_DOC', 'V_GREEN_TRANSPARENT'
    )),
    lender_category_resolved VARCHAR(32) NOT NULL,
    claimed_brand_normalized VARCHAR(255),
    matched_lender_id        UUID REFERENCES licensed_lender_registry(id),
    nominal_principal_vnd    BIGINT CHECK (nominal_principal_vnd > 0),
    net_disbursed_vnd        BIGINT CHECK (net_disbursed_vnd >= 0),
    total_repayment_vnd      BIGINT CHECK (total_repayment_vnd > 0),
    tenure_days              INTEGER CHECK (tenure_days > 0),
    apr_vn_365               DOUBLE PRECISION,
    apr_min_bound            DOUBLE PRECISION,
    apr_max_bound            DOUBLE PRECISION,
    ear_xirr                 DOUBLE PRECISION,
    lost_workdays            DOUBLE PRECISION,
    upfront_fee_detected     BOOLEAN NOT NULL DEFAULT FALSE,
    brand_spoof_detected     BOOLEAN NOT NULL DEFAULT FALSE,
    vietqr_extracted         BOOLEAN NOT NULL DEFAULT FALSE,
    user_edited_numbers      BOOLEAN NOT NULL DEFAULT FALSE,
    masked_extraction_json   JSONB NOT NULL,
    cited_legal_clauses      TEXT[] NOT NULL DEFAULT '{}',
    processing_latency_ms    INTEGER NOT NULL,
    model_tier_used          VARCHAR(32) NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_masked_extraction_size
        CHECK (octet_length(masked_extraction_json::text) < 65536),
    CONSTRAINT ck_masked_extraction_depth
        CHECK (jsonb_max_depth(masked_extraction_json) <= 5)
);
CREATE INDEX IF NOT EXISTS idx_audits_session ON contract_audits(session_id);

-- ---------------------------------------------------------------------------
-- 6. interception_events  (EC-DB26: khong control char ngoai \n \t)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interception_events (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES protection_sessions(id) ON DELETE CASCADE,
    audio_call_state        VARCHAR(32) NOT NULL CHECK (audio_call_state IN (
        'ACTIVE_CELLULAR', 'ACTIVE_VOIP', 'ENDED_WITHIN_180S', 'NONE'
    )),
    screen_share_detected   BOOLEAN NOT NULL DEFAULT FALSE,
    computed_scam_prob      DOUBLE PRECISION NOT NULL CHECK (computed_scam_prob BETWEEN 0.0 AND 1.0),
    detected_ontology_axes  TEXT[] NOT NULL DEFAULT '{}',
    masked_user_utterance   TEXT CHECK (masked_user_utterance !~ '[\x01-\x08\x0B-\x1F]'),
    cooling_off_seconds     SMALLINT NOT NULL DEFAULT 15,
    outcome_action          VARCHAR(32) NOT NULL CHECK (outcome_action IN (
        'USER_ABORTED_TRANSFER', 'ESCALATED_TO_FAMILY', 'FAMILY_RESCUE_CARD_DELIVERED',
        'GOLDEN_HOUR_TRIAGE_ENTERED', 'VERIFIED_ROUTINE_SAFE', 'OFFLINE_QUEUED_SYNCED'
    )),
    family_ack_latency_ms   INTEGER,
    rescue_card_received_at TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intercept_session ON interception_events(session_id);

-- ---------------------------------------------------------------------------
-- 7. legal_provision_nodes / edges  (HNSW m=16, ef_construction=64: EC-DB17)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_provision_nodes (
    clause_code         VARCHAR(64) PRIMARY KEY,
    doc_type            VARCHAR(32) NOT NULL,
    doc_number          VARCHAR(64) NOT NULL,
    effective_date      DATE NOT NULL,
    is_currently_valid  BOOLEAN NOT NULL DEFAULT TRUE,
    title_vi            VARCHAR(255) NOT NULL,
    verbatim_content    TEXT NOT NULL,
    plain_summary_vi    TEXT NOT NULL,
    applicable_verdicts TEXT[] NOT NULL DEFAULT '{}',
    embedding_768       VECTOR(768) NOT NULL,
    search_tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', verbatim_content)) STORED
);
CREATE INDEX IF NOT EXISTS idx_legal_hnsw_vec
    ON legal_provision_nodes USING hnsw (embedding_768 vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_legal_gin_tsv ON legal_provision_nodes USING GIN (search_tsv);

CREATE TABLE IF NOT EXISTS legal_provision_edges (
    source_clause       VARCHAR(64) NOT NULL REFERENCES legal_provision_nodes(clause_code),
    target_clause       VARCHAR(64) NOT NULL REFERENCES legal_provision_nodes(clause_code),
    relation_type       VARCHAR(32) NOT NULL CHECK (relation_type IN (
        'EXEMPTS_FOR_CREDIT_INST', 'CRIMINALIZES_EXCESS_OF', 'AMENDS', 'GUIDES_IMPLEMENTATION'
    )),
    PRIMARY KEY (source_clause, target_clause, relation_type)
);

-- ---------------------------------------------------------------------------
-- 8. Do thi tai khoan moi
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fraud_campaign_clusters (
    id                      UUID PRIMARY KEY,
    campaign_label          VARCHAR(160) NOT NULL,
    impersonated_lender_id  UUID REFERENCES licensed_lender_registry(id),
    dominant_scam_trap      VARCHAR(32) NOT NULL,
    centroid_embedding      VECTOR(768),
    incident_count_24h      INTEGER NOT NULL DEFAULT 1,
    severity_level          VARCHAR(16) NOT NULL CHECK (severity_level IN ('WATCH', 'ELEVATED', 'CRITICAL_WAVE')),
    draft_advisory_text     TEXT,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mule_account_nodes (
    id                      UUID PRIMARY KEY,
    bank_bin                VARCHAR(12) NOT NULL,
    account_hmac_sha256     VARCHAR(64) UNIQUE NOT NULL,
    va_prefix_hmac          VARCHAR(64),
    pepper_version          SMALLINT NOT NULL DEFAULT 1,
    masked_account_display  VARCHAR(32) NOT NULL,
    account_holder_masked   VARCHAR(64),
    account_cipher_envelope BYTEA NOT NULL,
    distinct_device_quorum  INTEGER NOT NULL DEFAULT 1,
    threat_confidence       DOUBLE PRECISION NOT NULL DEFAULT 0.35,
    simo_dossier_status     VARCHAR(24) NOT NULL DEFAULT 'OBSERVING' CHECK (simo_dossier_status IN (
        'OBSERVING', 'QUORUM_REACHED', 'REVIEWED_CONFIRMED', 'EXPORTED_TO_SIMO', 'FALSE_POSITIVE_DISMISSED'
    )),
    cluster_id              UUID REFERENCES fraud_campaign_clusters(id),
    first_reported_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_reported_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mule_va_prefix
    ON mule_account_nodes(bank_bin, va_prefix_hmac) WHERE va_prefix_hmac IS NOT NULL;

CREATE TABLE IF NOT EXISTS mule_incident_edges (
    id                  UUID PRIMARY KEY,
    mule_node_id        UUID NOT NULL REFERENCES mule_account_nodes(id) ON DELETE CASCADE,
    reporter_device_id  UUID NOT NULL REFERENCES anonymous_devices(id),
    source_audit_id     UUID REFERENCES contract_audits(id),
    source_intercept_id UUID REFERENCES interception_events(id),
    evidence_channel    VARCHAR(32) NOT NULL CHECK (evidence_channel IN (
        'VIETQR_CRYPTO_DECODED', 'OCR_RECEIPT_EXTRACTED', 'VOICE_CHAT_REPORTED'
    )),
    scam_script_vec     VECTOR(768),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_one_vote_per_device_per_mule UNIQUE (mule_node_id, reporter_device_id)
);

-- ---------------------------------------------------------------------------
-- 9. B2B partners + shields (RLS co lap tenant: EC-DB15)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fintech_partners (
    id                  UUID PRIMARY KEY,
    partner_code        VARCHAR(64) UNIQUE NOT NULL,
    organization_name   VARCHAR(200) NOT NULL,
    lender_registry_id  UUID REFERENCES licensed_lender_registry(id),
    api_key_prefix      VARCHAR(16) UNIQUE NOT NULL,
    hmac_secret_cipher  BYTEA NOT NULL,
    rate_limit_rpm      INTEGER NOT NULL DEFAULT 600,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS b2b_disbursement_shields (
    id                      UUID PRIMARY KEY,
    partner_id              UUID NOT NULL REFERENCES fintech_partners(id),
    idempotency_key         VARCHAR(128) NOT NULL,
    borrower_phone_hmac     VARCHAR(64) NOT NULL,
    matched_session_id      UUID REFERENCES protection_sessions(id),
    amount_bucket_tier      VARCHAR(24) NOT NULL,
    shield_duration_hours   SMALLINT NOT NULL DEFAULT 24 CHECK (shield_duration_hours BETWEEN 1 AND 72),
    status                  VARCHAR(24) NOT NULL CHECK (status IN (
        'ARMED_ON_DEVICE', 'PENDING_USER_ONLINE', 'NO_DEVICE_OPTED_IN'
    )),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (partner_id, idempotency_key)
);

ALTER TABLE b2b_disbursement_shields ENABLE ROW LEVEL SECURITY;
ALTER TABLE b2b_disbursement_shields FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pol_b2b_tenant_isolation ON b2b_disbursement_shields;
CREATE POLICY pol_b2b_tenant_isolation ON b2b_disbursement_shields
    USING (partner_id = NULLIF(current_setting('app.current_partner_id', TRUE), '')::UUID)
    WITH CHECK (partner_id = NULLIF(current_setting('app.current_partner_id', TRUE), '')::UUID);

-- ---------------------------------------------------------------------------
-- 10. tamper_evident_audit_chain (append-only, hash-chain: EC-DB20)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tamper_evident_audit_chain (
    seq_no          BIGSERIAL PRIMARY KEY,
    actor_id        VARCHAR(120) NOT NULL,
    action_type     VARCHAR(64) NOT NULL,
    target_ref_id   UUID NOT NULL,
    prev_entry_hash VARCHAR(64) NOT NULL,
    entry_hash      VARCHAR(64) UNIQUE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION forbid_audit_chain_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_AUDIT_CHAIN_VIOLATION: Updates and deletes are strictly prohibited';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_immutable_audit_chain ON tamper_evident_audit_chain;
CREATE TRIGGER trg_immutable_audit_chain
    BEFORE UPDATE OR DELETE ON tamper_evident_audit_chain
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_chain_mutation();

DROP TRIGGER IF EXISTS trg_immutable_audit_chain_truncate ON tamper_evident_audit_chain;
CREATE TRIGGER trg_immutable_audit_chain_truncate
    BEFORE TRUNCATE ON tamper_evident_audit_chain
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_audit_chain_mutation();

-- Cong thuc bam (khop app/db_schema/hashchain.py):
--   sha256_hex( prev | actor | action | target | ts_utc_iso_micro )  (noi bang '|')
CREATE OR REPLACE FUNCTION audit_chain_entry_hash(
    p_prev text, p_actor text, p_action text, p_target uuid, p_created timestamptz
) RETURNS text
LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public
AS $$
    SELECT encode(digest(convert_to(
        p_prev || '|' || p_actor || '|' || p_action || '|' || p_target::text || '|' ||
        to_char(p_created AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'UTF8'), 'sha256'), 'hex')
$$;

-- Moi INSERT phai noi dung duoi chuoi (tuan tu hoa bang advisory lock) va entry_hash dung.
CREATE OR REPLACE FUNCTION audit_chain_enforce_link() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    tip text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('vayshield.tamper_evident_audit_chain'));
    SELECT entry_hash INTO tip FROM tamper_evident_audit_chain ORDER BY seq_no DESC LIMIT 1;
    IF NEW.prev_entry_hash IS DISTINCT FROM COALESCE(tip, repeat('0', 64)) THEN
        RAISE EXCEPTION 'AUDIT_CHAIN_BROKEN_LINK: prev_entry_hash khong khop dau chuoi';
    END IF;
    IF NEW.entry_hash IS DISTINCT FROM audit_chain_entry_hash(
        NEW.prev_entry_hash, NEW.actor_id, NEW.action_type, NEW.target_ref_id, NEW.created_at
    ) THEN
        RAISE EXCEPTION 'AUDIT_CHAIN_BAD_HASH: entry_hash khong dung';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_chain_link ON tamper_evident_audit_chain;
CREATE TRIGGER trg_audit_chain_link
    BEFORE INSERT ON tamper_evident_audit_chain
    FOR EACH ROW EXECUTE FUNCTION audit_chain_enforce_link();

-- Ghi 1 muc moi: tu lay prev, tu tinh hash.
CREATE OR REPLACE FUNCTION append_audit_chain(p_actor text, p_action text, p_target uuid)
RETURNS bigint
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    tip text;
    now_ts timestamptz := clock_timestamp();
    new_seq bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('vayshield.tamper_evident_audit_chain'));
    SELECT entry_hash INTO tip FROM tamper_evident_audit_chain ORDER BY seq_no DESC LIMIT 1;
    tip := COALESCE(tip, repeat('0', 64));
    INSERT INTO tamper_evident_audit_chain (actor_id, action_type, target_ref_id, prev_entry_hash, entry_hash, created_at)
    VALUES (p_actor, p_action, p_target, tip,
            audit_chain_entry_hash(tip, p_actor, p_action, p_target, now_ts), now_ts)
    RETURNING seq_no INTO new_seq;
    RETURN new_seq;
END;
$$;

-- Tra seq_no dau tien bi dut/gia mao, hoac NULL neu chuoi toan ven.
CREATE OR REPLACE FUNCTION verify_audit_chain() RETURNS bigint
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, public
AS $$
DECLARE
    r record;
    expected_prev text := repeat('0', 64);
BEGIN
    FOR r IN SELECT * FROM tamper_evident_audit_chain ORDER BY seq_no LOOP
        IF r.prev_entry_hash <> expected_prev
           OR r.entry_hash <> audit_chain_entry_hash(r.prev_entry_hash, r.actor_id, r.action_type, r.target_ref_id, r.created_at)
        THEN
            RETURN r.seq_no;
        END IF;
        expected_prev := r.entry_hash;
    END LOOP;
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- GRANT toi thieu cho role app (NOSUPERUSER NOBYPASSRLS), tao truoc migration.
-- Neu role chua ton tai (dev thuan) thi bo qua, khong loi.
-- ---------------------------------------------------------------------------
DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vayshield_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            licensed_lender_registry, anonymous_devices, family_guardian_links,
            protection_sessions, contract_audits, interception_events,
            legal_provision_nodes, legal_provision_edges, fraud_campaign_clusters,
            mule_account_nodes, mule_incident_edges, fintech_partners,
            b2b_disbursement_shields
            TO vayshield_app;
        -- Audit chain: chi SELECT + INSERT (khong UPDATE/DELETE/TRUNCATE).
        REVOKE ALL ON tamper_evident_audit_chain FROM vayshield_app;
        GRANT SELECT, INSERT ON tamper_evident_audit_chain TO vayshield_app;
        GRANT USAGE, SELECT ON SEQUENCE tamper_evident_audit_chain_seq_no_seq TO vayshield_app;
        GRANT EXECUTE ON FUNCTION
            uuid_generate_v7(), jsonb_max_depth(jsonb),
            audit_chain_entry_hash(text, text, text, uuid, timestamptz),
            append_audit_chain(text, text, uuid), verify_audit_chain()
            TO vayshield_app;
    END IF;
END
$grants$;

COMMIT;
