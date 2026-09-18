"""Stable content hashing used by the host and replaceable business packs."""
import json
from hashlib import sha256


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def content_hash(value):
    return sha256(canonical(value).encode()).hexdigest()
