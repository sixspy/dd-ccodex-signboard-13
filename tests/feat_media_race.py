"""轨道 C（signboard-13）：「媒资桥置可播互斥」双会话争用。

旧实现没有这把锁，两路置可播会无锁双成。补齐后：
- 任一会话经 /api/locks 持锁，另一会话的媒资桥置可播必须 409 且零正式落库；
- race_media_checksum 多轮并发压锁，每轮恰一成一败，败方零正式。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PYTHONPATH", str(ROOT / "backend"))

import db as dbmod  # noqa: E402
from db import init_db  # noqa: E402
from app import app  # noqa: E402

MEDIA_LOCK_KEY = "媒资桥置可播互斥"


def race_media_checksum(rounds: int = 3, **kwargs):
    """压锁入口：委托给 backend/race_media_checksum.py 的同名实现。

    放在本模块是为了让 `from feat_media_race import race_media_checksum` 也可取用。
    """
    from race_media_checksum import race_media_checksum as _impl

    return _impl(rounds=rounds, **kwargs)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    monkeypatch.setattr(dbmod, "DATA", tmp_path)
    if db.exists():
        db.unlink()
    init_db()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _matching_asset(client, h, code="race_asset_1", checksum="race-sum-1"):
    r = client.post(
        "/api/assets",
        headers=h,
        json={
            "code": code,
            "title": "争用素材",
            "kind": "image",
            "duration_sec": 10,
            "resolution": "1920x1080",
            "status": "draft",
            "checksum": checksum,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_mutex_constant_is_exact_named_lock():
    try:
        import media_svc  # noqa: F401
    except ModuleNotFoundError:
        assert False, "media_svc 尚未实现「媒资桥置可播互斥」"
    import media_svc

    assert media_svc.MEDIA_LOCK_KEY == MEDIA_LOCK_KEY


def test_second_session_rejected_while_named_lock_held(client):
    admin = _login(client, "admin", "admin123")
    writer = _login(client, "writer", "writer123")

    # 会话 A 持有全局命名互斥（长 TTL，不释放）
    lock = client.post(
        "/api/locks/acquire",
        headers=admin,
        json={"lock_key": MEDIA_LOCK_KEY, "ttl_sec": 30},
    )
    assert lock.status_code == 200, lock.text

    aid = _matching_asset(client, writer)

    # 会话 B 即便校验和一致，也必须被互斥挡下
    r = client.post(
        "/api/stubs/media_bridge",
        headers=writer,
        json={"asset_id": aid, "remote_checksum": "race-sum-1", "attempt_id": "c2-writer"},
    )
    assert r.status_code == 409, f"持锁期间败方必须 409，实际 {r.status_code}: {r.text}"

    a = client.get(f"/api/assets/{aid}", headers=writer).json()
    assert a["status"] == "draft", "争用败方不得推进正式状态"

    with dbmod.connect() as conn:
        v = conn.execute(
            "SELECT COUNT(*) c FROM media_verifications WHERE asset_id=? AND holder=?",
            (aid, "writer"),
        ).fetchone()["c"] if _table_exists(conn, "media_verifications") else 0
        au = conn.execute(
            "SELECT COUNT(*) c FROM audit_log WHERE actor=? AND payload LIKE ?",
            ("writer", "%c2-writer%"),
        ).fetchone()["c"]
    assert v == 0, "败方不得写入媒资核验台账"
    assert au == 0, "败方不得写成功审计（零正式落库）"


def test_patch_ready_route_is_also_mutex_gated(client):
    """复现“两路无锁双成”：持锁期间 PATCH 置可播同样必须失败。"""
    admin = _login(client, "admin", "admin123")
    writer = _login(client, "writer", "writer123")
    client.post(
        "/api/locks/acquire",
        headers=admin,
        json={"lock_key": MEDIA_LOCK_KEY, "ttl_sec": 30},
    )
    aid = _matching_asset(client, writer, code="race_asset_2")
    r = client.patch(
        f"/api/assets/{aid}",
        headers=writer,
        json={"status": "ready", "remote_checksum": "race-sum-1"},
    )
    assert r.status_code == 409, f"PATCH 置可播也须受命名互斥约束，实际 {r.status_code}"
    assert client.get(f"/api/assets/{aid}", headers=writer).json()["status"] == "draft"


def test_race_media_checksum_pressure_invariant():
    try:
        report = race_media_checksum(rounds=3)
    except ModuleNotFoundError as exc:  # 红阶段：实现尚未落地
        pytest.fail(f"race_media_checksum 压锁实现缺失：{exc}")
    assert report["lock_key"] == MEDIA_LOCK_KEY
    assert report["rounds"] == 3
    for i, rd in enumerate(report["details"]):
        assert rd["winners"] == 1, f"第 {i} 轮应恰一成，实际 {rd['winners']}"
        assert rd["busy_losers"] == 1, f"第 {i} 轮应恰一败，实际 {rd['busy_losers']}"
        assert rd["loser_formal_rows"] == 0, f"第 {i} 轮败方必须零正式落库"
    assert report["ok"] is True


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
