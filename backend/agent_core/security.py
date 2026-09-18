"""Transport-neutral identity primitives shared by hosts and business packs."""
from hashlib import sha256
import unicodedata

from argon2 import PasswordHasher


hasher = PasswordHasher()


def digest(value: str):
    return sha256(value.encode()).hexdigest()


def normalize_username(value: str):
    return unicodedata.normalize("NFKC", value).strip().casefold()
