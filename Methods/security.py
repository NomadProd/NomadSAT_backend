import base64
import hashlib

import bcrypt


_PREFIX = "sha256bcrypt$"


def _password_bytes(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def get_password_hash(password: str) -> str:
    """Converts plain text password to a secure hash."""
    hashed = bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt())
    return f"{_PREFIX}{hashed.decode('utf-8')}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Checks if the plain password matches the stored hash."""
    if hashed_password.startswith(_PREFIX):
        stored = hashed_password[len(_PREFIX) :].encode("utf-8")
        return bcrypt.checkpw(_password_bytes(plain_password), stored)

    # Compatibility with older standard bcrypt hashes already in the database.
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > 72:
        return False
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
