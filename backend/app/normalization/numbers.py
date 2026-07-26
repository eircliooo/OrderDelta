"""数值归一化。SPEC §7、§7.1。

**全程 Decimal，禁止二进制浮点。** 域内模块出现 `float(` 会被 AST 扫描测试判失败
（CLAUDE.md 架构边界）。

保存四元组 raw_value / parsed_value / normalized_value / parse_warning（SPEC §7）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.normalization.text import nfkc

# --------------------------------------------------------------- 币种精度与舍入

#: SPEC §7.1。容差表达为「该币种 1 个最小单位」，不是硬编码 0.01——
#: 否则 JPY/KRW/VND（无小数位）会把正确金额判错。
CURRENCY_DECIMALS: dict[str, int] = {
    "default": 2,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
    "JOD": 3,
    "TND": 3,
}

ROUNDING = ROUND_HALF_UP


def currency_decimals(currency: str | None) -> int:
    if not currency:
        return CURRENCY_DECIMALS["default"]
    return CURRENCY_DECIMALS.get(currency.upper(), CURRENCY_DECIMALS["default"])


def minimal_unit(currency: str | None) -> Decimal:
    """该币种的 1 个最小单位。文档内算术校验的容差就是它（SPEC §9.3）。"""
    return Decimal(1).scaleb(-currency_decimals(currency))


def quantize_money(value: Decimal, currency: str | None) -> Decimal:
    """按币种精度量化。

    跨文档判等**先量化再精确相等分桶**，禁止 abs(a-b) <= tol——容差关系不传递
    （a~b、b~c 但 a≁c），两两比较会得到自相矛盾且依赖顺序的结果（SPEC §9.3）。
    """
    return value.quantize(minimal_unit(currency), rounding=ROUNDING)


# --------------------------------------------------------------- 解析

_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
#: 货币符号与常见前后缀。单独的 $ 由 currency.py 判定歧义，这里只负责剥离。
_CURRENCY_CHARS = re.compile(r"[$€£¥￥₹₩₫]|(?i:usd|eur|gbp|jpy|cny|rmb|hkd|aud|cad)")
_TRAILING_UNIT = re.compile(r"(?i)\s*(pcs?|sets?|ctns?|cartons?|kgs?|pieces?)\s*$")
_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


@dataclass(frozen=True)
class NumberParse:
    """数值解析四元组（SPEC §7）。"""

    raw: str | None
    value: Decimal | None
    normalized: str | None
    warning: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def _fail(raw: str | None, warning: str) -> NumberParse:
    return NumberParse(raw=raw, value=None, normalized=None, warning=warning)


def parse_decimal(raw: object) -> NumberParse:
    """把单元格原始值解析成 Decimal。

    支持：千位分隔符、货币符号、括号负数、百分号、全角数字、尾随单位。
    不支持/无法确定时返回 value=None 并给出 warning——**无法判断时显式失败**。

    注意：openpyxl 对数字单元格返回 int / float。float 是 openpyxl 给的，
    这里立刻经 str() 转 Decimal，域内不再出现二进制浮点参与运算。
    """
    if raw is None:
        return NumberParse(raw=None, value=None, normalized=None, warning=None)

    if isinstance(raw, bool):
        return _fail(str(raw), "布尔值不是数量或金额")

    if isinstance(raw, int):
        value = Decimal(raw)
        return NumberParse(raw=str(raw), value=value, normalized=format(value, "f"))

    if isinstance(raw, Decimal):
        return NumberParse(raw=str(raw), value=raw, normalized=format(raw, "f"))

    if isinstance(raw, float):
        # openpyxl 的数字单元格。经 repr 转 Decimal 保住十进制字面量。
        value = Decimal(repr(raw))
        return NumberParse(raw=repr(raw), value=value, normalized=format(value, "f"))

    if not isinstance(raw, str):
        return _fail(str(raw), f"不支持的单元格类型 {type(raw).__name__}")

    original = raw
    text = nfkc(raw).strip()
    if not text:
        return NumberParse(raw=original, value=None, normalized=None, warning=None)

    negative = False
    # 括号负数：(1,234.00) -> -1234.00
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()

    text = _CURRENCY_CHARS.sub("", text).strip()
    text = _TRAILING_UNIT.sub("", text).strip()
    text = _THOUSANDS.sub("", text)
    text = text.replace(" ", "")

    if not text:
        return _fail(original, "剥离符号后没有剩余数字")

    if not _NUMERIC.match(text):
        return _fail(original, f"无法解析为数值：{original!r}")

    try:
        value = Decimal(text)
    except InvalidOperation:  # pragma: no cover - 已被 _NUMERIC 拦截
        return _fail(original, f"无法解析为数值：{original!r}")

    if negative:
        value = -value
    warning = None
    if percent:
        value = value / Decimal(100)
        warning = "原文为百分数，已转换为小数"

    return NumberParse(raw=original, value=value, normalized=format(value, "f"), warning=warning)


def parse_percent(raw: object) -> NumberParse:
    """付款比例专用：把 30 / 30% / 0.3 统一成 Decimal 比例。

    裸数字 30 视为 30%（付款条件语境下 30 表示三成，不是 3000%）。

    **「裸数字」这个前提必须真的检查**：`parse_decimal` 已经把 `150%` 换算成 1.5，
    再对它套一次「>1 就是百分数字面量」会二次除以 100，把 150% 静默读成 1.5%——
    错得离谱却看起来像个正常比例，是最难被发现的一类错。
    """
    parsed = parse_decimal(raw)
    if parsed.value is None:
        return parsed
    value = parsed.value
    warning = parsed.warning
    already_percent = isinstance(raw, str) and "%" in nfkc(raw)
    if not already_percent and value > 1:
        value = value / Decimal(100)
        warning = "原文为百分数字面量，已按百分比处理"
    return NumberParse(raw=parsed.raw, value=value, normalized=format(value, "f"), warning=warning)


def fmt(value: Decimal | None) -> str | None:
    """golden 序列化统一走这里：Decimal 一律 format(d, 'f')，不用科学计数法。"""
    return None if value is None else format(value, "f")
