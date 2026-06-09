"""Symmetric encryption for admin TOTP secrets at rest (Fernet / AES-128-CBC + HMAC).

The key comes from Settings.resolved_mfa_encryption_key. Only the encrypted bytes
are stored in users.mfa_secret_encrypted; the plaintext base32 secret lives in memory
only during setup and verification.
"""

from cryptography.fernet import Fernet


def encrypt_secret(key: str, plaintext_secret: str) -> bytes:
    return Fernet(key.encode("utf-8")).encrypt(plaintext_secret.encode("utf-8"))


def decrypt_secret(key: str, ciphertext: bytes) -> str:
    return Fernet(key.encode("utf-8")).decrypt(ciphertext).decode("utf-8")
