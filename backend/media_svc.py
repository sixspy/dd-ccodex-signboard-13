"""signboard-13 媒资桥校验和核心（唯一正确性口径：甲路径）。

口径（见 docs/signboard-13-命运说明.md，2026-11-27 拍板锁死）：
- 媒资桥同步必须比对台账/期望校验和与桥回传校验和，一致才把素材置 ready+verified；
- 不一致/缺失整笔拒写 ready：同事务把素材压回 draft+mismatch，正式可播零增；
- media_verifications（媒资桥结果）、assets（素材正式状态）、audit_log、change_history
  在同一个 BEGIN IMMEDIATE 事务内写入/提交，跨表账实一致；
- 全局命名互斥 MEDIA_LOCK_KEY='媒资桥置可播互斥'：进程内非阻塞互斥 + SQLite 写事务
  + write_locks 命名行锁（兼容 /api/locks 外部持锁），争用恰一成一败，败方零写入。

本模块不依赖 FastAPI 路由，便于压锁脚本 race_media_checksum 直接调用。
"""
from __future__ import annotations

import sqlite3
import threading
import time

from fastapi import HTTPException

from audit_svc import write_audit, write_history
from db import connect, rows_to_list

# 互斥名逐字固定（题面指定），禁止改名。
MEDIA_LOCK_KEY = "媒资桥置可播互斥"

CHECKSUMS_MATCH = "match"
CHECKSUMS_MISMATCH = "checksum_mismatch"
CHECKSUMS_MISSING = "checksum_missing"

CS_UNVERIFIED = "unverified"
CS_VERIFIED = "verified"
CS_MISMATCH = "mismatch"

CS_LABEL = {
    CS_UNVERIFIED: "未校验",
    CS_VERIFIED: "校验一致",
    CS_MISMATCH: "校验不一致",
}

# 联查“当前校验和存在一致台账”的谓词片段（调用处别名固定为 a=assets）。
# 可播的唯一账实口径：正式状态 ready + checksum_status=verified + 存在与当前
# assets.checksum 相符的 matched 媒资桥结果。旁路写入的 ready 脏行因缺台账而不可播。
PLAYABLE_SQL = (
    "(a.status='ready' AND COALESCE(a.checksum_status,'unverified')='verified' "
    "AND TRIM(COALESCE(a.checksum,'')) <> '' "
    "AND EXISTS (SELECT 1 FROM media_verifications v "
    "            WHERE v.asset_id=a.id AND v.matched=1 "
    "            AND LOWER(TRIM(v.remote_checksum))=LOWER(TRIM(a.checksum))))"
)

_proc_locks_guard = threading.Lock()
_proc_locks: dict[str, threading.Lock] = {}


def proc_lock(key: str = MEDIA_LOCK_KEY) -> threading.Lock:
    """进程内命名锁（单 uvicorn worker / 单 pytest 进程下的双会话线程互斥）。"""
    with _proc_locks_guard:
        lk = _proc_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _proc_locks[key] = lk
        return lk


class MediaLockBusy(Exception):
    """争用败方：互斥被他会话持有。路由映射 409，败方不得有任何正式落库。"""

    def __init__(self, message: str, holder: str | None = None):
        super().__init__(message)
        self.holder = holder
        self.message = message


class MediaChecksumMismatch(Exception):
    """校验失败：结果已同事务落账并把素材压回 draft，但 ready 写入被拒（路由映射 409）。"""

    def __init__(self, result: dict):
        super().__init__(result.get("detail") or "校验和不一致")
        self.result = result


