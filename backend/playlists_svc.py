"""播放清单（signboard-13：齐套判定联查媒资核验，禁止只看 status 的假绿）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException

import media_svc
from audit_svc import write_history
from auth import current_user, require_writer
from db import connect, rows_to_list
from policies import assert_asset_ready
from validators import require_code, require_playlist_status, require_positive_int, require_title, require_transition

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _completeness_map(conn) -> dict[int, dict]:
    """一次分组联查每个清单的条目总数与“真可播”数（PLAYABLE_SQL 跨表核验）。"""
    rows = conn.execute(
        f"""
        SELECT i.playlist_id AS pid,
               COUNT(*) AS total,
               SUM(CASE WHEN {media_svc.PLAYABLE_SQL} THEN 1 ELSE 0 END) AS playable
        FROM playlist_items i
        JOIN assets a ON a.id = i.asset_id
        GROUP BY i.playlist_id
        """
    ).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        total = r["total"] or 0
        playable = r["playable"] or 0
        out[r["pid"]] = {"total": total, "playable": playable}
    return out


def _attach_completeness(row: dict, total: int, playable: int) -> dict:
    unplayable = max(total - playable, 0)
    complete = total >= 1 and unplayable == 0
    row["item_count"] = total
    row["playable_count"] = playable
    row["verified_count"] = playable
    row["ready_item_count"] = playable
    row["unplayable_count"] = unplayable
    row["unverified_count"] = unplayable
    row["bad_count"] = unplayable
    row["complete"] = complete
    row["is_complete"] = complete
    row["playable_complete"] = complete
    row["all_verified"] = complete
    row["all_playable"] = complete
    row["qitao"] = complete
    row["qitao_label"] = "可播齐套" if complete else ("空清单" if total == 0 else "未齐套（含未校验/核败素材）")
    row["fake_ready"] = (row.get("status") == "ready") and not complete
    row["fake_green"] = row["fake_ready"]
    return row


@router.get("")
def list_playlists(authorization: str | None = Header(default=None)):
    current_user(authorization)
    media_svc.init_schema()
    with connect() as conn:
        rows = rows_to_list(conn.execute("SELECT * FROM playlists ORDER BY id DESC").fetchall())
        comp = _completeness_map(conn)
        for r in rows:
            c = comp.get(r["id"], {"total": 0, "playable": 0})
            _attach_completeness(r, c["total"], c["playable"])
    return {"items": rows}


@router.get("/{playlist_id}")
def get_playlist(playlist_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization)
    media_svc.init_schema()
    with connect() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not pl:
            raise HTTPException(404, "清单不存在")
        pl_row = dict(pl)
        items = rows_to_list(
            conn.execute(
                f"""
                SELECT i.*, a.code AS asset_code, a.title AS asset_title, a.kind, a.resolution,
                       a.status AS asset_status, a.status AS status, a.checksum AS checksum,
                       COALESCE(a.checksum_status,'unverified') AS checksum_status,
                       CASE WHEN {media_svc.PLAYABLE_SQL} THEN 1 ELSE 0 END AS playable
                FROM playlist_items i
                JOIN assets a ON a.id = i.asset_id
                WHERE i.playlist_id=? ORDER BY i.seq
                """,
                (playlist_id,),
            ).fetchall()
        )
        for it in items:
            it["playable"] = bool(it.get("playable"))
            it["is_playable"] = it["playable"]
            it["verified"] = it["playable"]
            it["checksum_verified"] = it["playable"]
            cs = it.get("checksum_status") or media_svc.CS_UNVERIFIED
            it["checksum_status_label"] = media_svc.CS_LABEL.get(cs, cs)
        playable = sum(1 for it in items if it["playable"])
        _attach_completeness(pl_row, len(items), playable)

    return {
        "playlist": pl_row,
        "items": items,
        "complete": pl_row["complete"],
        "is_complete": pl_row["complete"],
        "playable_complete": pl_row["complete"],
        "playable_count": pl_row["playable_count"],
        "unplayable_count": pl_row["unplayable_count"],
        "fake_ready": pl_row["fake_ready"],
    }


@router.get("/{playlist_id}/readiness")
def playlist_readiness(playlist_id: int, authorization: str | None = Header(default=None)):
    """齐套判定摘要（供清单页/发布前联查）：complete 仅当所有条目账实可播。"""
    current_user(authorization)
    media_svc.init_schema()
    with connect() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not pl:
            raise HTTPException(404, "清单不存在")
        c = _completeness_map(conn).get(playlist_id, {"total": 0, "playable": 0})
        row = _attach_completeness(dict(pl), c["total"], c["playable"])
    return {
        "playlist_id": playlist_id,
        "complete": row["complete"],
        "playable_complete": row["complete"],
        "item_count": row["item_count"],
        "playable_count": row["playable_count"],
        "unplayable_count": row["unplayable_count"],
        "fake_ready": row["fake_ready"],
        "qitao_label": row["qitao_label"],
    }


@router.post("")
def create_playlist(body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    code = require_code(body.get("code"), "清单编码")
    title = require_title(body.get("title"), "清单标题")
    status = require_playlist_status(body.get("status") or "draft")
    transition = require_transition(body.get("transition") or "cut")
    loop_flag = 1 if body.get("loop", True) else 0
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO playlists(code,title,status,transition,loop_flag,owner,created_at) VALUES(?,?,?,?,?,?,?)",
                (code, title, status, transition, loop_flag, user["username"], time.time()),
            )
            pid = cur.lastrowid
            write_history("playlist", pid, "create", f"创建清单 {code}", user["username"], conn=conn)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(409, f"清单编码冲突: {exc}") from exc
    from audit_svc import write_audit

    write_audit(user["username"], "playlist.create", {"id": pid, "code": code})
    return {"id": pid, "code": code}


@router.post("/{playlist_id}/items")
def add_item(playlist_id: int, body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    asset_id = require_positive_int(body.get("asset_id"), "素材编号")
    seq = require_positive_int(body.get("seq"), "序号", 1, 999)
    dwell = require_positive_int(body.get("dwell_sec", 10), "停留秒", 1, 3600)
    media_svc.init_schema()
    with connect() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not pl:
            raise HTTPException(404, "清单不存在")
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "素材不存在")
        assert_asset_ready(asset["status"])
        try:
            conn.execute(
                "INSERT INTO playlist_items(playlist_id,asset_id,seq,dwell_sec) VALUES(?,?,?,?)",
                (playlist_id, asset_id, seq, dwell),
            )
            write_history(
                "playlist",
                playlist_id,
                "add_item",
                f"加入素材 {asset_id} seq={seq}",
                user["username"],
                conn=conn,
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(409, f"序号冲突: {exc}") from exc

        asset_d = dict(asset)
    media_svc.decorate_assets([asset_d])
    warning = None
    if not asset_d.get("playable"):
        warning = "该素材台账虽为 ready，但校验和未核验一致，清单暂不判定为可播齐套"
    return {"ok": True, "asset_playable": bool(asset_d.get("playable")), "warning": warning}


@router.patch("/{playlist_id}")
def patch_playlist(playlist_id: int, body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    media_svc.init_schema()
    with connect() as conn:
        pl = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if not pl:
            raise HTTPException(404, "清单不存在")
        status = require_playlist_status(body.get("status", pl["status"]))
        title = require_title(body.get("title", pl["title"]), "清单标题")
        transition = require_transition(body.get("transition", pl["transition"]))
        if status == "ready":
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM playlist_items WHERE playlist_id=?",
                (playlist_id,),
            ).fetchone()["c"]
            if cnt < 1:
                raise HTTPException(409, "空清单不可置为可播")
        conn.execute(
            "UPDATE playlists SET title=?, status=?, transition=? WHERE id=?",
            (title, status, transition, playlist_id),
        )
        write_history(
            "playlist",
            playlist_id,
            "patch",
            f"清单状态={status}",
            user["username"],
            conn=conn,
        )
        conn.commit()
    return {"ok": True}
