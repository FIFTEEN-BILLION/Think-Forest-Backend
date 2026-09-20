-- 기존 PostgreSQL DB에 게스트 기능 배포 전 적용. 계정/기록을 삭제하지 않는다.
BEGIN;
ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role;
ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ('CHILD', 'GUARDIAN', 'GUEST'));
CREATE TABLE IF NOT EXISTS guest_rate_limits (
    key VARCHAR(80) NOT NULL,
    "window" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (key, "window")
);
COMMIT;
