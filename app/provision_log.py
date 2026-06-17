"""SQLite provision log — local record of all provisioning operations."""
from __future__ import annotations

import json
import os
import sqlite3
import datetime
from typing import Optional

from app.config import DB_PATH

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS provision_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    mac TEXT NOT NULL,
    uuid TEXT,
    product_type TEXT,
    firmware_ver TEXT,
    test_results TEXT,
    status TEXT NOT NULL,
    error_reason TEXT,
    batch TEXT,
    cloud_confirmed INTEGER DEFAULT 0,
    recovery_key TEXT
);
"""


class ProvisionLog:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_SQL)
        # Idempotent migration: add recovery_key to an existing DB (CREATE IF NOT EXISTS
        # won't add the column to a table created before recovery keys existed).
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(provision_logs)")}
        if "recovery_key" not in cols:
            self._conn.execute("ALTER TABLE provision_logs ADD COLUMN recovery_key TEXT")
        self._conn.commit()

    def add(self, mac: str, uuid: Optional[str], product_type: str,
            firmware_ver: str, test_results: Optional[dict],
            status: str, error_reason: str = "", batch: str = "",
            cloud_confirmed: bool = False, recovery_key: str = "") -> int:
        cur = self._conn.execute(
            """INSERT INTO provision_logs
               (timestamp, mac, uuid, product_type, firmware_ver, test_results,
                status, error_reason, batch, cloud_confirmed, recovery_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.datetime.utcnow().isoformat() + "Z",
                mac, uuid, product_type, firmware_ver,
                json.dumps(test_results) if test_results else None,
                status, error_reason, batch,
                1 if cloud_confirmed else 0,
                recovery_key or None,
            )
        )
        self._conn.commit()
        return cur.lastrowid

    def list(self, limit: int = 100, offset: int = 0,
             status: str = "", search: str = "") -> list[dict]:
        query = "SELECT * FROM provision_logs WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND (mac LIKE ? OR uuid LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> dict:
        today = datetime.date.today().isoformat()
        total = self._conn.execute("SELECT COUNT(*) FROM provision_logs").fetchone()[0]
        success = self._conn.execute(
            "SELECT COUNT(*) FROM provision_logs WHERE status='success'").fetchone()[0]
        failed = self._conn.execute(
            "SELECT COUNT(*) FROM provision_logs WHERE status != 'success'").fetchone()[0]
        today_success = self._conn.execute(
            "SELECT COUNT(*) FROM provision_logs WHERE status='success' AND timestamp LIKE ?",
            (today + "%",)).fetchone()[0]
        today_failed = self._conn.execute(
            "SELECT COUNT(*) FROM provision_logs WHERE status != 'success' AND timestamp LIKE ?",
            (today + "%",)).fetchone()[0]
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "today_success": today_success,
            "today_failed": today_failed,
        }

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        if d.get("test_results"):
            try:
                d["test_results"] = json.loads(d["test_results"])
            except Exception:
                pass
        return d
