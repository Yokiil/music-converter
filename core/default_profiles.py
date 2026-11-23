"""Published format constants for the NCM variant supported by ncmdump.

These are public format constants, not account credentials or per-song keys.
Reference: https://github.com/taurusxin/ncmdump/blob/1.5.1/src/ncmcrypt.cpp
Upstream names: NeteaseCrypt::sCoreKey and NeteaseCrypt::sModifyKey.
Source/notice: THIRD_PARTY.md. No third-party decryption code is copied here;
file parsing and decryption continue to use the pinned optional adapter.
"""
from types import MappingProxyType

from .platforms import Profile

_NCM = MappingProxyType({
    "core_key": bytes.fromhex("687a4852416d736f356b496e62617857"),
    "tag_key": bytes.fromhex("2331346c6a6b5f215c5d2630553c2728"),
})


def default_profile() -> Profile:
    """Only NCM has a verified built-in default in this release."""
    return MappingProxyType({"ncm": _NCM})
