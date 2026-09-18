"""Local-only schema upgrade. Back up first; archive incompatible tables without discarding rows.

Run from backend: .venv/Scripts/python tools/migrate_local_sqlite.py
Never connects to Supabase. Existing compatible tables stay in place.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base  # noqa: E402
from app.v1 import tables  # noqa: E402,F401
from sqlalchemy import create_engine  # noqa: E402


def repair_legacy_references(connection: sqlite3.Connection) -> list[dict]:
    """Repair foreign keys in obsolete tables, keeping every row and its indexes."""
    names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    repaired = []
    for name in sorted(names - set(Base.metadata.tables)):
        if name.startswith("legacy_"):
            continue
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
        changed = sql
        for _, _, parent, column, target, *_ in connection.execute(f'PRAGMA foreign_key_list("{name}")'):
            candidates = [n for n in names if n.startswith("legacy_") and n.endswith("_" + parent)]
            matches = [
                n
                for n in candidates
                if connection.execute(
                    f'SELECT count(*) FROM "{name}" child WHERE child."{column}" IS NOT NULL '
                    f'AND NOT EXISTS (SELECT 1 FROM "{n}" parent WHERE parent."{target}" = child."{column}")'
                ).fetchone()[0]
                == 0
            ]
            if len(matches) != 1:
                continue
            archived = matches[0]
            changed = re.sub(
                r'(?i)(REFERENCES\s+)"?' + re.escape(parent) + r'"?(?=\s*\()',
                lambda m, archived=archived: m[1] + '"' + archived + '"',
                changed,
            )
            repaired.append({"table": name, "parent": parent, "archive": archived})
        if changed == sql:
            continue
        indexes = [
            r[0]
            for r in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (name,)
            )
        ]
        temporary = "migration_copy_" + name
        create = re.sub(
            r'(?i)^CREATE TABLE\s+"?' + re.escape(name) + r'"?', 'CREATE TABLE "' + temporary + '"', changed, count=1
        )
        count = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        connection.execute(create)
        connection.execute(f'INSERT INTO "{temporary}" SELECT * FROM "{name}"')
        assert connection.execute(f'SELECT count(*) FROM "{temporary}"').fetchone()[0] == count
        connection.execute(f'DROP TABLE "{name}"')
        connection.execute(f'ALTER TABLE "{temporary}" RENAME TO "{name}"')
        for index in indexes:
            connection.execute(index)
    return repaired


def migrate(path: Path) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Existing local SQLite file required")
    connection = sqlite3.connect(path)
    names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    incompatible = []
    for table in Base.metadata.sorted_tables:
        if table.name not in names:
            continue
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table.name}")')}
        if set(table.c.keys()) - columns:
            incompatible.append(table.name)
    missing = set(Base.metadata.tables) - names
    if not incompatible and not missing:
        unmapped = connection.execute(
            "SELECT count(*) FROM conversation_sessions c "
            "LEFT JOIN conversation_owners o ON o.session_id=c.id WHERE o.session_id IS NULL"
        ).fetchone()[0]
        if not unmapped and not connection.execute("PRAGMA foreign_key_check").fetchall():
            connection.close()
            return {"database": str(path), "changed": False, "archived": {}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.stem}.before-api-{stamp}.db")
    with sqlite3.connect(backup) as target:
        connection.backup(target)
    archived = {}
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute("BEGIN IMMEDIATE")
        for name in incompatible:
            archived_name = f"legacy_{stamp}_{name}"
            count = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            connection.execute(f'ALTER TABLE "{name}" RENAME TO "{archived_name}"')
            indexes = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (archived_name,),
            ).fetchall()
            for (index,) in indexes:
                connection.execute('DROP INDEX "' + index.replace('"', '""') + '"')
            archived[name] = {"table": archived_name, "rows": count}
        connection.commit()
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        if "profile_settings" in archived:
            source = archived["profile_settings"]["table"]
            connection.execute(f'''INSERT INTO profile_settings
                (id,profile_id,tts_enabled,guardian_preview_enabled,theme,retention_days,version,created_at,updated_at)
                SELECT 'pst_migrated_' || profile_id,profile_id,tts_enabled,guardian_preview_enabled,
                upper(theme),retention_days,version,updated_at,updated_at FROM "{source}"''')
        # Preserve the historical child association before a user changes their default profile.
        connection.execute("""INSERT OR IGNORE INTO conversation_owners(session_id, child_id)
            SELECT c.id,u.child_id FROM conversation_sessions c JOIN users u ON u.id=c.user_id""")
        # Prior implementation cached incompatible response bodies. Request receipts are not user records.
        if incompatible:
            connection.execute("DELETE FROM idempotency_records")
        repaired = repair_legacy_references(connection)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Foreign key check failed; backup retained")
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        report = {
            "database": str(path),
            "backup": str(backup),
            "archived": archived,
            "repairedLegacyReferences": repaired,
        }
        path.with_name(f"migration-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        connection.close()


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "data" / "thinkforest.db"
    print(json.dumps(migrate(target), indent=2))
