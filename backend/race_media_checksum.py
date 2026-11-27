"""「媒资桥置可播互斥」压锁入口 race_media_checksum。

自开临时库，多轮并发两路执行“校验一致后置可播”，用钩子确定性放大争用窗口，
断言每轮恰一成、一败（409/busy），且败方零正式落库（无核验行/审计/历史）。

可被三处调用：
- tests/feat_media_race.py：from race_media_checksum import race_media_checksum
- scripts/race_media_checksum.py：压锁脚本调用本函数
- python backend/race_media_checksum.py [rounds]
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path


def _one_round(dbmod, media_svc, index: int, hold_timeout: float = 5.0) -> dict:
    code = f"race_r{index}_{time.time_ns() % 10_000_000}"
    checksum = f"race-sum-{index}-{time.time_ns() % 100000}"
    holder_a = f"race-a-{index}"
    holder_b = f"race-b-{index}"

    with dbmod.connect() as conn:
        cur = conn.execute(
            "INSERT INTO assets(code,title,kind,duration_sec,resolution,status,owner,"
            "created_at,checksum,checksum_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                code,
                "争用素材",
                "image",
                10,
                "1920x1080",
                "draft",
                "race-setup",
                time.time(),
                checksum,
                media_svc.CS_UNVERIFIED,
            ),
        )
        aid = cur.lastrowid
        conn.commit()

    start_barrier = threading.Barrier(2)
    winner_entered = threading.Event()
    release_winner = threading.Event()
    outcomes: dict[str, tuple[str, dict]] = {}
    outcomes_lock = threading.Lock()
    one_done = threading.Event()

    def run(which: str, holder: str):
        attempt = f"att-{which}-{index}"

        def on_hold():
            winner_entered.set()
            # 持锁阻塞，直到主线程确认败方已经撞上互斥。
            release_winner.wait(timeout=hold_timeout)

        try:
            start_barrier.wait(timeout=hold_timeout)
            res = media_svc.verify_and_set_playable(
                asset_id=aid,
                remote_checksum=checksum,
                actor=holder,
                source="race_pressure",
                attempt_id=attempt,
                on_hold=on_hold,
            )
            kind, payload = "win", res
        except media_svc.MediaLockBusy as exc:
            kind, payload = "busy", {"detail": exc.message, "holder": exc.holder}
        except Exception as exc:  # noqa: BLE001 - 压锁要把任何异常计为 error
            kind, payload = "error", {"error": repr(exc)}
        with outcomes_lock:
            outcomes[which] = (kind, payload)
        one_done.set()

    ta = threading.Thread(target=run, args=("a", holder_a), daemon=True)
    tb = threading.Thread(target=run, args=("b", holder_b), daemon=True)
    ta.start()
    tb.start()

    # 胜方进入临界区并停在 on_hold；随后第一个结束的必然是撞锁的败方。
    winner_seen = winner_entered.wait(timeout=hold_timeout)
    # 等待败方落定（胜方在 release 前不会写 outcomes）。
    loser_seen = one_done.wait(timeout=hold_timeout)
    release_winner.set()
    ta.join(timeout=hold_timeout)
    tb.join(timeout=hold_timeout)

    with outcomes_lock:
        snapshot = dict(outcomes)

    winners = [w for w, (k, _) in snapshot.items() if k == "win"]
    busy_losers = [w for w, (k, _) in snapshot.items() if k == "busy"]
    errors = {w: p for w, (k, p) in snapshot.items() if k == "error"}

    loser_holder = None
    loser_formal_rows = 0
    if busy_losers:
        lw = busy_losers[0]
        loser_holder = holder_a if lw == "a" else holder_b
        with dbmod.connect() as conn:
            v = conn.execute(
                "SELECT COUNT(*) c FROM media_verifications WHERE asset_id=? AND holder=?",
                (aid, loser_holder),
            ).fetchone()["c"]
            h = conn.execute(
                "SELECT COUNT(*) c FROM change_history WHERE entity_id=? AND actor=?",
                (aid, loser_holder),
            ).fetchone()["c"]
            a = conn.execute(
                "SELECT COUNT(*) c FROM audit_log WHERE actor=? AND payload LIKE ?",
                (loser_holder, f"%att-{lw}-{index}%"),
            ).fetchone()["c"]
        loser_formal_rows = int(v) + int(h) + int(a)

    with dbmod.connect() as conn:
        asset = conn.execute("SELECT status, checksum_status FROM assets WHERE id=?", (aid,)).fetchone()
        verif_rows = conn.execute(
            "SELECT COUNT(*) c FROM media_verifications WHERE asset_id=? AND matched=1",
            (aid,),
        ).fetchone()["c"]

    detail = {
        "round": index,
        "asset_id": aid,
        "winner_seen_inside_lock": winner_seen,
        "loser_settled": loser_seen,
        "outcomes": {w: k for w, (k, _) in snapshot.items()},
        "winners": len(winners),
        "busy_losers": len(busy_losers),
        "errors": errors,
        "loser_holder": loser_holder,
        "loser_formal_rows": loser_formal_rows,
        "matched_verification_rows": verif_rows,
        "asset_status": asset["status"] if asset else None,
        "checksum_status": asset["checksum_status"] if asset else None,
    }
    return detail


def race_media_checksum(rounds: int = 5, workers: int = 2, db_path: str | Path | None = None) -> dict:
    """跑 rounds 轮双路争用，返回结构化报告；report['ok'] 为总不变量是否成立。"""
    import db as dbmod
    import media_svc

    rounds = max(1, int(rounds))
    tmpdir = None
    if db_path is None:
        tmpdir = tempfile.mkdtemp(prefix="race_media_ck_")
        target = Path(tmpdir) / "race.db"
    else:
        target = Path(db_path)

    saved_db, saved_data = dbmod.DB_PATH, dbmod.DATA
    dbmod.DB_PATH = target
    dbmod.DATA = target.parent
    report: dict = {
        "ok": False,
        "lock_key": media_svc.MEDIA_LOCK_KEY,
        "rounds": rounds,
        "workers": 2,
        "db_path": str(target),
        "details": [],
    }
    try:
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        dbmod.init_db()
        media_svc.init_schema()

        for i in range(rounds):
            report["details"].append(_one_round(dbmod, media_svc, i))

        details = report["details"]
        report["winner_count"] = sum(d["winners"] for d in details)
        report["loser_count"] = sum(d["busy_losers"] for d in details)
        report["busy_count"] = report["loser_count"]
        report["loser_formal_rows"] = sum(d["loser_formal_rows"] for d in details)
        report["formal_ready_count"] = sum(d["matched_verification_rows"] for d in details)
        report["race"] = "media_checksum"
        report["ok"] = all(
            d["winners"] == 1
            and d["busy_losers"] == 1
            and not d["errors"]
            and d["loser_formal_rows"] == 0
            and d["matched_verification_rows"] == 1
            and d["asset_status"] == "ready"
            and d["checksum_status"] == "verified"
            for d in report["details"]
        )
    finally:
        dbmod.DB_PATH = saved_db
        dbmod.DATA = saved_data
    return report


def _main(argv: list[str]) -> int:
    rounds = int(argv[1]) if len(argv) > 1 else 10
    report = race_media_checksum(rounds=rounds)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        f"[race_media_checksum] rounds={rounds} ok={report['ok']} "
        f"lock={report['lock_key']}"
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
