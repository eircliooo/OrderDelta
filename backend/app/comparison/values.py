"""字段级比较器。SPEC §9.2、§9.3。

**跨文档判等：先量化再精确相等分桶，禁止 `abs(a-b) <= tol`。**
容差关系不传递（a~b、b~c 但 a≁c），两两比较会得到自相矛盾且依赖顺序的差异集。

**N 元收集，绝不两两组合产出多条差异**：每字段收集 {role: value}，非空值分桶，
桶数 > 1 产出**一条** VALUE_CONFLICT。否则同一冲突产出 3 条，总览计数翻三倍。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.enums import DocumentRole, Verdict
from app.domain.fields import Comparator, FieldScope, FieldSpec, line_item_spec
from app.domain.models import ValueCell
from app.normalization.delivery import (
    delivery_comparable,
    delivery_equal,
    parse_delivery_terms,
)
from app.normalization.numbers import quantize_money
from app.normalization.payment import parse_payment_terms, payment_comparable
from app.normalization.text import collapse_ws


@dataclass(frozen=True)
class BucketResult:
    """分桶结果。`buckets` 的键是量化后的可比较表示。"""

    verdict: Verdict
    buckets: dict[str, tuple[DocumentRole, ...]]
    reason_key: str | None = None
    detail: str | None = None

    @property
    def conflicting(self) -> bool:
        return self.verdict is Verdict.DIFFERENT


def _decimal_key(value: str) -> str | None:
    try:
        return format(Decimal(value).normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def _money_key(value: str, currency: str | None) -> str | None:
    try:
        return format(quantize_money(Decimal(value), currency), "f")
    except (InvalidOperation, ValueError):
        return None


def _text_key(value: str) -> str:
    return collapse_ws(value)


def _semantic_key(value: str) -> str:
    return collapse_ws(value).casefold()


def _bucket(
    keys: dict[DocumentRole, str],
) -> dict[str, tuple[DocumentRole, ...]]:
    grouped: dict[str, list[DocumentRole]] = defaultdict(list)
    for role, key in keys.items():
        grouped[key].append(role)
    # 角色顺序固定，桶键排序 —— 输出必须确定性
    return {
        key: tuple(sorted(roles, key=lambda r: r.value)) for key, roles in sorted(grouped.items())
    }


def _verdict_from(buckets: dict[str, tuple[DocumentRole, ...]]) -> Verdict:
    return Verdict.DIFFERENT if len(buckets) > 1 else Verdict.EQUAL


def _incomparable(reason_key: str, detail: str) -> BucketResult:
    return BucketResult(
        verdict=Verdict.INCOMPARABLE, buckets={}, reason_key=reason_key, detail=detail
    )


def _uncertain(reason_key: str, detail: str) -> BucketResult:
    return BucketResult(verdict=Verdict.UNCERTAIN, buckets={}, reason_key=reason_key, detail=detail)


def compare_values(
    spec: FieldSpec,
    cells: dict[DocumentRole, ValueCell],
    *,
    currency_by_role: dict[DocumentRole, str | None] | None = None,
    unit_by_role: dict[DocumentRole, str | None] | None = None,
) -> BucketResult:
    """对同一字段在多个角色上的取值做 N 元判定。

    传入的 cells 必须都是**有值**的（缺失由调用方单独处理成 MISSING_VALUE）。
    """
    values = {role: cell.value for role, cell in cells.items() if cell.value is not None}
    if len(values) < 2:
        return BucketResult(verdict=Verdict.EQUAL, buckets={})

    currencies = currency_by_role or {}
    units = unit_by_role or {}
    comparator = spec.comparator

    if comparator is Comparator.QUANTITY_WITH_UNIT:
        distinct_units = {units.get(role) for role in values if units.get(role)}
        if len(distinct_units) > 1:
            return _incomparable(
                "incomparable_units",
                "单位不同（" + "、".join(sorted(u for u in distinct_units if u)) + "），"
                "本工具不做跨单位换算，请人工确认",
            )
        keys: dict[DocumentRole, str] = {}
        for role, value in values.items():
            key = _decimal_key(value)
            if key is None:
                return _uncertain("unparsable_number", f"{role.value} 的数量无法解析为数值")
            keys[role] = key
        buckets = _bucket(keys)
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.MONEY_QUANTIZED:
        distinct_currencies = {currencies.get(role) for role in values if currencies.get(role)}
        if len(distinct_currencies) > 1:
            return _incomparable(
                "incomparable_currency",
                "币种不同（" + "、".join(sorted(c for c in distinct_currencies if c)) + "），"
                "金额无法直接比较",
            )
        currency = next(iter(distinct_currencies), None)
        money_keys: dict[DocumentRole, str] = {}
        for role, value in values.items():
            key = _money_key(value, currency)
            if key is None:
                return _uncertain("unparsable_number", f"{role.value} 的金额无法解析为数值")
            money_keys[role] = key
        buckets = _bucket(money_keys)
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.DECIMAL_QUANTIZED:
        decimal_keys: dict[DocumentRole, str] = {}
        for role, value in values.items():
            key = _decimal_key(value)
            if key is None:
                return _uncertain("unparsable_number", f"{role.value} 的数值无法解析")
            decimal_keys[role] = key
        buckets = _bucket(decimal_keys)
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.CURRENCY_CODE:
        ambiguous = [
            role for role, cell in cells.items() if cell.warning and "可能对应" in cell.warning
        ]
        if ambiguous:
            return _uncertain(
                "ambiguous_currency_symbol",
                "存在无法确定的币种符号（如单独的 $），未擅自认定",
            )
        buckets = _bucket(dict(values))
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.DATE_ISO:
        ambiguous_roles = [
            role for role, cell in cells.items() if cell.warning and "歧义" in cell.warning
        ]
        if ambiguous_roles:
            return _uncertain("ambiguous_date", "日期存在日月顺序歧义，未擅自确定")
        buckets = _bucket(dict(values.items()))
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.PAYMENT_STRUCTURED:
        parsed = {role: parse_payment_terms(value) for role, value in values.items()}
        roles = sorted(parsed, key=lambda r: r.value)
        for i, left in enumerate(roles):
            for right in roles[i + 1 :]:
                if not payment_comparable(parsed[left], parsed[right]):
                    return _uncertain(
                        "unstructured_payment_terms",
                        "付款条件无法可靠结构化，已保留原文，请人工确认",
                    )
        payment_keys = {
            role: "|".join(
                (
                    str(p.deposit_percent),
                    str(p.balance_percent),
                    str(p.due_days),
                    str(p.deposit_trigger),
                    str(p.balance_trigger),
                )
            )
            for role, p in parsed.items()
        }
        buckets = _bucket(payment_keys)
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.DELIVERY_TERMS:
        parsed_delivery = {role: parse_delivery_terms(value) for role, value in values.items()}
        roles = sorted(parsed_delivery, key=lambda r: r.value)
        for i, left in enumerate(roles):
            for right in roles[i + 1 :]:
                if not delivery_comparable(parsed_delivery[left], parsed_delivery[right]):
                    return _uncertain(
                        "incomparable_delivery_terms",
                        "交期表述不同类（相对条款 vs 绝对日期）或无法结构化，需人工换算",
                    )
        reference = parsed_delivery[roles[0]]
        all_equal = all(delivery_equal(reference, parsed_delivery[role]) for role in roles[1:])
        if all_equal:
            return BucketResult(
                verdict=Verdict.EQUAL, buckets=_bucket(dict.fromkeys(roles, "same"))
            )
        delivery_keys = {
            role: f"{p.lead_time_days}|{p.delivery_trigger}|{p.absolute_date}"
            for role, p in parsed_delivery.items()
        }
        buckets = _bucket(delivery_keys)
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    if comparator is Comparator.TEXT_SEMANTIC:
        buckets = _bucket({role: _semantic_key(value) for role, value in values.items()})
        return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)

    # TEXT_EXACT 与 INCOTERM_TRIPLE（term 段）走同一条：
    # incoterm 的 named_place / version 已提升为独立字段单独比较，
    # 所以「三段全等才算相等」由三条字段规则共同保证。
    buckets = _bucket({role: _text_key(value) for role, value in values.items()})
    return BucketResult(verdict=_verdict_from(buckets), buckets=buckets)


def line_field_spec(key: str) -> FieldSpec:
    return line_item_spec(key)


__all__ = ["BucketResult", "FieldScope", "compare_values", "line_field_spec"]