def ensure_media_schema(conn) -> None:
    """幂等补齐 signboard-13 所需结构（不改 db.py）。可在任意事务/连接内调用。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(assets)").fetchall()}
    if "checksum_status" not in cols:
        try:
            conn.execute(
                "ALTER TABLE assets ADD COLUMN checksum_status TEXT NOT NULL DEFAULT 'unverified'"
            )
        except sqlite3.OperationalError as exc:  # 并发下他连接已加列
            if "duplicate column" not in str(exc).lower():
                raise
    if "checksum_verified_at" not in cols:
        try:
            conn.execute("ALTER TABLE assets ADD COLUMN checksum_verified_at REAL")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_verifications(
          id INTEGER PRIMARY KEY,
          asset_id INTEGER NOT NULL REFERENCES assets(id),
          source TEXT NOT NULL DEFAULT 'media_bridge',
          holder TEXT NOT NULL DEFAULT '',
          attempt_id TEXT NOT NULL DEFAULT '',
          local_checksum TEXT NOT NULL DEFAULT '',
          remote_checksum TEXT NOT NULL DEFAULT '',
          matched INTEGER NOT NULL DEFAULT 0,
          reason TEXT NOT NULL DEFAULT '',
          status_snapshot TEXT NOT NULL DEFAULT '',
          lock_key TEXT NOT NULL DEFAULT '',
          created_at REAL NOT NULL
        )
        """
    )
    mv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(media_verifications)").fetchall()}
    if "status_snapshot" not in mv_cols:
        try:
            conn.execute(
                "ALTER TABLE media_verifications ADD COLUMN status_snapshot TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_verif_asset ON media_verifications(asset_id, id DESC)"
    )


_schema_init_lock = threading.Lock()
_schema_initialized: set[str] = set()


def init_schema(force: bool = False) -> None:
    """独立连接提交 DDL；每个 DB 路径进程内只初始化一次（测试按用例换临时库）。"""
    import db as _db

    key = str(getattr(_db, "DB_PATH", ""))
    with _schema_init_lock:
        if not force and key and key in _schema_initialized:
            return
        with connect() as conn:
            ensure_media_schema(conn)
            conn.commit()
        if key:
            _schema_initialized.add(key)


def compare_checksums(local: str | None, remote: str | None) -> tuple[bool, str]:
    left = (local or "").strip()
    right = (remote or "").strip()
    if not left or not right:
        return False, CHECKSUMS_MISSING
    if left == right or left.lower() == right.lower():
        return True, CHECKSUMS_MATCH
    return False, CHECKSUMS_MISMATCH


def _acquire_db_lock_row(conn, holder: str, ttl_sec: int = 30) -> None:
    """在业务事务内原子夺取命名行锁；他人有效持锁则 MediaLockBusy。

    与 locks.acquire 共用 write_locks 表与同一个键，因此 /api/locks 外部持锁同样生效。
    """
    now = time.time()
    conn.execute(
        """
        INSERT INTO write_locks(lock_key,holder,expires_at) VALUES(?,?,?)
        ON CONFLICT(lock_key) DO UPDATE
          SET holder=excluded.holder, expires_at=excluded.expires_at
        WHERE write_locks.holder=? OR write_locks.expires_at<=?
        """,
        (MEDIA_LOCK_KEY, holder, now + ttl_sec, holder, now),
    )
    row = conn.execute(
        "SELECT holder FROM write_locks WHERE lock_key=?", (MEDIA_LOCK_KEY,)
    ).fetchone()
    current = row["holder"] if row is not None else None
    if current != holder:
        raise MediaLockBusy(
            f"「{MEDIA_LOCK_KEY}」正被会话 {current or '其他会话'} 持有，败方不得置可播",
            holder=current,
        )


