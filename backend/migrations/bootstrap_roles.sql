-- ============================================================================
-- Bootstrap role app (idempotent, chay bang role ADMIN, nhieu lan khong loi).
-- Tao vayshield_app (NOSUPERUSER NOBYPASSRLS) neu thieu, GRANT toi thieu, va
-- ALTER DEFAULT PRIVILEGES cho bang/sequence tao sau. Mat khau do compose/CI dat.
-- Runner (app.db_schema.migrate) luon chay file nay truoc va sau khi migrate.
-- ============================================================================
DO $boot$
DECLARE
    t text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vayshield_app') THEN
        CREATE ROLE vayshield_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    ALTER ROLE vayshield_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO vayshield_app', current_database());
    GRANT USAGE ON SCHEMA public TO vayshield_app;

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vayshield_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO vayshield_app;

    -- Bang da ton tai (tru bang theo doi migration va audit chain append-only).
    FOR t IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename NOT IN ('schema_migrations', 'tamper_evident_audit_chain')
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO vayshield_app', t);
    END LOOP;

    IF to_regclass('public.tamper_evident_audit_chain') IS NOT NULL THEN
        REVOKE ALL ON tamper_evident_audit_chain FROM vayshield_app;
        GRANT SELECT, INSERT ON tamper_evident_audit_chain TO vayshield_app;
        GRANT USAGE, SELECT ON SEQUENCE tamper_evident_audit_chain_seq_no_seq TO vayshield_app;
    END IF;
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
        REVOKE ALL ON schema_migrations FROM vayshield_app;
    END IF;
END
$boot$;
