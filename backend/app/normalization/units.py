"""单位归一化。SPEC §7（保留原第十节）。

**没有明确企业换算规则时，只统一同义单位，不执行跨单位换算。**

  PCS / PC / PIECE   可候选等价
  SETS / SET         可候选等价
  CTNS / CARTONS     可候选等价
  KG / KGS           可候选等价

  SETS 与 PCS  **不能**自动换算
  CTNS 与 PCS  **不能**自动换算

跨族比较的结果是 INCOMPARABLE，不是 DIFFERENT——把「无法比较」说成「不一致」
是假警报，说成「一致」是危险的沉默（SPEC §9.1）。
"""

from __future__ import annotations

from app.normalization.text import nfkc

#: 归一化后的规范单位 -> 别名集合。只收同义词，不收可换算词。
_UNIT_FAMILIES: dict[str, frozenset[str]] = {
    "PCS": frozenset({"pcs", "pc", "piece", "pieces", "个", "只", "件", "支"}),
    "SET": frozenset({"set", "sets", "套", "组"}),
    "CTN": frozenset({"ctn", "ctns", "carton", "cartons", "箱", "纸箱", "外箱"}),
    "KG": frozenset({"kg", "kgs", "kilogram", "kilograms", "公斤", "千克"}),
    "PAIR": frozenset({"pair", "pairs", "双", "对"}),
    "DOZ": frozenset({"doz", "dozen", "dozens", "打"}),
    "M": frozenset({"m", "meter", "meters", "metre", "metres", "米"}),
    "SQM": frozenset({"sqm", "m2", "squaremeter", "squaremeters", "平方米"}),
    "L": frozenset({"l", "liter", "liters", "litre", "litres", "升"}),
    "ROLL": frozenset({"roll", "rolls", "卷"}),
    "BAG": frozenset({"bag", "bags", "袋"}),
}

_ALIAS_TO_UNIT: dict[str, str] = {
    alias: canonical for canonical, aliases in _UNIT_FAMILIES.items() for alias in aliases
}


def normalize_unit(raw: object) -> str | None:
    """归一化到规范单位。无法识别时返回大写后的原文（不猜、不丢）。

    >>> normalize_unit("pcs")
    'PCS'
    >>> normalize_unit("PIECE")
    'PCS'
    >>> normalize_unit("СTN")   # 无法识别时原样返回，不静默归零
    'СTN'
    """
    if raw is None:
        return None
    text = nfkc(str(raw)).strip()
    if not text:
        return None
    key = text.lower().replace(".", "").replace(" ", "").replace("/", "")
    return _ALIAS_TO_UNIT.get(key, text.upper())


def units_equivalent(left: str | None, right: str | None) -> bool:
    """两个单位是否同义（可直接比较数量）。"""
    if left is None or right is None:
        return False
    return normalize_unit(left) == normalize_unit(right)


def units_known(unit: str | None) -> bool:
    """是否落在已知单位族内。未知单位参与数量比较时必须降级为 REVIEW。"""
    return unit is not None and unit in _UNIT_FAMILIES


def quantity_comparable(left: str | None, right: str | None) -> bool:
    """数量是否可以直接比较。

    单位不同 -> 不可比较（INCOMPARABLE），**绝不自动换算**。
    两边都没有单位 -> 视为可比较（同一 SKU 的数量列，通常单位隐含且一致）。
    一边有一边没有 -> 可比较，但调用方应降级为 REVIEW。
    """
    if left is None or right is None:
        return True
    return units_equivalent(left, right)
