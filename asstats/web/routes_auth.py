"""Login/logout, own account and user management."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..models import ROLE_ADMIN, ROLES, User, UserSession
from ..security import hash_password, new_token, verify_password
from .deps import COOKIE_NAME, client_ip, current_session, current_user, get_db, require_admin

router = APIRouter()

MIN_PASSWORD = 8
_FAIL_WINDOW = 600
_FAIL_LIMIT = 10
_failures: dict[str, deque] = defaultdict(deque)


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "created_at": u.created_at,
        "last_login": u.last_login,
    }


def _throttled(key: str) -> bool:
    q = _failures[key]
    now = time.monotonic()
    while q and q[0] < now - _FAIL_WINDOW:
        q.popleft()
    return len(q) >= _FAIL_LIMIT


# ---------------------------------------------------------------- session


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if _throttled(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed logins, try again later")
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        _failures[ip].append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    cfg = get_config()
    now = int(time.time())
    token, hashed = new_token()
    db.add(
        UserSession(
            token_hash=hashed,
            user_id=user.id,
            created_at=now,
            expires_at=now + cfg.session_hours * 3600,
            last_seen=now,
            ip=ip[:64],
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
    )
    user.last_login = now
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=cfg.session_hours * 3600,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )
    return user_out(user)


@router.post("/auth/logout")
def logout(response: Response, sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    db.execute(delete(UserSession).where(UserSession.id == sess.id))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD, max_length=256)


@router.post("/auth/password")
def change_password(
    body: PasswordIn,
    sess: UserSession = Depends(current_session),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    user = db.get(User, user.id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is wrong")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "new password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # log out other sessions
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != sess.id))
    return user_out(user)


# ---------------------------------------------------------------- user management


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=MIN_PASSWORD, max_length=256)
    role: str = "readonly"
    must_change_password: bool = True


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=MIN_PASSWORD, max_length=256)
    must_change_password: bool | None = None


def _admin_count(db: Session, exclude: int | None = None) -> int:
    q = select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True))
    if exclude is not None:
        q = q.where(User.id != exclude)
    return db.execute(q).scalar_one()


@router.get("/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.execute(select(User).order_by(User.username)).scalars()]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(409, "username already exists")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        must_change_password=body.must_change_password,
        created_at=int(time.time()),
    )
    db.add(user)
    db.flush()
    return user_out(user)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    demoting = (body.role is not None and body.role != ROLE_ADMIN) or body.is_active is False
    if user.role == ROLE_ADMIN and demoting and _admin_count(db, exclude=user.id) == 0:
        raise HTTPException(400, "at least one active admin is required")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    if body.password:
        user.password_hash = hash_password(body.password)
        user.must_change_password = True if body.must_change_password is None else body.must_change_password
        if user.id != admin.id:
            db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    elif body.must_change_password is not None:
        user.must_change_password = body.must_change_password
    return user_out(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    if user.id == admin.id:
        raise HTTPException(400, "you cannot delete yourself")
    if user.role == ROLE_ADMIN and _admin_count(db, exclude=user.id) == 0:
        raise HTTPException(400, "at least one active admin is required")
    db.delete(user)
    return {"ok": True}
