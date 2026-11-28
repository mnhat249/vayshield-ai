-- ============================================================================
-- 0003_hardening.sql  [T20] Va lo hong schema (chi THEM, khong sua 0001/0002). Idempotent.
--   1. UNIQUE pubkey_ecdsa_p256 (chong Sybil/race dang ky thiet bi)
--   2. RLS cho family_guardian_links (owner = thiet bi, guardian = theo link id)
--   3. FK ON DELETE SET NULL cho cac cot tham chieu den du lieu bi xoa theo Luat 91
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. UNIQUE pubkey. NULL (thiet bi bia mo) van duoc phep nhieu dong (NULL khac nhau).
--    KHONG xoa du lieu: neu da co khoa trung, migration DUNG (rollback ca file, khong ghi
--    schema_migrations) voi thong bao ro; van hanh gop/xoa ban trung thu cong roi chay lai.
-- ---------------------------------------------------------------------------
DO $uniq$
DECLARE
    dup_count bigint;
BEGIN
    SELECT count(*) INTO dup_count FROM (
        SELECT pubkey_ecdsa_p256 FROM anonymous_devices
        WHERE pubkey_ecdsa_p256 IS NOT NULL
        GROUP BY pubkey_ecdsa_p256 HAVING count(*) > 1
    ) d;
    IF dup_count > 0 THEN
        RAISE EXCEPTION '0003: % khoa cong khai bi trung trong anonymous_devices; hay xu ly thu cong (khong tu xoa) roi chay lai migration', dup_count;
    END IF;
END
$uniq$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_anonymous_devices_pubkey
    ON anonymous_devices(pubkey_ecdsa_p256);

-- ---------------------------------------------------------------------------
-- 2. RLS family_guardian_links.
--    Ngu canh giao dich: app.current_device_id (chu thiet bi vay) HOAC
--    app.current_guardian_link_id (nguoi than thao tac dung 1 lien ket qua ma ghep doi).
--    Khong dat ngu canh => khong thay/khong ghi duoc gi. FK ON DELETE CASCADE (xoa thiet bi)
--    la hanh dong RI nen khong bi RLS chan.
-- ---------------------------------------------------------------------------
ALTER TABLE family_guardian_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE family_guardian_links FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pol_family_owner_or_link ON family_guardian_links;
CREATE POLICY pol_family_owner_or_link ON family_guardian_links
    USING (
        borrower_device_id = NULLIF(current_setting('app.current_device_id', TRUE), '')::UUID
        OR id = NULLIF(current_setting('app.current_guardian_link_id', TRUE), '')::UUID
    )
    WITH CHECK (
        borrower_device_id = NULLIF(current_setting('app.current_device_id', TRUE), '')::UUID
        OR id = NULLIF(current_setting('app.current_guardian_link_id', TRUE), '')::UUID
    );

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vayshield_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON family_guardian_links TO vayshield_app;
    END IF;
END
$grant$;

-- ---------------------------------------------------------------------------
-- 3. FK ON DELETE SET NULL (cot deu nullable). mule_incident_edges.reporter_device_id la
--    NOT NULL + nam trong UNIQUE quorum: GIU NGUYEN, vi phieu duoc chuyen sang thiet bi bia mo
--    (app/security/erasure.py) de bao toan quorum (EC-DB21).
-- ---------------------------------------------------------------------------
ALTER TABLE b2b_disbursement_shields
    DROP CONSTRAINT IF EXISTS b2b_disbursement_shields_matched_session_id_fkey;
ALTER TABLE b2b_disbursement_shields
    ADD CONSTRAINT b2b_disbursement_shields_matched_session_id_fkey
    FOREIGN KEY (matched_session_id) REFERENCES protection_sessions(id) ON DELETE SET NULL;

ALTER TABLE mule_incident_edges
    DROP CONSTRAINT IF EXISTS mule_incident_edges_source_audit_id_fkey;
ALTER TABLE mule_incident_edges
    ADD CONSTRAINT mule_incident_edges_source_audit_id_fkey
    FOREIGN KEY (source_audit_id) REFERENCES contract_audits(id) ON DELETE SET NULL;

ALTER TABLE mule_incident_edges
    DROP CONSTRAINT IF EXISTS mule_incident_edges_source_intercept_id_fkey;
ALTER TABLE mule_incident_edges
    ADD CONSTRAINT mule_incident_edges_source_intercept_id_fkey
    FOREIGN KEY (source_intercept_id) REFERENCES interception_events(id) ON DELETE SET NULL;
