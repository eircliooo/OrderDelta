"""SKU 与客户料号归一化。SPEC §7（保留原第十节，逐字生效）。

规则：
  - 去首尾空格
  - 统一大小写
  - 统一全角半角
  - 可配置是否忽略空格 / 短横线 / 下划线
  - **不得默认删除前导零**（0012 与 12 是不同的料号）
  - 保留原始 SKU
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.normalization.text import nfkc

_SEPARATORS = re.compile(r"[\s　\-_]+")
_INNER_WS = re.compile(r"[\s　]+")


@dataclass(frozen=True)
class SkuNormalizationOptions:
    """项目级可配置项。默认最保守：只做大小写与全角半角统一。

    ignore_separators=True 会把 `AB-100` 与 `AB100` 视为同一 SKU。默认关闭，
    因为它会提高匹配率但同时提高误匹配率——「低误匹配优先于高匹配率」。
    """

    ignore_separators: bool = False
    uppercase: bool = True


DEFAULT_SKU_OPTIONS = SkuNormalizationOptions()


def normalize_sku(
    raw: object, options: SkuNormalizationOptions = DEFAULT_SKU_OPTIONS
) -> str | None:
    """归一化 SKU。返回 None 表示该行没有 SKU（不是空字符串）。

    >>> normalize_sku(" ab-100 ")
    'AB-100'
    >>> normalize_sku("ＡＢ－１００")
    'AB-100'
    >>> normalize_sku("0012")
    '0012'
    >>> normalize_sku("AB-100", SkuNormalizationOptions(ignore_separators=True))
    'AB100'
    """
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        # Excel 常把纯数字料号读成数字。
        text = str(raw)
    elif isinstance(raw, float):
        # 域内禁止出现 `float(`（CLAUDE.md 架构边界，有 AST 扫描测试）：经 repr 转
        # Decimal 保住十进制字面量，再去掉 .0 尾巴，全程不让二进制浮点参与运算。
        number = Decimal(repr(raw))
        integral = number.to_integral_value()
        text = format(integral if number == integral else number, "f")
    elif isinstance(raw, str):
        text = raw
    else:
        text = str(raw)

    text = nfkc(text).strip()
    if not text:
        return None

    if options.uppercase:
        text = text.upper()

    pattern = _SEPARATORS if options.ignore_separators else _INNER_WS
    text = pattern.sub("" if options.ignore_separators else " ", text)

    return text or None


def normalize_customer_part(
    raw: object, options: SkuNormalizationOptions = DEFAULT_SKU_OPTIONS
) -> str | None:
    """客户料号与 SKU 走同一套归一化。"""
    return normalize_sku(raw, options)
