"""Explicit local bootstrap; never runs automatically on application startup."""
import argparse
import getpass
from sqlalchemy import select
from .db import SessionLocal
from .models import User
from .security import hasher, normalize_username


def create_admin(db, username, display_name, password):
    if db.scalar(select(User.id).where(User.super_admin.is_(True))): raise ValueError("Initial administrator already exists")
    if len(password) < 12: raise ValueError("Password must contain at least 12 characters")
    user = User(username=normalize_username(username), display_name=display_name, password_hash=hasher.hash(password), super_admin=True)
    db.add(user); db.flush()
    return user


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Initial password: ")
    with SessionLocal.begin() as db: create_admin(db, args.username, args.name, password)
    print("Initial administrator created; bootstrap is now closed.")
