"""Fernet encryption for MCP bearer tokens (§M4a).

Never log or return plain tokens. Key is a Fernet key stored in MCP_TOKEN_KEY env.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import settings
from app.errors import ApiError


def _fernet() -> Fernet:
    if not settings.mcp_token_key:
        raise ApiError(
            400, "mcp_key_missing", "Set MCP_TOKEN_KEY to store bearer tokens"
        )
    return Fernet(settings.mcp_token_key.encode())


def encrypt_token(plain: str) -> str:
    """Return the Fernet-encrypted ciphertext of *plain* as a str."""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_token(enc: str) -> str:
    """Return the plaintext of *enc* (a Fernet ciphertext str)."""
    return _fernet().decrypt(enc.encode()).decode()
