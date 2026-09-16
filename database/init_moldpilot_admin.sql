-- MoldPilot local PostgreSQL seed.
-- Target database: moldpilot
-- Creates/updates the single local super administrator requested for development validation.

INSERT INTO app_user (
    id,
    created_at,
    username,
    display_name,
    department,
    password_hash,
    super_admin,
    active,
    security_version
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    CURRENT_TIMESTAMP,
    'admin',
    '超级管理员',
    '系统管理',
    '$argon2id$v=19$m=65536,t=3,p=4$crZ28vZfFpINIWwNr3QWTg$iMa7d+MjHol7Vl+TJNWJRuhwwVT4W8hZ+t4cR0p5Anc',
    TRUE,
    TRUE,
    1
)
ON CONFLICT (username) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    department = EXCLUDED.department,
    password_hash = EXCLUDED.password_hash,
    super_admin = TRUE,
    active = TRUE,
    security_version = app_user.security_version + 1;
