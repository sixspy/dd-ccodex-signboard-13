"""素材台账（signboard-13：置可播必须过校验和闸口，账实联查）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

import media_svc
from audit_svc import write_audit, write_history
from auth import current_user, require_writer
from db import connect, rows_to_list
from media_svc import MediaChecksumMismatch, MediaLockBusy
from models import ASSET_KIND_LABEL, ASSET_STATUS_LABEL, DEFAULT_DURATIONS
from validators import require_asset_status, require_code, require_kind, require_positive_int, require_title

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _enrich(row: dict) -> dict:
    row["kind_label"] = ASSET_KIND_LABEL.get(row["kind"], row["kind"])
    row["status_label"] = ASSET_STATUS_LABEL.get(row["status"], row["status"])
    cs = row.get("checksum_status") or media_svc.CS_UNVERIFIED
    row["checksum_status"] = cs
    row["checksum_status_label"] = media_svc.CS_LABEL.get(cs, cs)
    return row


def _core_error_response(exc: Exception):
    if isinstance(exc, MediaChecksumMismatch):
        return JSONResponse(status_code=409, content=exc.result)
    if isinstance(exc, MediaLockBusy):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "busy": True,
                "locked": True,
                "detail": exc.message,
                "holder": exc.holder,
                "lock_key": media_svc.MEDIA_LOCK_KEY,
            },
        )
    return None


_REMOTE_KEYS = (
    "remote_checksum",
    "bridge_checksum",
    "actual_checksum",
    "received_checksum",
    "remote_sha256",
)


def _remote_from(body: dict):
    # 注意：素材登记体里的裸 `checksum` 是“本地/期望校验和”，不能当成桥回传值自证一致，
    # 因此这里只认显式的 remote_* 字段（裸 checksum 别名仅在 stubs 桥入口兼容）。
    for n in _REMOTE_KEYS:
        if n in body and body[n] is not None:
            return body[n]
    return None


def _remote_present(body: dict) -> bool:
    return any(n in body for n in _REMOTE_KEYS)


@router.get("")
def list_assets(authorization: str | None = Header(default=None)):
    current_user(authorization)
    media_svc.init_schema()
    with connect() as conn:
        rows = rows_to_list(conn.execute("SELECT * FROM assets ORDER BY id DESC").fetchall())
    media_svc.decorate_assets(rows)
    return {"items": [_enrich(r) for r in rows]}


@router.get("/{asset_id}")
def get_asset(asset_id: int, authorization: str | None = Header(default=None)):
    current_user(authorization)
    media_svc.init_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "素材不存在")
    data = dict(row)
    media_svc.decorate_assets([data])
    return _enrich(data)


@router.post("")
def create_asset(body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    media_svc.init_schema()
    code = require_code(body.get("code"), "素材编码")
    title = require_title(body.get("title"), "素材标题")
    kind = require_kind(body.get("kind"))
    duration = require_positive_int(
        body.get("duration_sec", DEFAULT_DURATIONS.get(kind, 10)), "时长", 1, 3600
    )
    resolution = (body.get("resolution") or "1920x1080").strip()
    wanted_status = require_asset_status(body.get("status") or "draft")
    checksum = (body.get("checksum") or "").strip()

    # 新素材一律先以 draft/unverified 入库，避免“未核先 ready”。
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO assets(code,title,kind,duration_sec,resolution,status,owner,created_at,checksum,checksum_status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    code,
                    title,
                    kind,
                    duration,
                    resolution,
                    "draft",
                    user["username"],
                    time.time(),
                    checksum,
                    media_svc.CS_UNVERIFIED,
                ),
            )
            aid = cur.lastrowid
            write_history("asset", aid, "create", f"创建素材 {code}", user["username"], conn=conn)
            write_audit(
                user["username"], "asset.create", {"id": aid, "code": code}, conn=conn
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(409, f"素材编码冲突: {exc}") from exc

    remote = _remote_from(body)
    if remote is not None:
        # 内联校验：一致才置可播，不一致核心会压回 draft 并抛 mismatch。
        try:
            result = media_svc.verify_and_set_playable(
                asset_id=aid,
                remote_checksum=remote,
                expected_checksum=body.get("expected_checksum") or checksum,
                actor=user["username"],
                source="asset_create",
                attempt_id=str(body.get("attempt_id") or ""),
            )
        except (MediaChecksumMismatch, MediaLockBusy) as exc:
            resp = _core_error_response(exc)
            if resp is not None:
                return resp
            raise
        return {"id": aid, "code": code, **result}

    # 无校验回传：保留旧契约允许登记台账状态，但未核验者账实不可播。
    if wanted_status == "ready":
        with connect() as conn:
            conn.execute("UPDATE assets SET status='ready' WHERE id=?", (aid,))
            write_history(
                "asset",
                aid,
                "create_ready_unverified",
                f"登记即置 ready，但校验和未核验（账实不可播） {code}",
                user["username"],
                conn=conn,
            )
            conn.commit()
    return {"id": aid, "code": code}


@router.patch("/{asset_id}")
def patch_asset(asset_id: int, body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    media_svc.init_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise HTTPException(404, "素材不存在")
        row = dict(row)

    # 内联媒资校验置可播：走与媒资桥完全相同的互斥 + 同事务核心（禁止单点另起口径）。
    if _remote_present(body):
        try:
            result = media_svc.verify_and_set_playable(
                asset_id=asset_id,
                remote_checksum=_remote_from(body),
                expected_checksum=body.get("expected_checksum"),
                actor=user["username"],
                source="asset_patch",
                attempt_id=str(body.get("attempt_id") or ""),
                hold_ms=body.get("hold_ms") or 0,
            )
        except (MediaChecksumMismatch, MediaLockBusy) as exc:
            resp = _core_error_response(exc)
            if resp is not None:
                return resp
            raise
        # 附带的标题/时长修订
        title = require_title(body.get("title", row["title"]), "素材标题")
        duration = require_positive_int(
            body.get("duration_sec", row["duration_sec"]), "时长", 1, 3600
        )
        with connect() as conn:
            conn.execute(
                "UPDATE assets SET title=?, duration_sec=? WHERE id=?",
                (title, duration, asset_id),
            )
            conn.commit()
        return {"ok": True, **result}

    title = require_title(body.get("title", row["title"]), "素材标题")
    status = require_asset_status(body.get("status", row["status"]))
    duration = require_positive_int(
        body.get("duration_sec", row["duration_sec"]), "时长", 1, 3600
    )

    # 已有在案校验失败：甲路径下任何重新置可播都必须先校验一致，直接拒绝。
    if status == "ready" and (row.get("checksum_status") or "") == media_svc.CS_MISMATCH:
        raise HTTPException(409, "该校验和不一致在案，禁止置可播；请重新经媒资桥校验一致")

    with connect() as conn:
        conn.execute(
            "UPDATE assets SET title=?, status=?, duration_sec=? WHERE id=?",
            (title, status, duration, asset_id),
        )
        write_history(
            "asset",
            asset_id,
            "patch",
            f"更新素材状态={status}",
            user["username"],
            conn=conn,
        )
        conn.commit()
    write_audit(user["username"], "asset.patch", {"id": asset_id, "status": status})
    return {"ok": True}


@router.post("/{asset_id}/verify")
@router.post("/{asset_id}/checksum")
def verify_asset(asset_id: int, body: dict | None = None, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    body = body or {}
    try:
        return media_svc.verify_and_set_playable(
            asset_id=asset_id,
            remote_checksum=_remote_from(body),
            expected_checksum=body.get("expected_checksum"),
            actor=user["username"],
            source="asset_verify",
            attempt_id=str(body.get("attempt_id") or ""),
            hold_ms=body.get("hold_ms") or 0,
        )
    except (MediaChecksumMismatch, MediaLockBusy) as exc:
        resp = _core_error_response(exc)
        if resp is not None:
            return resp
        raise


@router.post("/{asset_id}/ready")
@router.post("/{asset_id}/playable")
def set_asset_ready(asset_id: int, body: dict | None = None, authorization: str | None = Header(default=None)):
    """显式“置可播”闸口：要么带 remote_checksum 当场校验，要么必须已存在与当前 checksum 相符的核验。"""
    user = current_user(authorization)
    require_writer(user)
    body = body or {}

    if _remote_from(body) is not None:
        return verify_asset(asset_id, body, authorization)

    media_svc.init_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise HTTPException(404, "素材不存在")
        data = dict(row)
    media_svc.decorate_assets([data])
    if not data.get("playable"):
        raise HTTPException(
            409,
            "校验和尚未核验一致，不可置可播；请先经媒资桥校验（一致后才可播）",
        )
    return {"ok": True, "asset_id": asset_id, "status": "ready", "playable": True}
