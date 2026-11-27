"""轨道 B（signboard-13）：清单页不得把未校验素材当成「可播齐套」。

旧实现只看 assets.status='ready' 与条目数，ready 但校验和未核/核败的素材被算成齐套，
是为假绿。补齐后清单服务联查媒资核验台账，给出 complete / playable_count / fake_ready。
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


def _h(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _asset(client, h, code, checksum):
    r = client.post(
        "/api/assets",
        headers=h,
        json={
            "code": code,
            "title": "齐套素材",
            "kind": "image",
            "duration_sec": 10,
            "resolution": "1920x1080",
            "status": "draft",
            "checksum": checksum,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _playlist(client, h, code):
    r = client.post(
        "/api/playlists",
        headers=h,
        json={"code": code, "title": "齐套清单", "status": "draft"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _raw_ready_unverified(aid):
    """夹具：历史/旁路写入的 ready 素材，没有任何校验一致台账。"""
    with dbmod.connect() as conn:
        conn.execute("UPDATE assets SET status='ready' WHERE id=?", (aid,))
        conn.commit()


def _raw_item(pid, aid, seq):
    with dbmod.connect() as conn:
        conn.execute(
            "INSERT INTO playlist_items(playlist_id,asset_id,seq,dwell_sec) VALUES(?,?,?,?)",
            (pid, aid, seq, 10),
        )
        conn.commit()


def _verified_asset(client, h, code, checksum):
    aid = _asset(client, h, code, checksum)
    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": aid, "remote_checksum": checksum},
    )
    assert r.status_code == 200, r.text
    return aid


def test_playlist_with_unverified_ready_asset_is_not_complete(client):
    h = _h(client)
    good = _verified_asset(client, h, "plb_good", "sum-good")
    fake = _asset(client, h, "plb_fake", "sum-fake")
    _raw_ready_unverified(fake)

    pid = _playlist(client, h, "pl_b_mix")
    _raw_item(pid, good, 1)
    _raw_item(pid, fake, 2)
    r = client.patch(f"/api/playlists/{pid}", headers=h, json={"status": "ready"})
    assert r.status_code == 200, r.text

    rows = client.get("/api/playlists", headers=h).json()["items"]
    row = next(p for p in rows if p["id"] == pid)

    assert row["item_count"] == 2
    assert row.get("playable_count") == 1
    assert row.get("unplayable_count") == 1
    # 多个近义字段，锁住“齐套”判定
    for key in ("complete", "is_complete", "playable_complete", "all_verified"):
        assert row.get(key) is False, f"{key} 不应把未校验素材算成齐套"
    assert row.get("fake_ready") is True, "status=ready 但账实不齐，应标 fake_ready"


def test_playlist_detail_items_carry_playable_flags(client):
    h = _h(client)
    good = _verified_asset(client, h, "pld_good", "sum-g2")
    fake = _asset(client, h, "pld_fake", "sum-f2")
    _raw_ready_unverified(fake)

    pid = _playlist(client, h, "pl_d_mix")
    _raw_item(pid, good, 1)
    _raw_item(pid, fake, 2)

    data = client.get(f"/api/playlists/{pid}", headers=h).json()
    items = data["items"]
    by_asset = {i["asset_id"]: i for i in items}
    assert by_asset[good].get("playable") is True
    assert by_asset[good].get("verified") is True
    assert by_asset[fake].get("playable") is False
    assert by_asset[fake].get("verified") is False

    pl = data["playlist"]
    assert pl.get("complete") is False
    assert data.get("complete") is False


def test_verified_only_playlist_is_complete(client):
    h = _h(client)
    good = _verified_asset(client, h, "plok_good", "sum-ok")
    pid = _playlist(client, h, "pl_ok")
    _raw_item(pid, good, 1)

    row = next(p for p in client.get("/api/playlists", headers=h).json()["items"] if p["id"] == pid)
    assert row.get("complete") is True
    assert row.get("playable_count") == 1
    assert row.get("unplayable_count") == 0
    assert row.get("fake_ready") is False


def test_repro_b_playlist_page_treats_unverified_as_complete_on_old_code(client):
    """复现顺序中的“失败测乙”：旧清单只数条目/状态，把未校验素材当成可播齐套。

    完全不依赖媒资桥：两素材都是台账 ready 但零校验记录（夹具「校验和不一致仍置可播」同款脏状态）。
    红阶段清单读模型没有 playable/complete 联查字段，齐套假象未被揭穿。
    """
    h = _h(client)
    a1 = _asset(client, h, "plr_a1", "sum-r1")
    a2 = _asset(client, h, "plr_a2", "sum-r2")
    _raw_ready_unverified(a1)
    _raw_ready_unverified(a2)

    pid = _playlist(client, h, "pl_repro_b")
    _raw_item(pid, a1, 1)
    _raw_item(pid, a2, 2)
    client.patch(f"/api/playlists/{pid}", headers=h, json={"status": "ready"})

    data = client.get(f"/api/playlists/{pid}", headers=h).json()
    for it in data["items"]:
        assert it.get("playable") is False, "未校验素材不得被判为可播"
    pl = data["playlist"]
    assert pl.get("complete") is False, "含未校验素材的清单不得算可播齐套"
    assert pl.get("fake_ready") is True


def test_mismatch_asset_referenced_by_playlist_is_unplayable(client):
    h = _h(client)
    bad = _asset(client, h, "plb_bad", "sum-local")
    r = client.post(
        "/api/stubs/media_bridge",
        headers=h,
        json={"asset_id": bad, "remote_checksum": "sum-DIFF"},
    )
    assert r.status_code == 409
    # 即便旁路把它再置 ready，联查仍判定不可播
    _raw_ready_unverified(bad)

    pid = _playlist(client, h, "pl_b_bad")
    _raw_item(pid, bad, 1)
    data = client.get(f"/api/playlists/{pid}", headers=h).json()
    assert data["items"][0].get("playable") is False
    assert data["playlist"].get("complete") is False
