"""Published default constants for supported local wrapper variants.

These are public format constants, not user credentials or per-song keys.
NCM: taurusxin/ncmdump 1.5.1, sCoreKey / sModifyKey.
QMCv1: presburger/qmc-decoder fb1e04b, seed traversal's 128-byte period.
QMCv2, KWM and KGM slot 1: MusicDecrypto.Library b97bedc.
Exact source links and license notices are retained in THIRD_PARTY.md.
"""
from types import MappingProxyType

from .platforms import Profile

_NCM = {
    "core_key": bytes.fromhex("687a4852416d736f356b496e62617857"),
    "tag_key": bytes.fromhex("2331346c6a6b5f215c5d2630553c2728"),
}
_QMC1_MASK = bytes.fromhex(
    "c34ad6ca9067f752d8a166629f5b0900"
    "c35e95239f13117ed8923fbc90bb740e"
    "c347743d90aa3f51d8f411849fde951d"
    "c3c609d59ffa66f9d8f0f7a090a1d6f3"
    "c3f3d6a190a0f7f0d8f966fa9fd509c6"
    "c31d95de9f8411f4d8513faa903d7447"
    "c30e74bb90bc3f92d87e11139f23955e"
    "c300095b9f6266a1d852f76790cad64a"
)


def default_profile() -> Profile:
    return MappingProxyType({name: MappingProxyType(values) for name, values in {
        "ncm": _NCM,
        "qmc1": {"mask": _QMC1_MASK},
        "qmc2": {
            "core_key": bytes.fromhex("695646382b20150b"),
            "garble_keys": (b"386ZJY!@#*$%^&)(", b"**#!(#$%&^a1cZ,T"),
        },
        "kwm": {"core_key": b"MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"},
        "kgm": {"slot_key": b"l,/'"},
    }.items()})


def merge_profile(overrides: Profile, *, include_defaults: bool = True,
                  exclude: tuple[str, ...] = ()) -> Profile:
    """Layer explicit per-field overrides on the published defaults.

    ``exclude`` drops individual *built-in* families only; an override for the
    same family is still applied, so operators can supply their own values while
    the shipped defaults stay off. Overrides never mutate the module constants.
    """
    combined = {name: dict(values) for name, values in
                (default_profile() if include_defaults else {}).items()
                if name not in exclude}
    for family, fields in overrides.items():
        combined.setdefault(family, {}).update(fields)
    return MappingProxyType({name: MappingProxyType(values) for name, values in combined.items()})
