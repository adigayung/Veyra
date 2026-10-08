"""Veyra security package: native boundary + crypto session.

This package is the security critical core of Veyra:

* :mod:`veyra.security.native_backend` - native (Windows DPAPI/CNG) boundary.
* :mod:`veyra.security.keystore` - DPAPI protected key store on disk.
* :mod:`veyra.security.crypto_session` - password -> KEK -> master key session.
"""

from __future__ import annotations

from veyra.security.crypto_session import (
    CryptoError,
    CryptoService,
    LockedError,
    WrongPasswordError,
)

__all__ = [
    "CryptoService",
    "CryptoError",
    "LockedError",
    "WrongPasswordError",
]
