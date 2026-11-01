"""Community feed SQL: posts, images, reactions, likes, comments."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from app.adapters.db.sql import _json, _loads, now_utc
from app.adapters.db.strategy_repository import DEMO_DIRECTION, NO_EXPERIMENT_SQL


def store_image(cur, user_id: int, content_type: str, data: bytes) -> int:
    cur.execute(
        "INSERT INTO app_images (user_id, content_type, data, created_at) VALUES (%s,%s,%s,%s)",
        (user_id, content_type, data, now_utc()),
    )
    return cur.lastrowid


def get_image(cur, image_id: int) -> Optional[Dict]:
    cur.execute("SELECT content_type, data FROM app_images WHERE id=%s", (image_id,))
    return cur.fetchone()


def create_post(cur, user_id: int, body: str, kind: str, snapshot: Optional[Dict], image_id: Optional[int] = None) -> int:
    cur.execute(
        "INSERT INTO app_posts (user_id, body, kind, snapshot_json, image_id, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, body[:2000], kind, _json(snapshot), image_id, now_utc()),
    )
    return cur.lastrowid


def edit_post(cur, post_id: int, user_id: int, body: str) -> bool:
    """Edit own post body, stamping edited_at. Returns False if not the owner."""
    cur.execute(
        "UPDATE app_posts SET body=%s, edited_at=%s WHERE id=%s AND user_id=%s",
        (body[:2000], now_utc(), post_id, user_id),
    )
    return cur.rowcount == 1


def delete_post(cur, post_id: int, user_id: int) -> bool:
    """Remove the author's own post with its replies, likes, reactions and photo, so nothing it shared
    stays readable. False when the post isn't theirs or is already gone."""
    cur.execute("SELECT image_id FROM app_posts WHERE id=%s AND user_id=%s", (post_id, user_id))
    row = cur.fetchone()
    if not row:
        return False
    for table in ("app_post_comments", "app_post_likes", "app_post_reactions"):
        cur.execute("DELETE FROM %s WHERE post_id=%%s" % table, (post_id,))
    cur.execute("DELETE FROM app_posts WHERE id=%s AND user_id=%s", (post_id, user_id))
    if row.get("image_id"):
        cur.execute("DELETE FROM app_images WHERE id=%s AND user_id=%s", (row["image_id"], user_id))
    return True


# A shared strategy card whose strategy has no gatekeeper record (NO_EXPERIMENT_SQL), as a column of the feed's own
# read, not a read of its own: its author's strategy by the snapshot's id (primary key), the seeded example never.
# A card stored before the snapshot carried `no_gatekeeper_record` (#598) said "on unseen data" for a pass from the
# old loose backtest gate; the feed adds the flag to a "passed" card from this (community._snapshot_out), and to a
# "failed" one, a void the first version's simple backtest stored with no record (VOID_NO_RECORD_SQL). Display only.
POST_NO_RECORD_SQL = ("(p.kind='strategy' AND EXISTS (SELECT 1 FROM app_strategies s "
                      "WHERE s.id=CAST(JSON_UNQUOTE(JSON_EXTRACT(p.snapshot_json, '$.id')) AS UNSIGNED) "
                      "AND s.user_id=p.user_id AND s.direction<>%s AND " + NO_EXPERIMENT_SQL + ")) AS no_gatekeeper_record")


# A strategy card whose strategy its author has deleted since, as a column of the same read (its row by the snapshot's
# id, primary key): the author's card linked its page, which said "Not found", and every other reader's said "Ask them
# to share a copy from its page" of a page that no longer exists (walked 2026-10-31). Looked up for every reader now.
# Display only (community._snapshot_out).
POST_GONE_SQL = ("(p.kind='strategy' AND JSON_EXTRACT(p.snapshot_json, '$.id') IS NOT NULL AND NOT EXISTS "
                 "(SELECT 1 FROM app_strategies g WHERE g.id=CAST(JSON_UNQUOTE(JSON_EXTRACT(p.snapshot_json, '$.id')) AS UNSIGNED) "
                 "AND g.user_id=p.user_id)) AS strategy_gone")


