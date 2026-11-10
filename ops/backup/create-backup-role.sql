-- Run as a PostgreSQL administrator with permission to grant BYPASSRLS.
-- Set the password separately through your secret mechanism; do not commit it.
CREATE ROLE studyhub_backup LOGIN BYPASSRLS;
GRANT CONNECT ON DATABASE studyhub TO studyhub_backup;
GRANT USAGE ON SCHEMA public TO studyhub_backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO studyhub_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO studyhub_backup;
-- Replace studyhub with the role that owns/creates tables during migrations.
ALTER DEFAULT PRIVILEGES FOR ROLE studyhub IN SCHEMA public GRANT SELECT ON TABLES TO studyhub_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE studyhub IN SCHEMA public GRANT SELECT ON SEQUENCES TO studyhub_backup;
