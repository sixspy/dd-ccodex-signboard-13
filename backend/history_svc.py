"""变更历史（signboard-13：支持按实体/动作过滤，追溯媒资校验置可播）。"""
from __future__ import annotations

from fastapi import APIRouter, Header

from auth import current_user
from db import connect, rows_to_list

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def list_history(
    limit: int = 100,
    entity_kind: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    authorization: str | None = Header(default=None),
):
    current_user(authorization)
    sql = "SELECT * FROM change_history WHERE 1=1"
    params: list = []
    if entity_kind:
        sql += " AND entity_kind=?"
        params.append(entity_kind)
    if action:
        sql += " AND action=?"
        params.append(action)
    if actor:
        sql += " AND actor=?"
        params.append(actor)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 300)))
    with connect() as conn:
        rows = rows_to_list(conn.execute(sql, params).fetchall())
    return {"items": rows}


@router.get("/media")
def media_history(limit: int = 100, authorization: str | None = Header(default=None)):
    """媒资核验/置可播轨迹（entity_kind=media_bridge）。"""
    current_user(authorization)
    with connect() as conn:
        rows = rows_to_list(
            conn.execute(
                "SELECT * FROM change_history WHERE entity_kind='media_bridge' "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 300)),),
            ).fetchall()
        )
    return {"items": rows}
