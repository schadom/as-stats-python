"""Request dependencies: database session, current user, role checks."""

from __future__ import annotations

import time
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import new_session
from ..models import ROLE_ADMIN, User, UserSession
from ..security import token_hash

COOKIE_NAME = "asstats_session"
TOUCH_INTERVAL = 300


def get_db() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _load_session(request: Request, db: Session) -> tuple[UserSession, User] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == token_hash(token))
    ).first()
    if row is None:
        return None
    sess, user = row
    now = int(time.time())
    if sess.expires_at < now or not user.is_active:
        return None
    if now - sess.last_seen > TOUCH_INTERVAL:
        sess.last_seen = now
        sess.expires_at = now + get_config().session_hours * 3600
    return sess, user


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    found = _load_session(request, db)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    return found[0]


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    found = _load_session(request, db)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    user = found[1]
    if user.must_change_password and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    request.state.user = user
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