# A deployment card whose deployment its author has removed since (stopped: Deploy's Remove), or that no longer exists,
# as a column of the same read (its row by the snapshot's id, primary key): "realized +$1.39 · 3 trades since Oct 10"
# read as a run still trading (walked 2026-10-31). Display only (community._snapshot_out).
POST_REMOVED_SQL = ("(p.kind='deployment' AND JSON_EXTRACT(p.snapshot_json, '$.id') IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM app_deployments rd WHERE rd.id=CAST(JSON_UNQUOTE(JSON_EXTRACT(p.snapshot_json, '$.id')) AS UNSIGNED) "
                    "AND rd.user_id=p.user_id AND rd.status<>'stopped')) AS deployment_removed")


def list_posts(cur, viewer_id: int, before_id: int = 0, limit: int = 30, mine_only: bool = False) -> List[Dict]:
    cur.execute(
        """SELECT p.id, p.body, p.kind, p.snapshot_json, p.image_id, p.edited_at, p.likes_count, p.comments_count, p.created_at,
                  u.name, u.username, u.picture_url, (p.user_id = %s) AS mine,
                  EXISTS(SELECT 1 FROM app_post_likes l WHERE l.post_id=p.id AND l.user_id=%s) AS liked,
                  """ + POST_NO_RECORD_SQL + """, """ + POST_GONE_SQL + """, """ + POST_REMOVED_SQL + """
           FROM app_posts p JOIN app_users u ON u.id=p.user_id
           WHERE (%s = 0 OR p.id < %s) AND (%s = 0 OR p.user_id = %s) ORDER BY p.id DESC LIMIT %s""",
        (viewer_id, viewer_id, DEMO_DIRECTION, before_id, before_id, 1 if mine_only else 0, viewer_id, limit),
    )
    rows = cur.fetchall()
    for r in rows:
        r["snapshot"] = _loads(r.pop("snapshot_json"))
        r["mine"] = bool(r["mine"]); r["liked"] = bool(r["liked"])
        r["no_gatekeeper_record"] = bool(r.get("no_gatekeeper_record"))
        r["strategy_gone"] = bool(r.get("strategy_gone"))
        r["deployment_removed"] = bool(r.get("deployment_removed"))
    by_post = reactions_for(cur, [r["id"] for r in rows], viewer_id)
    for r in rows:
        r["reactions"] = by_post.get(r["id"], [])
    return rows


def reactions_for(cur, post_ids: List[int], viewer_id: int) -> Dict[int, List[Dict]]:
    """Per-post emoji counts (+ whether the viewer added each, + up to 8 reactor
    names and @handles for the hover tooltip), most-used first. The names/handles ride
    along in the feed payload so the tooltip renders from already-loaded data with no
    per-hover round-trip. One grouped query for the whole page of posts (no N+1)."""
    if not post_ids:
        return {}
    marks = ",".join(["%s"] * len(post_ids))
    # SEPARATOR is the unit-separator char (never in a name); SUBSTRING_INDEX caps the list. Reactions in the same second
    # are ordered by who made them: on created_at alone their order came back different from one read to the next, so the
    # feed's unchanged poll answer differed and the whole feed was drawn again every 20s (walk 2026-11-01), and the two
    # lists could name reactor i and handle i in different orders.
    cur.execute(
        "SELECT r.post_id, r.emoji, COUNT(*) AS n, MAX(r.user_id=%s) AS mine, "
        "SUBSTRING_INDEX(GROUP_CONCAT(COALESCE(u.name,'') ORDER BY r.created_at, r.user_id SEPARATOR 0x1f), 0x1f, 8) AS names, "
        "SUBSTRING_INDEX(GROUP_CONCAT(COALESCE(u.username,'') ORDER BY r.created_at, r.user_id SEPARATOR 0x1f), 0x1f, 8) AS handles "
        "FROM app_post_reactions r JOIN app_users u ON u.id=r.user_id "
        "WHERE r.post_id IN (%s) GROUP BY r.post_id, r.emoji "
        "ORDER BY n DESC, r.emoji" % ("%s", marks),
        tuple([viewer_id] + list(post_ids)),
    )
    out: Dict[int, List[Dict]] = {}
    for r in cur.fetchall():
        # Kept position for position (a missing name or handle is ''), so the route names reactor i by handle i.
        names = (r.get("names") or "").split("\x1f")
        handles = (r.get("handles") or "").split("\x1f")
        out.setdefault(r["post_id"], []).append(
            {"emoji": r["emoji"], "count": int(r["n"]), "mine": bool(r["mine"]),
             "names": names, "handles": handles})
    return out


