from datetime import timedelta
from hashlib import sha256
import secrets
import unicodedata
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Request, Depends
from sqlalchemy import select
from .db import get_db, now
from .models import User, LoginSession
from .errors import DomainError
from .config import settings

hasher = PasswordHasher()
def digest(value: str): return sha256(value.encode()).hexdigest()
def normalize_username(value: str): return unicodedata.normalize("NFKC", value).strip().casefold()


def login(db, username, password):
    user = db.scalar(select(User).where(User.username == normalize_username(username)))
    try:
        if not user or not user.active or not hasher.verify(user.password_hash, password):
            raise DomainError("LOGIN_FAILED", "用户名或密码不正确", 401)
    except VerificationError:
        raise DomainError("LOGIN_FAILED", "用户名或密码不正确", 401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(token), csrf_hash=digest(csrf), user_id=user.id, expires_at=now()+timedelta(hours=8)))
    return user, token, csrf


def current_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("mold_session", "")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(token), LoginSession.expires_at > now()))
    if not session: raise DomainError("UNAUTHENTICATED", "请先登录", 401)
    user = db.get(User, session.user_id)
    if not user or not user.active: raise DomainError("UNAUTHENTICATED", "账号已停用", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != settings().origin:
            raise DomainError("ORIGIN_DENIED", "请求来源不受信任", 403)
        csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(digest(csrf), session.csrf_hash):
            raise DomainError("CSRF_REQUIRED", "请刷新会话后再操作", 403)
        # Shared lock gives a deterministic boundary against concurrent revocation.
        db.refresh(user, with_for_update={"read": True})
    request.state.session = session
    return user


def public_user(user):
    return {"id": user.id, "username": user.username, "display_name": user.display_name,
            "department": user.department, "super_admin": user.super_admin,
            "active": user.active, "security_version": user.security_version}
