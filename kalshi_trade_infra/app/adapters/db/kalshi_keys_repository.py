"""Per-user production Kalshi API key storage (encrypted at rest by the caller)."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.adapters.db.sql import now_utc


def save_kalshi_key(cur, user_id: int, key_id: str, private_key_enc: bytes) -> None:
    cur.execute(
        """INSERT INTO app_kalshi_keys (user_id, key_id, private_key_enc, created_at) VALUES (%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE key_id=VALUES(key_id), private_key_enc=VALUES(private_key_enc),
             created_at=VALUES(created_at)""",
        (user_id, key_id, private_key_enc, now_utc()),
    )


def get_kalshi_key(cur, user_id: int) -> Optional[Dict]:
    cur.execute("SELECT key_id, private_key_enc, created_at FROM app_kalshi_keys WHERE user_id=%s", (user_id,))
    return cur.fetchone()


def has_kalshi_key(cur, user_id: int) -> bool:
    """Whether a live key is saved, for a page that only says so: one primary-key probe that selects a
    constant, never the key's id or its encrypted bytes (get_kalshi_key, for the paths that use the key)."""
    cur.execute("SELECT 1 AS present FROM app_kalshi_keys WHERE user_id=%s LIMIT 1", (user_id,))
    return cur.fetchone() is not None


def kalshi_keys_saved(cur, user_id: int) -> Tuple[bool, bool]:
    """(a live key is saved, a demo key is saved), for /api/me, which every signed-in page's shell carries and
    only says whether each is there: both primary-key probes in one statement, selecting constants, never a
    key's id or its encrypted bytes. True exactly when get_kalshi_key / get_kalshi_dev_key find a row."""
    cur.execute("SELECT EXISTS(SELECT 1 FROM app_kalshi_keys WHERE user_id=%s) AS live, "
                "EXISTS(SELECT 1 FROM app_kalshi_dev_keys WHERE user_id=%s) AS demo", (user_id, user_id))
    row = cur.fetchone() or {}
    return bool(row.get("live")), bool(row.get("demo"))


def delete_kalshi_key(cur, user_id: int) -> None:
    cur.execute("DELETE FROM app_kalshi_keys WHERE user_id=%s", (user_id,))
