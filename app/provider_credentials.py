"""Encrypted provider credential helpers."""

import os

from cryptography.fernet import Fernet, InvalidToken


ENV_KEY = "PROVIDER_CREDENTIAL_ENCRYPTION_KEY"


def _cipher():
    key = os.environ.get(ENV_KEY)
    if not key:
        raise RuntimeError(f"{ENV_KEY} is not configured.")

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"{ENV_KEY} must be a valid Fernet key.") from error


def encrypt_secret(secret):
    """Encrypt a provider secret for database storage."""
    if secret is None:
        raise ValueError("Secret is required.")

    value = str(secret)
    if not value:
        raise ValueError("Secret cannot be empty.")

    return _cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_secret):
    """Decrypt a provider secret from database storage."""
    if not encrypted_secret:
        raise ValueError("Encrypted secret is required.")

    try:
        return _cipher().decrypt(
            encrypted_secret.encode("utf-8")
        ).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("The stored provider credential could not be decrypted.") from error


def secret_hint(secret):
    """Return a non-sensitive display hint without exposing the secret."""
    value = str(secret or "")
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"
