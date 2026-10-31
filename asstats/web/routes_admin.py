"""Administrative actions that are not configuration: the danger zone."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models import User
from .deps import require_admin

router = APIRouter()

CONFIRM_WORD = "DELETE"


class ResetIn(BaseModel):
    confirm: str


@router.post("/statistics/reset")
def reset_statistics(body: ResetIn, _: User = Depends(require_admin)):
    """Delete every collected traffic counter. Exporters, links, users, settings and AS names stay."""
    from ..db import get_engine
    from ..services.reset import reset_statistics as reset
    from .routes_stats import _top_cache

    if body.confirm != CONFIRM_WORD:
        raise HTTPException(400, f"type {CONFIRM_WORD} to confirm")
    deleted = reset(get_engine())
    with _top_cache.lock:
        _top_cache.data.clear()
    return {"deleted": sum(deleted.values()), "tables": deleted}
