"""Cifrado en reposo de las claves de los dispositivos (Fernet = AES-128-CBC + HMAC-SHA256)."""
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> Fernet:
    clave = settings.CLAVE_CIFRADO_CAMPOS
    if not clave:
        raise ImproperlyConfigured("Falta CLAVE_CIFRADO_CAMPOS en .env (genérala con: python scripts/generar_env.py).")
    return Fernet(clave.encode())


def cifrar(datos: bytes) -> str:
    return _fernet().encrypt(datos).decode()


def descifrar(token: str) -> bytes:
    return _fernet().decrypt(token.encode())