def toggle_reaction(cur, post_id: int, user_id: int, emoji: str) -> bool:
    """Add/remove one emoji reaction. Returns True if now reacted."""
    cur.execute("DELETE FROM app_post_reactions WHERE post_id=%s AND user_id=%s AND emoji=%s", (post_id, user_id, emoji))
    if cur.rowcount:
        return False
    cur.execute("INSERT INTO app_post_reactions (post_id, user_id, emoji, created_at) VALUES (%s,%s,%s,%s)",
                (post_id, user_id, emoji, now_utc()))
    return True


def set_reaction(cur, post_id: int, user_id: int, emoji: str, on: bool) -> bool:
    """Put one emoji reaction on, or take it off, whatever it was: a press from a feed drawn before another tab changed
    it does what the reader saw it would. Returns True if now reacted."""
    if not on:
        cur.execute("DELETE FROM app_post_reactions WHERE post_id=%s AND user_id=%s AND emoji=%s", (post_id, user_id, emoji))
        return False
    cur.execute("SELECT 1 FROM app_post_reactions WHERE post_id=%s AND user_id=%s AND emoji=%s", (post_id, user_id, emoji))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO app_post_reactions (post_id, user_id, emoji, created_at) VALUES (%s,%s,%s,%s)",
                    (post_id, user_id, emoji, now_utc()))
    return True


def toggle_like(cur, post_id: int, user_id: int) -> bool:
    """Returns True if now liked, False if unliked. Keeps likes_count in sync."""
    cur.execute("DELETE FROM app_post_likes WHERE post_id=%s AND user_id=%s", (post_id, user_id))
    if cur.rowcount:
        cur.execute("UPDATE app_posts SET likes_count=GREATEST(likes_count-1,0) WHERE id=%s", (post_id,))
        return False
    cur.execute("INSERT INTO app_post_likes (post_id, user_id, created_at) VALUES (%s,%s,%s)", (post_id, user_id, now_utc()))
    cur.execute("UPDATE app_posts SET likes_count=likes_count+1 WHERE id=%s", (post_id,))
    return True


def post_exists(cur, post_id: int) -> bool:
    cur.execute("SELECT 1 FROM app_posts WHERE id=%s", (post_id,))
    return cur.fetchone() is not None


def post_author(cur, post_id: int) -> Optional[Dict]:
    """The post's author (user_id, email, email_opt_out, name) plus the post's words as `preview`, for
    reply notifications. Whole (at most 2,000 characters): the email cuts it, after its marks read as plain words,
    with an ellipsis (notify._plain). LEFT(body, 140) cut it here first, so a long post's quote stopped mid-word
    with nothing to say it went on ("…in the band the mark”)."""
    cur.execute(
        """SELECT p.user_id, u.email, u.email_opt_out, u.name, p.body AS preview
           FROM app_posts p JOIN app_users u ON u.id=p.user_id WHERE p.id=%s""",
        (post_id,),
    )
    return cur.fetchone()


def add_comment(cur, post_id: int, user_id: int, body: str) -> int:
    cur.execute(
        "INSERT INTO app_post_comments (post_id, user_id, body, created_at) VALUES (%s,%s,%s,%s)",
        (post_id, user_id, body[:1000], now_utc()),
    )
    cur.execute("UPDATE app_posts SET comments_count=comments_count+1 WHERE id=%s", (post_id,))
    return cur.lastrowid


def list_comments(cur, post_id: int, limit: int = 100, before: int = 0) -> List[Dict]:
    """A post's newest `limit` replies (older than reply id `before`, when given), oldest first. It read the
    oldest 100: a thread past 100 replies never showed its newest ones, a reply just sent among them (walk
    2026-11-01, "121 replies" listing up to the 106th)."""
    cur.execute(
        """SELECT c.id, c.body, c.created_at, u.name, u.username, u.picture_url
           FROM app_post_comments c JOIN app_users u ON u.id=c.user_id
           WHERE c.post_id=%s AND (%s = 0 OR c.id < %s) ORDER BY c.id DESC LIMIT %s""",
        (post_id, before, before, limit),
    )
    return list(reversed(cur.fetchall()))


def recent_posts_count(cur, user_id: int, since: datetime) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM app_posts WHERE user_id=%s AND created_at >= %s", (user_id, since))
    return int(cur.fetchone()["n"])
