from __future__ import annotations

from jamo import h2j

_CHOSEONG = {
    "\u1100": "g", "\u1101": "kk", "\u1102": "n", "\u1103": "d", "\u1104": "dd",
    "\u1105": "l", "\u1106": "m", "\u1107": "b", "\u1108": "pp", "\u1109": "s",
    "\u110a": "ss", "\u110b": "", "\u110c": "j", "\u110d": "jj", "\u110e": "ch",
    "\u110f": "k", "\u1110": "t", "\u1111": "p", "\u1112": "h",
}

_JUNGSEONG = {
    "\u1161": "a", "\u1162": "ae", "\u1163": "ya", "\u1164": "yae", "\u1165": "eo",
    "\u1166": "e", "\u1167": "yeo", "\u1168": "ye", "\u1169": "o", "\u116a": "wa",
    "\u116b": "wae", "\u116c": "oe", "\u116d": "yo", "\u116e": "u", "\u116f": "wo",
    "\u1170": "we", "\u1171": "wi", "\u1172": "yu", "\u1173": "eu", "\u1174": "ui",
    "\u1175": "i",
}

_JONGSEONG = {
    "": "",
    "\u11a8": "k", "\u11a9": "k", "\u11aa": "n", "\u11ab": "t", "\u11ac": "l",
    "\u11ad": "m", "\u11ae": "p", "\u11af": "t", "\u11b0": "t", "\u11b1": "ng",
    "\u11b2": "t", "\u11b3": "t", "\u11b4": "k", "\u11b5": "t", "\u11b6": "p",
    "\u11b7": "t", "\u11b8": "lk", "\u11b9": "lm", "\u11ba": "lb", "\u11bb": "ls",
    "\u11bc": "lt", "\u11bd": "lp", "\u11be": "lh", "\u11bf": "bs", "\u11c0": "t",
    "\u11c1": "b", "\u11c2": "t",
}

_FILLERS = {"\u115f", "\u1160"}


def _romanize_syllable(ch: str) -> str:
    jamos = h2j(ch)
    choseong = _CHOSEONG.get(jamos[0], "")
    jungseong = _JUNGSEONG.get(jamos[1], "")
    jongseong = _JONGSEONG.get(jamos[2], "") if len(jamos) > 2 else ""
    return choseong + jungseong + jongseong


def romanize_korean(text: str) -> str:
    """Transliterate Hangul to Revised-Romanization; non-Hangul is kept as-is."""
    out: list[str] = []
    for ch in text:
        if "\uac00" <= ch <= "\ud7a3":
            out.append(_romanize_syllable(ch))
        elif ch in _FILLERS:
            continue
        else:
            out.append(ch)
    return "".join(out)
