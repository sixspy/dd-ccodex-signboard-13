"""轨道 A（signboard-13）：媒资桥校验校验和，失败不得 ready（甲路径）。

红测口径：在未实现校验前，校验和不一致仍会被置/留为 ready，下列断言失败；
补齐媒资桥、素材、审计、历史服务的同事务跨表校验后转绿。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PYTHONPATH", str(ROOT / "backend"))

from db import init_db  # noqa: E402
import db as dbmod  # noqa: E402
from app import app  # noqa: E402

FIXTURE = ROOT / "fixtures" / "media_checksum_case.json"
MEDIA_LOCK_KEY = "媒资桥置可播互斥"


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


def _login(client, username="admin", password="admin123"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_asset(client, h, code="ck_a1", checksum="local-cksum-1", status="draft"):
    r = client.post(
        "/api/assets",
        headers=h,
        json={
            "code": code,
            "title": "校验轨道素材",
            "kind": "image",
            "duration_sec": 10,
            "resolution": "1920x1080",
            "status": status,
            "checksum": checksum,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _raw_set_ready(aid, checksum_status=None):
    """模拟历史脏数据 / 夹具：台账状态已是 ready，但未经媒资桥校验。"""
    with dbmod.connect() as conn:
        if checksum_status is not None:
            conn.execute(
                "UPDATE assets SET status='ready', checksum_status=? WHERE id=?",
                (checksum_status, aid),
            )
        else:
            conn.execute("UPDATE assets SET status='ready' WHERE id=?", (aid,))
        conn.commit()


def test_fixture_names_the_reproduction_case():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["case"] == "校验和不一致仍置可播"
    assert data["mutex_key"] == MEDIA_LOCK_KEY
    assert data["path_decision"] == "jia_reject_ready"
    assert data["mismatch_case"]["expect"]["status"] == "draft"


def test_mismatch_bridge_rejects_ready_and_keeps_draft(client):
    h = _login(client, "writer", "writer123")
    aid = _create_asset(client, h, code="ck_mm_1", checksum="local-deadbeef")

    before = client.get("/api/dashboard", headers=h).json()["ready_assets"]

    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": "remote-0badf00d", "attempt_id": "a-mm-1"},
    )
    # 甲口径：校验失败整笔拒写 ready —— 409 + 结构化判定
    assert r.status_code == 409, r.text
    body = r.json()
    assert body.get("matched") is False or body.get("verified") is False
    assert body.get("status") == "draft"

    a = client.get(f"/api/assets/{aid}", headers=h).json()
    assert a["status"] == "draft", "校验和不一致后素材正式状态必须是草稿，不得 ready"
    assert a.get("checksum_status") == "mismatch"
    assert a.get("playable") is False

    after = client.get("/api/dashboard", headers=h).json()["ready_assets"]
    assert after == before, "校验失败不得新增正式可播素材（正式状态零增）"


def test_mismatch_knocks_legacy_ready_row_back_to_draft(client):
    """夹具「校验和不一致仍置可播」：脏数据已 ready，核败同事务压回 draft。"""
    h = _login(client)
    aid = _create_asset(client, h, code="ck_mm_2", checksum="local-deadbeef")
    _raw_set_ready(aid)
    assert client.get(f"/api/assets/{aid}", headers=h).json()["status"] == "ready"

    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": "remote-0badf00d"},
    )
    assert r.status_code == 409, r.text
    a = client.get(f"/api/assets/{aid}", headers=h).json()
    assert a["status"] == "draft", "核败后不得继续 ready"
    assert a.get("playable") is False


def test_recorded_mismatch_blocks_any_ready_patch(client):
    h = _login(client, "writer", "writer123")
    aid = _create_asset(client, h, code="ck_mm_3", checksum="local-deadbeef")
    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": "nope-remote"},
    )
    assert r.status_code == 409, r.text

    p = client.patch(f"/api/assets/{aid}", headers=h, json={"status": "ready"})
    assert p.status_code == 409, "已有校验失败记录的素材禁止重新置可播"
    a = client.get(f"/api/assets/{aid}", headers=h).json()
    assert a["status"] != "ready"


def test_repro_a_inline_mismatch_leaves_formal_asset_ready_on_old_code(client):
    """复现顺序中的“失败测甲”：带不一致校验和要求置可播后，正式素材仍 ready（旧桩之过）。

    断言以“事后联查状态”为先，使红因正是『校验失败后素材正式仍 ready』。
    """
    h = _login(client, "writer", "writer123")
    aid = _create_asset(client, h, code="ck_repro_a", checksum="expected-sum-A")

    p = client.patch(
        f"/api/assets/{aid}",
        headers=h,
        json={"status": "ready", "remote_checksum": "DIFFERENT-sum-Z"},
    )
    a = client.get(f"/api/assets/{aid}", headers=h).json()
    # 先钉死账实状态：红阶段这里会看到 ready
    assert a["status"] == "draft", f"校验失败后素材正式仍 {a['status']}（缺口复现）"
    assert a.get("playable") is False
    assert p.status_code == 409


def test_match_allows_playable_and_gate_endpoint(client):
    h = _login(client, "writer", "writer123")
    aid = _create_asset(client, h, code="ck_ok_1", checksum="good-c0ffee")

    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": "good-c0ffee", "attempt_id": "a-ok-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("matched") is True and body.get("verified") is True
    assert body.get("status") == "ready"

    a = client.get(f"/api/assets/{aid}", headers=h).json()
    assert a["status"] == "ready"
    assert a.get("checksum_status") == "verified"
    assert a.get("verified") is True
    assert a.get("playable") is True

    # 已核验后，显式置可播闸口放行
    g = client.post(f"/api/assets/{aid}/ready", headers=h, json={})
    assert g.status_code == 200, g.text


def test_ready_gate_rejects_unverified_asset(client):
    h = _login(client, "writer", "writer123")
    aid = _create_asset(client, h, code="ck_unv_1", checksum="whatever")
    r = client.post(f"/api/assets/{aid}/ready", headers=h, json={})
    assert r.status_code == 409, "未校验一致不得置可播"
    assert client.get(f"/api/assets/{aid}", headers=h).json()["status"] == "draft"


def test_bridge_result_and_asset_state_are_cross_table_recorded(client):
    h = _login(client)
    aid = _create_asset(client, h, code="ck_x_1", checksum="x-local")
    # 先败
    assert (
        client.post(
            "/api/stubs/media_bridge",
            headers=h,
            json={"asset_id": aid, "remote_checksum": "x-bad"},
        ).status_code
        == 409
    )
    # 后成（重新同步一致）
    assert (
        client.post(
            "/api/stubs/media_bridge",
            headers=h,
            json={"asset_id": aid, "remote_checksum": "x-local"},
        ).status_code
        == 200
    )

    res = client.get("/api/stubs/media_bridge/results", headers=h).json()
    items = res["items"]
    mine = [v for v in items if v["asset_id"] == aid]
    assert len(mine) >= 2
    # 结果按 id 倒序：最新一条为一致成功，其上一条为不一致失败
    assert mine[0]["matched"] in (1, True)
    assert mine[0]["status_snapshot"] == "ready"
    assert mine[0]["asset_status"] == "ready"
    assert mine[1]["matched"] in (0, False)
    assert mine[1]["reason"] == "checksum_mismatch"
    # 核败当笔快照为 draft；联查带出的当前素材状态在重核一致后为 ready
    assert mine[1]["status_snapshot"] == "draft"
    assert mine[1]["asset_status"] == "ready"

    # 审计与变更历史都留下了媒资核验痕迹
    audit = client.get("/api/audit/recent?limit=200", headers=h).json()["items"]
    assert any("media_bridge" in (x.get("action") or "") for x in audit)
    hist = client.get("/api/history?limit=200", headers=h).json()["items"]
    assert any(x.get("entity_kind") == "media_bridge" and x.get("entity_id") == aid for x in hist)


def test_auth_still_enforced_on_media_bridge(client):
    # 未登录 401
    assert client.post("/api/stubs/media_bridge", json={}).status_code == 401
    h = _login(client, "viewer", "viewer123")
    aid = _create_asset(client, _login(client, "admin"), code="ck_auth_1")
    # 只读账号不可执行校验置可播
    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": "x"},
    )
    assert r.status_code == 403, r.text
