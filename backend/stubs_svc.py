"""具名空桩与 signboard-13 媒资桥实现。

- remote_push 仍固定 501（题面空桩，保持不变）。
- media_bridge 不再是空成功：执行 checksum 校验 + 命名互斥 + 同事务跨表置可播（甲路径）。
"""
from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

import media_svc
from auth import current_user, require_writer
from media_svc import (
    MEDIA_LOCK_KEY,
    MediaChecksumMismatch,
    MediaLockBusy,
)

router = APIRouter(prefix="/api/stubs", tags=["stubs"])

STUB_META = {
    "remote_push": {
        "title": "远程推屏桥",
        "desc": "将正式发布推送到边缘播放器集群（空桩）",
    },
    "media_bridge": {
        "title": "媒资中台桥接",
        "desc": "从媒资中台同步素材元数据并校验校验和，一致才可置可播",
    },
}


@router.get("")
def list_stubs(authorization: str | None = Header(default=None)):
    current_user(authorization)
    items = []
    for k, v in STUB_META.items():
        if k == "media_bridge":
            status = "checksum_gated"
        else:
            status = "not_implemented"
        items.append({"key": k, **v, "status": status})
    return {"items": items, "media_lock_key": MEDIA_LOCK_KEY}


def _first(body: dict, *names: str):
    for n in names:
        if n in body and body[n] is not None:
            return body[n]
    return None


def _has_asset_ref(body: dict) -> bool:
    return any(
        body.get(k) is not None for k in ("asset_id", "id", "asset", "code", "asset_code")
    )


def _run_bridge(body: dict, user: dict):
    aid = _first(body, "asset_id", "id", "asset")
    code = _first(body, "code", "asset_code")
    try:
        return media_svc.verify_and_set_playable(
            asset_id=int(aid) if aid is not None else None,
            code=str(code) if code is not None else None,
            remote_checksum=_first(
                body,
                "remote_checksum",
                "checksum",
                "bridge_checksum",
                "actual_checksum",
                "received_checksum",
                "remote_sha256",
            ),
            expected_checksum=_first(body, "expected_checksum", "local_checksum"),
            actor=user["username"],
            source="media_bridge",
            attempt_id=str(_first(body, "attempt_id", "request_id") or ""),
            hold_ms=_first(body, "hold_ms") or 0,
        )
    except MediaChecksumMismatch as exc:
        return JSONResponse(status_code=409, content=exc.result)
    except MediaLockBusy as exc:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "busy": True,
                "locked": True,
                "detail": exc.message,
                "holder": exc.holder,
                "lock_key": MEDIA_LOCK_KEY,
            },
        )


@router.post("/remote_push")
def remote_push(body: dict, authorization: str | None = Header(default=None)):
    current_user(authorization)
    return JSONResponse(
        status_code=501,
        content={"ok": False, "stub": "remote_push", "detail": "远程推屏桥尚未实现", "body": body},
    )


def media_bridge(body: dict, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    # 空体 ping：表明桥已落地并回显契约，不产生写入。
    if not _has_asset_ref(body or {}):
        return {
            "ok": True,
            "stub": "media_bridge",
            "implemented": True,
            "lock_key": MEDIA_LOCK_KEY,
            "usage": "传入 asset_id 与 remote_checksum，校验一致才置 ready；不一致整笔拒写 ready。",
            "body": body,
        }
    return _run_bridge(body, user)


# 兼容别名（历史命名 media_bridge_entry）。
media_bridge_entry = media_bridge


# 主入口 + 近义路径，兼容不同调用命名。
router.add_api_route("/media_bridge", media_bridge, methods=["POST"])
router.add_api_route("/media_bridge/verify", media_bridge, methods=["POST"])
router.add_api_route("/media_bridge/sync", media_bridge, methods=["POST"])


@router.get("/media_bridge/results")
def media_bridge_results(
    asset_id: int | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    current_user(authorization)
    return {
        "items": media_svc.list_verifications(asset_id=asset_id, limit=limit),
        "lock_key": MEDIA_LOCK_KEY,
    }


@router.get("/media_bridge/verifications")
def media_bridge_verifications(
    asset_id: int | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    return media_bridge_results(asset_id=asset_id, limit=limit, authorization=authorization)


@router.post("/media_bridge/race-selftest")
def media_bridge_race_selftest(
    body: dict | None = None, authorization: str | None = Header(default=None)
):
    """在隔离临时库中跑 race_media_checksum 压锁自检，返回报告（不触碰正式库）。"""
    user = current_user(authorization)
    require_writer(user)
    from race_media_checksum import race_media_checksum

    rounds = int((body or {}).get("rounds") or 3)
    report = race_media_checksum(rounds=max(1, min(rounds, 20)))
    report["triggered_by"] = user["username"]
    return report


# ---- 顶层 /api/media/* 近义别名（同一套处理器），提升联查/同步入口可发现性 ----
media_root = APIRouter()
media_root.add_api_route("/api/media/verify", media_bridge, methods=["POST"])
media_root.add_api_route("/api/media/sync", media_bridge, methods=["POST"])
media_root.add_api_route("/api/media/ready", media_bridge, methods=["POST"])
media_root.add_api_route("/api/media/check", media_bridge, methods=["POST"])
media_root.add_api_route("/api/media/bridge", media_bridge, methods=["POST"])


@media_root.get("/api/media/verifications")
def _media_verifs(
    asset_id: int | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    return media_bridge_results(asset_id=asset_id, limit=limit, authorization=authorization)


@media_root.get("/api/media/results")
def _media_results(
    asset_id: int | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    return media_bridge_results(asset_id=asset_id, limit=limit, authorization=authorization)


# 直接把绝对路径路由挂进本 router 的路由表；app include 本 router 时会一并注册。
router.routes.extend(media_root.routes)
