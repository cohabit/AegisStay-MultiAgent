"""Deployment secret generation and consistent SQLite backups. No dependencies."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path


def initialize(path: Path):
    roles = ("frontdesk", "housekeeping", "engineering", "manager")
    credentials = {secrets.token_urlsafe(40): {"role": role, "subject": role + "-01"} for role in roles}
    settings = {
        "AEGIS_DEPLOYED": "1", "AEGIS_AUTH_ENABLED": "1", "AEGIS_DB_PATH": "/data/aegisstay.sqlite3",
        "AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic",
        "AEGIS_BUSINESS_DATE": date.today().isoformat(),
        "AEGIS_ALLOWED_ORIGINS": "http://localhost:8501",
        "AEGIS_STAFF_TOKEN": secrets.token_urlsafe(40), "AEGIS_SIGNING_KEY": secrets.token_urlsafe(48),
        "AEGIS_STAFF_KEYS_JSON": json.dumps(credentials, separators=(",", ":")),
    }
    # Exclusive creation protects an existing deployment and its guest passes.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(key + "=" + value for key, value in settings.items()) + "\n")
    print(f"Created {path.resolve()}. Secrets were not printed. Store staff credentials securely.")


def backup(database: Path, output: Path):
    if not database.is_file():
        raise ValueError("数据库不存在")
    if output.exists():
        raise FileExistsError("备份目标已存在，拒绝覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(output)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("备份完整性检查失败")
    except Exception:
        # Keep the file for diagnosis; never advertise a failed backup.
        raise
    print(f"Backup verified: {output.resolve()}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--output", type=Path, default=Path("deploy.env"))
    copy = sub.add_parser("backup")
    copy.add_argument("--database", type=Path, required=True)
    copy.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.output)
    else:
        backup(args.database, args.output)


if __name__ == "__main__":
    main()
