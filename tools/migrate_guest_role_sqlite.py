"""기존 로컬 SQLite의 계정 종류 제약에 GUEST를 추가한다. 원본 전체 백업 후 원자적으로 교체한다."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROLE_CHECK = re.compile(
    r"(CONSTRAINT\s+[\"`\[]?ck_users_role[\"`\]]?\s+CHECK\s*\(\s*role\s+IN\s*\()([^)]*)(\)\s*\))",
    re.IGNORECASE,
)


def migrate(path: Path) -> dict:
    path = path.resolve(strict=True)
    with sqlite3.connect(path, timeout=30) as connection:
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if row is None:
            raise ValueError("users 테이블이 있는 기존 SQLite 파일이 필요합니다.")
        original = row[0]
        check = ROLE_CHECK.search(original)
        if check is None or "'GUEST'" in check[2].upper():
            return {"changed": False, "database": str(path)}
        if {v.strip().strip("'").upper() for v in check[2].split(',')} != {"CHILD", "GUARDIAN"}:
            raise ValueError("예상하지 못한 계정 종류 제약입니다. 원본을 변경하지 않았습니다.")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.stem}.before-guest-{stamp}.db")
        with sqlite3.connect(backup) as target:
            connection.backup(target)

        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            before_errors = set(connection.execute("PRAGMA foreign_key_check").fetchall())
            objects = connection.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name='users' "
                "AND type IN ('index','trigger') AND sql IS NOT NULL"
            ).fetchall()
            count = connection.execute('SELECT count(*) FROM users').fetchone()[0]
            updated = ROLE_CHECK.sub(lambda m: m[1] + m[2] + ",'GUEST'" + m[3], original, count=1)
            create, matched = re.subn(
                r'^CREATE TABLE\s+["`\[]?users["`\]]?(?=\s*\()',
                'CREATE TABLE "users_guest_upgrade"', updated, count=1, flags=re.IGNORECASE,
            )
            if matched != 1:
                raise ValueError("users 테이블 정의를 확인할 수 없습니다.")
            connection.execute(create)
            connection.execute('INSERT INTO users_guest_upgrade SELECT * FROM users')
            if connection.execute('SELECT count(*) FROM users_guest_upgrade').fetchone()[0] != count:
                raise RuntimeError("복사한 계정 수가 다릅니다.")
            if connection.execute('SELECT * FROM users EXCEPT SELECT * FROM users_guest_upgrade').fetchone():
                raise RuntimeError("복사한 계정 내용이 다릅니다.")
            connection.execute('DROP TABLE users')
            connection.execute('ALTER TABLE users_guest_upgrade RENAME TO users')
            for (sql,) in objects:
                connection.execute(sql)
            after_errors = set(connection.execute("PRAGMA foreign_key_check").fetchall())
            if after_errors != before_errors:
                raise RuntimeError("참조 무결성 검사 결과가 달라졌습니다.")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != 'ok':
                raise RuntimeError("DB 무결성 검사에 실패했습니다.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"changed": True, "database": str(path), "backup": str(backup), "preservedUsers": count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data' / 'thinkforest.db'
    )
    args = parser.parse_args()
    print(json.dumps(migrate(args.database), ensure_ascii=False, indent=2))
