from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(secret: str) -> bytes:
    if not secret:
        return b""
    source, source_buffer = _blob(secret.encode("utf-8"))
    result = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Codex Site Publisher",
        None,
        None,
        None,
        0,
        ctypes.byref(result),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def unprotect(payload: bytes) -> str:
    if not payload:
        return ""
    source, source_buffer = _blob(payload)
    result = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def save_secret(path: Path, secret: str) -> None:
    if secret:
        path.write_bytes(protect(secret))
    elif path.exists():
        path.unlink()


def load_secret(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return unprotect(path.read_bytes())
    except (OSError, UnicodeDecodeError):
        return ""
