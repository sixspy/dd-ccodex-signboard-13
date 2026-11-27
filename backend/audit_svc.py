"""审计与辅助查询。

signboard-13：write_audit / write_history 增加可选 conn 参数。
传入调用方事务连接时只 INSERT、不自行 commit，从而让「媒资核验结果 + 素材状态 +
审计 + 变更历史」能够同事务跨表账实一致；不传 conn 时保持旧行为（独立连接提交）。
"""
from __future__ import annotations

import json
import time
from fastapi import APIRouter, Header

from auth import current_user
from db import connect, rows_to_list

router = APIRouter(prefix="/api/audit", tags=["audit"])


def write_audit(actor: str, action: str, payload: dict, conn=None) -> None:
    if conn is not None:
        conn.execute(
            "INSERT INTO audit_log(actor,action,payload,created_at) VALUES(?,?,?,?)",
            (actor, action, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        return
    with connect() as own:
        own.execute(
            "INSERT INTO audit_log(actor,action,payload,created_at) VALUES(?,?,?,?)",
            (actor, action, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        own.commit()


def write_history(
    entity_kind: str,
    entity_id: int,
    action: str,
    detail: str,
    actor: str,
    conn=None,
) -> None:
    if conn is not None:
        conn.execute(
            "INSERT INTO change_history(entity_kind,entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?,?)",
            (entity_kind, entity_id, action, detail, actor, time.time()),
        )
        return
    with connect() as own:
        own.execute(
            "INSERT INTO change_history(entity_kind,entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?,?)",
            (entity_kind, entity_id, action, detail, actor, time.time()),
        )
        own.commit()


@router.get("/recent")
def recent(limit: int = 50, authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
    return {"items": rows_to_list(rows)}
