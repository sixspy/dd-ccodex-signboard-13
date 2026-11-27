#!/usr/bin/env python3
"""压锁脚本（别名入口）：调用 race_media_checksum 压「媒资桥置可播互斥」。

等价于 scripts/race_media_checksum.py [rounds]。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from race_media_checksum import race_media_checksum  # noqa: E402


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    report = race_media_checksum(rounds=rounds)
    print(f"[press_media_lock] lock={report['lock_key']} rounds={rounds} ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
