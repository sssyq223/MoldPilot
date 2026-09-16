-- MoldPilot PostgreSQL / Navicat verification script.
-- Open this file in Navicat against database "moldpilot" and run all statements.
-- It intentionally does not display password hashes or other secrets.

SELECT
    current_database() AS current_database,
    current_user AS connected_user,
    inet_server_addr() AS server_addr,
    inet_server_port() AS server_port,
    version() AS postgres_version;

SELECT
    username,
    display_name,
    department,
    super_admin,
    active,
    security_version
FROM app_user
WHERE username = 'admin';

SELECT
    'app_user' AS table_name,
    COUNT(*) AS row_count
FROM app_user
UNION ALL
SELECT
    'business_subject',
    COUNT(*)
FROM business_subject
UNION ALL
SELECT
    'audit_event',
    COUNT(*)
FROM audit_event
ORDER BY table_name;
