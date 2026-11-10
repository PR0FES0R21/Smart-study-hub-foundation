-- Run once as the migration owner/admin after the first migration.
-- Set the password interactively with psql: \password studyhub_app
CREATE ROLE studyhub_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE studyhub TO studyhub_app;
GRANT USAGE ON SCHEMA public TO studyhub_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO studyhub_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO studyhub_app;
-- Replace studyhub if a different role creates tables in your release process.
ALTER DEFAULT PRIVILEGES FOR ROLE studyhub IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO studyhub_app;
ALTER DEFAULT PRIVILEGES FOR ROLE studyhub IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO studyhub_app;
