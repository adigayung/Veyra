"""Native (Windows-backed) security boundary for Veyra crypto material.

This module is the *trust anchor* for the Veyra crypto session.  Instead of
letting a plain Python object decide whether the crypto session is open, the
key material is bound to the operating system through native components:

* :func:`protect` / :func:`unprotect` wrap **Windows DPAPI**
  (``CryptProtectData`` / ``CryptUnprotectData``).  Data wrapped this way can
  only be recovered by the same Windows user on the same machine, so key
  material at rest (and, in memory, the live session ticket) is validated by
  the OS - not by a forgeable Python flag.
* :func:`random_bytes` uses the Windows CNG CSPRNG (``BCryptGenRandom``) so
  salts / nonces never depend on ``random``.

The DPAPI layer is **not** a replacement for the master password: the real
authorization is ``password -> Argon2id -> KEK -> unwrap master key`` (see
:mod:`veyra.security.crypto_session`).  DPAPI simply makes the boundary a
native, machine/user bound component that a Python-level patch cannot fake:
a bogus session ticket fails ``unprotect`` and every crypto operation is
refused.

When the native component is unavailable (for example a non-Windows CI host)
:func:`is_available` returns ``False`` and the caller must decide how to
degrade.  The crypto session keeps the password requirement in every case.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional, Tuple

__all__ = [
    "NativeBoundaryError",
    "is_available",
    "platform_name",
    "random_bytes",
    "protect",
    "unprotect",
]


class NativeBoundaryError(RuntimeError):
    """Raised when the native security boundary cannot perform an operation."""


# ---------------------------------------------------------------------------
# ctypes bindings
# ---------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


#: ``CryptProtectData`` / ``CryptUnprotectData`` flags.
CRYPTPROTECT_UI_FORBIDDEN = 0x1

#: ``BCryptGenRandom`` flag: use the system preferred RNG (no algorithm handle).
BCRYPT_USE_SYSTEM_PREFERRED_RNG = 0x00000002

_wintypes_bool = ctypes.c_int

_crypt32: Optional[ctypes.WinDLL] = None
_kernel32: Optional[ctypes.WinDLL] = None
_bcrypt: Optional[ctypes.WinDLL] = None


def _load_native() -> bool:
    """Load the native DLLs exactly once; return ``True`` when usable."""
    global _crypt32, _kernel32, _bcrypt
    if sys.platform != "win32":
        return False
    if _crypt32 is not None:
        return True
    if not hasattr(ctypes, "WinDLL"):  # pragma: no cover - defensive
        return False
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)

        crypt32.CryptProtectData.restype = _wintypes_bool
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.restype = _wintypes_bool
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]

        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]

        bcrypt.BCryptGenRandom.restype = ctypes.c_long
        bcrypt.BCryptGenRandom.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
    except (OSError, AttributeError):  # pragma: no cover - defensive
        return False
    _crypt32, _kernel32, _bcrypt = crypt32, kernel32, bcrypt
    return True


def is_available() -> bool:
    """Return ``True`` when the native (DPAPI/CNG) boundary can be used."""
    return _load_native()


def platform_name() -> str:
    """Human readable name of the active boundary implementation."""
    if _load_native():
        return "windows-dpapi"
    return "os.urandom-fallback"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _blob_from_bytes(data: bytes) -> Tuple[_DATA_BLOB, ctypes.Array]:
    """Build a ``_DATA_BLOB`` that points at ``data`` (buffer kept alive)."""
    if data:
        buffer = ctypes.create_string_buffer(bytes(data), len(data))
    else:
        buffer = ctypes.create_string_buffer(1)
    blob = _DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _bytes_from_blob(blob: _DATA_BLOB) -> bytes:
    if not blob.cbData or not blob.pbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def _local_free(ptr) -> None:
    if ptr:
        _kernel32.LocalFree(ctypes.cast(ptr, ctypes.c_void_p))


def _raise_last_error(prefix: str) -> None:
    code = ctypes.get_last_error()
    raise NativeBoundaryError(f"{prefix} (WinError {code})")


# ---------------------------------------------------------------------------
# secure randomness
# ---------------------------------------------------------------------------

def random_bytes(count: int) -> bytes:
    """Return ``count`` cryptographically secure random bytes.

    Uses the Windows CNG CSPRNG when available and falls back to
    :func:`os.urandom` otherwise.
    """
    if count <= 0:
        return b""
    if _load_native():
        buffer = (ctypes.c_ubyte * count)()
        status = _bcrypt.BCryptGenRandom(
            None, buffer, count, BCRYPT_USE_SYSTEM_PREFERRED_RNG
        )
        if status == 0:
            return bytes(buffer)
    return os.urandom(count)


# ---------------------------------------------------------------------------
# DPAPI protect / unprotect
# ---------------------------------------------------------------------------

def protect(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """DPAPI-wrap ``data`` so only this Windows user can unwrap it.

    ``entropy`` is optional secondary entropy (itself a secret the caller must
    be able to reproduce).  Raises :class:`NativeBoundaryError` when the
    native boundary is unavailable or the call fails.
    """
    if not _load_native():
        raise NativeBoundaryError("Native DPAPI boundary is not available.")

    in_blob, _keep_in = _blob_from_bytes(data)
    entropy_blob = None
    entropy_struct = None
    if entropy is not None:
        entropy_struct, _keep_entropy = _blob_from_bytes(entropy)
        entropy_blob = ctypes.pointer(entropy_struct)

    out_blob = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "veyra",
        entropy_blob,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        _raise_last_error("CryptProtectData failed")
    try:
        return _bytes_from_blob(out_blob)
    finally:
        _local_free(out_blob.pbData)


def unprotect(blob: bytes, entropy: Optional[bytes] = None) -> bytes:
    """Reverse :func:`protect`; raises on tampering / wrong user / entropy."""
    if not _load_native():
        raise NativeBoundaryError("Native DPAPI boundary is not available.")

    in_blob, _keep_in = _blob_from_bytes(blob)
    entropy_blob = None
    if entropy is not None:
        entropy_struct, _keep_entropy = _blob_from_bytes(entropy)
        entropy_blob = ctypes.pointer(entropy_struct)

    out_blob = _DATA_BLOB()
    description = wintypes.LPWSTR()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        entropy_blob,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        _raise_last_error("CryptUnprotectData failed")
    try:
        return _bytes_from_blob(out_blob)
    finally:
        _local_free(out_blob.pbData)
        if description:
            _local_free(description)
