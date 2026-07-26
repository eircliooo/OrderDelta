"""币种归一化。SPEC §7。

**单独的 `$` 必须标记可能存在歧义**——USD / HKD / AUD / CAD / SGD / NZD 都写作 $，
在外贸单据里这个歧义是真实存在且代价高昂的。不得擅自认定为 USD。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.normalization.text import nfkc

#: 常见 ISO 4217 代码（MVP 只收外贸高频币种）。
KNOWN_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "HKD",
        "AUD",
        "CAD",
        "SGD",
        "NZD",
        "CHF",
        "SEK",
        "NOK",
        "DKK",
        "KRW",
        "INR",
        "RUB",
        "BRL",
        "MXN",
        "ZAR",
        "AED",
        "SAR",
        "TRY",
        "THB",
        "MYR",
        "IDR",
        "PHP",
        "VND",
        "PLN",
        "CZK",
    }
)

#: 无歧义符号。
_UNAMBIGUOUS_SYMBOLS: dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "₩": "KRW",
    "₹": "INR",
    "₫": "VND",
}

#: 歧义符号 -> 候选集合。
_AMBIGUOUS_SYMBOLS: dict[str, tuple[str, ...]] = {
    "$": ("USD", "HKD", "AUD", "CAD", "SGD", "NZD"),
    "¥": ("CNY", "JPY"),
    "￥": ("CNY", "JPY"),
}

#: 口语/中文写法。RMB 是 CNY 的俗称，不是独立币种。
_TEXT_ALIASES: dict[str, str] = {
    "rmb": "CNY",
    "人民币": "CNY",
    "元": "CNY",
    "美元": "USD",
    "美金": "USD",
    "欧元": "EUR",
    "英镑": "GBP",
    "日元": "JPY",
    "港币": "HKD",
    "港元": "HKD",
}

_CODE_IN_TEXT = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")


@dataclass(frozen=True)
class CurrencyParse:
    raw: str | None
    code: str | None
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()
    warning: str | None = None


def parse_currency(raw: object) -> CurrencyParse:
    """从单元格文本解析币种。

    >>> parse_currency("USD").code
    'USD'
    >>> parse_currency("$").ambiguous
    True
    >>> parse_currency("RMB").code
    'CNY'
    """
    if raw is None:
        return CurrencyParse(raw=None, code=None)
    original = str(raw)
    text = nfkc(original).strip()
    if not text:
        return CurrencyParse(raw=original, code=None)

    lowered = text.lower()
    if lowered in _TEXT_ALIASES:
        return CurrencyParse(raw=original, code=_TEXT_ALIASES[lowered])

    upper = text.upper()
    if upper in KNOWN_CURRENCIES:
        return CurrencyParse(raw=original, code=upper)

    match = _CODE_IN_TEXT.search(upper)
    if match and match.group(1) in KNOWN_CURRENCIES:
        return CurrencyParse(raw=original, code=match.group(1))

    for symbol, code in _UNAMBIGUOUS_SYMBOLS.items():
        if symbol in text:
            return CurrencyParse(raw=original, code=code)

    for symbol, candidates in _AMBIGUOUS_SYMBOLS.items():
        if symbol in text:
            return CurrencyParse(
                raw=original,
                code=None,
                ambiguous=True,
                candidates=candidates,
                warning=f"符号 {symbol} 可能对应 {'/'.join(candidates)}，未擅自认定",
            )

    return CurrencyParse(raw=original, code=None, warning=f"无法识别币种：{original!r}")