def current_match_exists(conn, asset_id: int, checksum: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM media_verifications
        WHERE asset_id=? AND matched=1
          AND LOWER(TRIM(remote_checksum))=LOWER(TRIM(?))
        LIMIT 1
        """,
        (asset_id, checksum or ""),
    ).fetchone()
    return row is not None


def playable_flags(asset: dict) -> dict:
    """纯字段推导（批量场景用 decorate_assets 补 match 存在性）。"""
    cs = asset.get("checksum_status") or CS_UNVERIFIED
    has_match = bool(asset.get("current_match"))
    verified = cs == CS_VERIFIED and has_match
    checksum = (asset.get("checksum") or "").strip()
    playable = asset.get("status") == "ready" and verified and bool(checksum)
    return {
        "checksum_status": cs,
        "checksum_status_label": CS_LABEL.get(cs, cs),
        "verified": verified,
        "checksum_verified": verified,
        "playable": playable,
        "is_playable": playable,
    }


def decorate_assets(rows: list[dict]) -> list[dict]:
    """批量补 checksum 校验状态 / verified / playable（联查 media_verifications）。"""
    if not rows:
        return rows
    init_schema()
    ids = [r["id"] for r in rows if r.get("id") is not None]
    match_ids: set[int] = set()
    with connect() as conn:
        ensure_media_schema(conn)
        if ids:
            placeholders = ",".join("?" for _ in ids)
            found = conn.execute(
                f"""
                SELECT DISTINCT v.asset_id AS aid
                FROM media_verifications v JOIN assets a ON a.id=v.asset_id
                WHERE v.matched=1
                  AND LOWER(TRIM(v.remote_checksum))=LOWER(TRIM(a.checksum))
                  AND v.asset_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
            match_ids = {r["aid"] for r in found}
    for r in rows:
        r["current_match"] = r.get("id") in match_ids
        r.update(playable_flags(r))
        r.pop("current_match", None)
    return rows


def verify_and_set_playable(
    *,
    asset_id: int | None = None,
    code: str | None = None,
    remote_checksum: str | None = None,
    expected_checksum: str | None = None,
    actor: str = "system",
    source: str = "media_bridge",
    attempt_id: str = "",
    hold_ms: float | int = 0,
    on_hold=None,
) -> dict:
    """媒资桥校验 + 置可播的唯一事务核心（甲路径 + 命名互斥）。

    成功（一致）返回 result；不一致在提交台账后抛 MediaChecksumMismatch；
    争用抛 MediaLockBusy（不产生任何写入）；素材不存在抛 404。
    on_hold：压锁测试钩子，在夺得锁/开启写事务后、提交前调用（用于确定性放大争用窗口）。
    hold_ms：HTTP 压锁缝，持锁内短暂睡眠（上限 2s）。
    """
    # 先独立提交结构，避免后续争用败方 ROLLBACK 把建表 DDL 一并撤销，导致“零行查询”无表。
    init_schema()
    lk = proc_lock()
    if not lk.acquire(blocking=False):
        raise MediaLockBusy(f"「{MEDIA_LOCK_KEY}」进程内已被占用")

    conn = None
    try:
        conn = connect()
        conn.isolation_level = None  # 手动事务，显式 BEGIN IMMEDIATE
        try:
            conn.execute("PRAGMA busy_timeout=0")
        except sqlite3.Error:
            pass

        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            # 跨进程压锁时，另一进程持写事务：busy_timeout=0 立即锁冲突，统一映射为互斥败方。
            raise MediaLockBusy(
                f"「{MEDIA_LOCK_KEY}」写事务争用，败方不得置可播（database is locked）",
            ) from exc

        result: dict | None = None
        try:
            ensure_media_schema(conn)
            _acquire_db_lock_row(conn, actor or "system")

            if asset_id is not None:
                asset = conn.execute(
                    "SELECT * FROM assets WHERE id=?", (asset_id,)
                ).fetchone()
            else:
                asset = conn.execute(
                    "SELECT * FROM assets WHERE code=?", (code,)
                ).fetchone()
            if asset is None:
                raise HTTPException(status_code=404, detail="素材不存在")
            aid = asset["id"]

            local = (expected_checksum or asset["checksum"] or "").strip()
            remote = ("" if remote_checksum is None else str(remote_checksum)).strip()
            matched, reason = compare_checksums(local, remote)
            now = time.time()
            snapshot_status = "ready" if matched else "draft"

            # 压锁缝：此时已持进程锁 + SQLite 写事务 + 命名行锁。
            if on_hold is not None:
                try:
                    on_hold()
                except Exception:
                    pass
            if hold_ms:
                time.sleep(min(max(float(hold_ms), 0.0), 2000.0) / 1000.0)

            cur = conn.execute(
                """
                INSERT INTO media_verifications(
                  asset_id,source,holder,attempt_id,local_checksum,remote_checksum,
                  matched,reason,status_snapshot,lock_key,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    aid,
                    source,
                    actor or "system",
                    attempt_id or "",
                    local,
                    remote,
                    1 if matched else 0,
                    reason,
                    snapshot_status,
                    MEDIA_LOCK_KEY,
                    now,
                ),
            )
            verif_id = cur.lastrowid

            if matched:
                conn.execute(
                    "UPDATE assets SET status='ready', checksum_status=?, checksum_verified_at=? WHERE id=?",
                    (CS_VERIFIED, now, aid),
                )
                write_history(
                    "media_bridge",
                    aid,
                    "verified_ready",
                    f"媒资桥校验和一致，素材置可播（{source}#{verif_id} {attempt_id}）".strip(),
                    actor,
                    conn=conn,
                )
                write_audit(
                    actor,
                    "media_bridge.verified_ready",
                    {
                        "asset_id": aid,
                        "verification_id": verif_id,
                        "attempt_id": attempt_id,
                        "lock_key": MEDIA_LOCK_KEY,
                        "checksum": local,
                    },
                    conn=conn,
                )
                result = {
                    "ok": True,
                    "matched": True,
                    "verified": True,
                    "playable": True,
                    "status": "ready",
                    "checksum_status": CS_VERIFIED,
                    "asset_id": aid,
                    "verification_id": verif_id,
                    "attempt_id": attempt_id,
                    "lock_key": MEDIA_LOCK_KEY,
                    "path": "jia_reject_ready",
                }
            else:
                # 甲路径：整笔拒写 ready，正式状态压回/保持 draft+mismatch（正式可播零增）。
                conn.execute(
                    "UPDATE assets SET status='draft', checksum_status=?, checksum_verified_at=NULL WHERE id=?",
                    (CS_MISMATCH, aid),
                )
                write_history(
                    "media_bridge",
                    aid,
                    "checksum_mismatch",
                    f"媒资桥校验和{ '缺失' if reason==CHECKSUMS_MISSING else '不一致' }，拒绝置可播并保持草稿（{source}#{verif_id} {attempt_id}）".strip(),
                    actor,
                    conn=conn,
                )
                write_audit(
                    actor,
                    "media_bridge.checksum_mismatch",
                    {
                        "asset_id": aid,
                        "verification_id": verif_id,
                        "attempt_id": attempt_id,
                        "reason": reason,
                        "local_checksum": local,
                        "remote_checksum": remote,
                        "lock_key": MEDIA_LOCK_KEY,
                    },
                    conn=conn,
                )
                result = {
                    "ok": False,
                    "matched": False,
                    "verified": False,
                    "playable": False,
                    "status": "draft",
                    "checksum_status": CS_MISMATCH,
                    "reason": reason,
                    "asset_id": aid,
                    "verification_id": verif_id,
                    "attempt_id": attempt_id,
                    "lock_key": MEDIA_LOCK_KEY,
                    "path": "jia_reject_ready",
                    "detail": (
                        "校验和缺失，无法核验，已拒绝置可播"
                        if reason == CHECKSUMS_MISSING
                        else "校验和不一致，已整笔拒绝置可播（甲路径：正式素材保持草稿）"
                    ),
                }

            # 命名行锁随业务事务一起释放（不留跨请求残留，避免误伤后续他会话正常重试）。
            conn.execute(
                "DELETE FROM write_locks WHERE lock_key=? AND holder=?",
                (MEDIA_LOCK_KEY, actor or "system"),
            )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        if not result["matched"]:
            raise MediaChecksumMismatch(result)
        return result
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        lk.release()


def list_verifications(asset_id: int | None = None, limit: int = 100) -> list[dict]:
    init_schema()
    with connect() as conn:
        ensure_media_schema(conn)
        sql = """
            SELECT v.*, a.code AS asset_code, a.title AS asset_title,
                   a.status AS asset_status, a.checksum AS current_checksum,
                   COALESCE(a.checksum_status,'unverified') AS asset_checksum_status
            FROM media_verifications v
            JOIN assets a ON a.id=v.asset_id
        """
        params: list = []
        if asset_id is not None:
            sql += " WHERE v.asset_id=?"
            params.append(asset_id)
        sql += " ORDER BY v.id DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        return rows_to_list(conn.execute(sql, params).fetchall())
