#!/usr/bin/env python3
"""压锁脚本：调用 race_media_checksum 对「媒资桥置可播互斥」做多轮双路争用压测。

用法：
    python scripts/race_media_checksum.py [rounds]

退出码 0 表示每轮恰一成一败、败方零正式落库；否则 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from race_media_checksum import race_media_checksum  # noqa: E402


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    report = race_media_checksum(rounds=rounds)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = [d["round"] for d in report["details"] if not (
        d["winners"] == 1 and d["busy_losers"] == 1 and d["loser_formal_rows"] == 0
    )]
    print(
        f"\n[压锁] 互斥名={report['lock_key']} 轮数={rounds} "
        f"总判定={'PASS' if report['ok'] else 'FAIL'} 异常轮={failed}"
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
