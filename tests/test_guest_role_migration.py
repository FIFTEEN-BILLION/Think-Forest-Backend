"""기존 CHECK 제약을 가진 DB도 기록·참조·인덱스를 유지하면서 게스트를 생성한다."""

import sqlite3

import pytest
from app import db
from app.main import app
from fastapi.testclient import TestClient
from tools.migrate_guest_role_sqlite import migrate


def test_guest_role_migration_preserves_data_and_allows_real_guest_endpoint(tmp_path):
    path = tmp_path / 'legacy.db'
    db.configure(f'sqlite:///{path.as_posix()}')
    db.init_db()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE users')
        connection.execute('''CREATE TABLE users (
            id VARCHAR(40) PRIMARY KEY, role VARCHAR(12) NOT NULL,
            status VARCHAR(12) NOT NULL, family_id VARCHAR(32) REFERENCES families(id),
            child_id VARCHAR(32) REFERENCES children(id), is_tester BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL,
            CONSTRAINT ck_users_role CHECK (role IN ('CHILD','GUARDIAN')),
            CONSTRAINT ck_users_status CHECK (status IN ('ACTIVE','DELETED'))
        )''')
        connection.execute("INSERT INTO users VALUES ('old','GUARDIAN','ACTIVE',NULL,NULL,0,'2026-09-20')")
        connection.execute('CREATE INDEX ix_users_child_id ON users(child_id)')
        connection.execute('CREATE TABLE migration_test_reference (id TEXT REFERENCES users(id))')
        connection.execute("INSERT INTO migration_test_reference VALUES ('old')")
    report = migrate(path)
    assert report['changed'] and report['preservedUsers'] == 1
    with sqlite3.connect(report['backup']) as backup:
        assert backup.execute('SELECT id,role FROM users').fetchall() == [('old', 'GUARDIAN')]
        assert "'GUEST'" not in backup.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT id,role FROM users').fetchall() == [('old', 'GUARDIAN')]
        assert connection.execute('SELECT * FROM migration_test_reference').fetchall() == [('old',)]
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='ix_users_child_id'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE users SET role='UNKNOWN' WHERE id='old'")
    assert migrate(path)['changed'] is False
    response = TestClient(app).post('/api/v1/auth/guest', json={})
    assert response.status_code == 200, response.text
    assert response.json()['user']['role'] == 'GUEST'
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT role FROM users WHERE id='old'").fetchone() == ('GUARDIAN',)
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
