-- 0002: cot ho tro seed (T08). Migration MOI, khong sua 0001.
--   * aliases / verified cho licensed_lender_registry
--   * verified cho legal_provision_nodes (noi dung chua doi chieu nguon chinh thuc = FALSE)
--   * sandbox_namespace cho do thi mule (NULL = du lieu live; 'sandbox_seed' = du lieu mo phong)
BEGIN;

ALTER TABLE licensed_lender_registry ADD COLUMN IF NOT EXISTS aliases  TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE licensed_lender_registry ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE legal_provision_nodes    ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE fraud_campaign_clusters  ADD COLUMN IF NOT EXISTS sandbox_namespace VARCHAR(64);
ALTER TABLE mule_account_nodes       ADD COLUMN IF NOT EXISTS sandbox_namespace VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_mule_sandbox_ns ON mule_account_nodes(sandbox_namespace)
    WHERE sandbox_namespace IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cluster_sandbox_ns ON fraud_campaign_clusters(sandbox_namespace)
    WHERE sandbox_namespace IS NOT NULL;

COMMIT;
