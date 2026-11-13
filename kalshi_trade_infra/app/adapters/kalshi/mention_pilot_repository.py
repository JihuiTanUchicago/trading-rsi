"""Tables and queries of the live mention-market pilot (app.mention_pilot_run, app.services.mention_pilot).

The pilot's NO positions are real exposure on the user's Kalshi account, so the reconcilers count them
(reconcile_repository.live_book_by_ticker_side, data_pipeline.live_alerter): without that every pilot
fill would read as an untracked position and halt the user's live entries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

DDL = [
    """
    CREATE TABLE IF NOT EXISTS app_mention_pilot_events (
      event_ticker VARCHAR(128) PRIMARY KEY,
      trigger_ts DOUBLE NOT NULL,             -- unix seconds of the live trigger print
      seen_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_mention_pilot_orders (
      order_id VARCHAR(64) PRIMARY KEY,
      client_order_id VARCHAR(64) NOT NULL,
      user_id BIGINT NOT NULL,
      event_ticker VARCHAR(128) NOT NULL,
      ticker VARCHAR(128) NOT NULL,
      yes_price DECIMAL(6,4) NOT NULL,        -- our resting YES ask
      qty DECIMAL(10,2) NOT NULL,
      filled DECIMAL(10,2) NOT NULL DEFAULT 0,
      sim_filled DECIMAL(10,2) NOT NULL DEFAULT 0,  -- what the print replay would have credited it
      status VARCHAR(12) NOT NULL,            -- resting | cancelled | done
      placed_at DATETIME NOT NULL,
      closed_at DATETIME NULL,
      INDEX idx_mpo_status (status),
      INDEX idx_mpo_ticker (ticker)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_mention_pilot_fills (
      fill_id VARCHAR(64) PRIMARY KEY,
      order_id VARCHAR(64) NOT NULL,
      ticker VARCHAR(128) NOT NULL,
      qty DECIMAL(10,2) NOT NULL,
      no_price DECIMAL(6,4) NOT NULL,
      fee DECIMAL(12,6) NOT NULL,
      ts DOUBLE NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_mention_pilot_positions (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      user_id BIGINT NOT NULL,
      event_ticker VARCHAR(128) NOT NULL,
      ticker VARCHAR(128) NOT NULL,
      side VARCHAR(3) NOT NULL DEFAULT 'no',
      qty DECIMAL(10,2) NOT NULL,
      cost DECIMAL(12,4) NOT NULL,            -- sum of NO price x contracts
      fees DECIMAL(12,6) NOT NULL,
      status VARCHAR(8) NOT NULL DEFAULT 'open',  -- open | closed
      result VARCHAR(16) NULL,
      pnl DECIMAL(12,4) NULL,
      opened_at DATETIME NOT NULL,
      closed_at DATETIME NULL,
      UNIQUE KEY uq_mpp_ticker (ticker),
      INDEX idx_mpp_status (user_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save_trigger(cur, event: str, ts: float) -> None:
    cur.execute("INSERT IGNORE INTO app_mention_pilot_events (event_ticker, trigger_ts, seen_at) VALUES (%s,%s,%s)",
                (event, float(ts), _now()))


def triggers_since(cur, since_ts: float) -> Dict[str, float]:
    cur.execute("SELECT event_ticker, trigger_ts FROM app_mention_pilot_events WHERE trigger_ts >= %s", (since_ts,))
    return {r["event_ticker"]: float(r["trigger_ts"]) for r in cur.fetchall()}


def add_order(cur, order_id: str, client_order_id: str, user_id: int, event: str, ticker: str,
              yes_price: float, qty: float) -> None:
    cur.execute("""INSERT INTO app_mention_pilot_orders (order_id, client_order_id, user_id, event_ticker, ticker,
                     yes_price, qty, status, placed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'resting',%s)""",
                (order_id, client_order_id, user_id, event, ticker, yes_price, qty, _now()))


def close_order(cur, order_id: str, status: str = "cancelled") -> None:
    cur.execute("UPDATE app_mention_pilot_orders SET status=%s, closed_at=%s WHERE order_id=%s AND status='resting'",
                (status, _now(), order_id))


def add_sim_fill(cur, order_id: str, qty: float) -> None:
    cur.execute("UPDATE app_mention_pilot_orders SET sim_filled=sim_filled+%s WHERE order_id=%s", (qty, order_id))


def orders(cur, status: Optional[str] = None) -> List[Dict]:
    if status:
        cur.execute("SELECT * FROM app_mention_pilot_orders WHERE status=%s", (status,))
    else:
        cur.execute("SELECT * FROM app_mention_pilot_orders")
    return cur.fetchall()


def book_fill(cur, fill_id: str, order_id: str, user_id: int, event: str, ticker: str, qty: float,
              no_price: float, fee: float, ts: float) -> bool:
    """Book one fill exactly once (fill_id is the key): the fill row, the order's filled count and the
    ticker's NO position. False when it was already booked."""
    cur.execute("""INSERT IGNORE INTO app_mention_pilot_fills (fill_id, order_id, ticker, qty, no_price, fee, ts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""", (fill_id, order_id, ticker, qty, no_price, fee, ts))
    if cur.rowcount != 1:
        return False
    cur.execute("UPDATE app_mention_pilot_orders SET filled=filled+%s WHERE order_id=%s", (qty, order_id))
    cur.execute("""INSERT INTO app_mention_pilot_positions (user_id, event_ticker, ticker, side, qty, cost, fees, opened_at)
                   VALUES (%s,%s,%s,'no',%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE qty=qty+VALUES(qty), cost=cost+VALUES(cost), fees=fees+VALUES(fees)""",
                (user_id, event, ticker, qty, round(qty * no_price, 4), fee, _now()))
    return True


def open_positions(cur, user_id: int) -> List[Dict]:
    cur.execute("SELECT * FROM app_mention_pilot_positions WHERE user_id=%s AND status='open'", (user_id,))
    return cur.fetchall()


def settle_position(cur, position_id: int, result: str, pnl: float) -> bool:
    cur.execute("""UPDATE app_mention_pilot_positions SET status='closed', result=%s, pnl=%s, closed_at=%s
                   WHERE id=%s AND status='open'""", (result[:16], round(pnl, 4), _now(), position_id))
    return cur.rowcount == 1


def realized_pnl(cur, user_id: int) -> float:
    cur.execute("SELECT COALESCE(SUM(pnl),0) AS p FROM app_mention_pilot_positions WHERE user_id=%s AND status='closed'",
                (user_id,))
    return float((cur.fetchone() or {}).get("p") or 0)


def filled_by_ticker(cur) -> Dict[str, float]:
    cur.execute("SELECT ticker, COALESCE(SUM(qty),0) AS q FROM app_mention_pilot_fills GROUP BY ticker")
    return {r["ticker"]: float(r["q"]) for r in cur.fetchall()}


def settled_count(cur, user_id: int) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM app_mention_pilot_positions WHERE user_id=%s AND status='closed'", (user_id,))
    return int((cur.fetchone() or {}).get("n") or 0)
