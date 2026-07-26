"""行项目提取。SPEC §6.1 数据区终止规则、§6.3 层优先级。

数据区规则（确定性、可单测）：
  - 非空单元格 <= 2 的行 -> 跳过，不产出 LineItem（滤掉小计 / 运费 / 合计行）
  - 身份列（SKU / 客户料号 / 描述）全空 **且** 数量列不可解析 -> 终止扫描

宁可少提取一行，也不要把「合计 12345」当成一个产品。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import ValueKind
from app.domain.fields import line_item_spec
from app.domain.identity import line_key
from app.extraction.header import HeaderDetection
from app.normalization.currency import parse_currency
from app.normalization.numbers import fmt, parse_decimal
from app.normalization.sku import normalize_customer_part, normalize_sku
from app.normalization.text import normalize_text
from app.normalization.units import normalize_unit
from app.parsers.base import ParsedTable

#: 少于等于这个数量非空单元格的行不产出 LineItem。
MIN_NON_EMPTY_CELLS = 3

#: 在采集到第一条行项目之前，允许跳过的不合规行数。
#:
#: 真实单据里表头正下方常有一行「单位行」（PCS / USD / 只 …）：它非空单元格 >=3，
#: 身份列全空，数量列不可解析。若直接按终止规则处理，整张表会在第一行就被判空。
#: 采集到第一条行项目之后，不合规行才视为数据区结束。
MAX_LEADING_SKIPS = 3

_IDENTITY_FIELDS = ("internal_sku", "customer_part_number", "description")


@dataclass(frozen=True)
class ExtractedCell:
    """一个字段的提取结果 + 定位信息（Evidence 的原料）。"""

    field_key: str
    raw_text: str | None
    normalized: str | None
    warning: str | None
    sheet_name: str
    address: str
    row_index: int
    col_index: int


@dataclass(frozen=True)
class ExtractedLineItem:
    line_key: str
    row_index: int
    row_offset: int
    sheet_name: str
    sku_norm: str | None
    customer_part_norm: str | None
    unit_norm: str | None
    currency: str | None
    cells: Mapping[str, ExtractedCell]

    def get(self, key: str) -> ExtractedCell | None:
        return self.cells.get(key)


@dataclass(frozen=True)
class LineItemExtraction:
    items: tuple[ExtractedLineItem, ...]
    skipped_rows: tuple[int, ...]
    terminated_at_row: int | None
    warnings: tuple[str, ...]


def _normalize_field(field_key: str, raw: object) -> tuple[str | None, str | None]:
    """按 FieldSpec 的 value_kind 归一化。返回 (normalized, warning)。"""
    spec = line_item_spec(field_key)

    if spec.value_kind in (ValueKind.DECIMAL, ValueKind.MONEY):
        parsed = parse_decimal(raw)
        return fmt(parsed.value), parsed.warning

    if spec.value_kind is ValueKind.CURRENCY:
        parsed_currency = parse_currency(raw)
        return parsed_currency.code, parsed_currency.warning

    if raw is None:
        return None, None
    text = normalize_text(str(raw))
    if field_key == "unit":
        return normalize_unit(text), None
    return text or None, None


def _non_empty_count(row: tuple[object, ...]) -> int:
    return sum(1 for cell in row if cell is not None and str(cell).strip())


def extract_line_items(table: ParsedTable, detection: HeaderDetection) -> LineItemExtraction:
    """从已定位表头的工作表里提取行项目。"""
    if not detection.found:
        return LineItemExtraction(items=(), skipped_rows=(), terminated_at_row=None, warnings=())

    mapping = detection.field_to_column
    items: list[ExtractedLineItem] = []
    skipped: list[int] = []
    warnings: list[str] = []
    terminated_at: int | None = None

    #: 同一文档内 SKU / 客户料号出现次数，用于 line_key 的序号（同 SKU 重复行）
    sku_counter: Counter[str] = Counter()
    cpn_counter: Counter[str] = Counter()

    for offset in range(detection.first_data_row_offset, len(table.cells)):
        row = table.cells[offset]
        raw_values = tuple(cell.value_raw for cell in row)
        excel_row = row[0].ref.row_index if row else offset + 1

        if _non_empty_count(raw_values) < MIN_NON_EMPTY_CELLS:
            skipped.append(excel_row)
            continue

        cells: dict[str, ExtractedCell] = {}
        for field_key, column in mapping.items():
            if column.col_offset >= len(row):
                continue
            cell = row[column.col_offset]
            normalized, warning = _normalize_field(field_key, cell.value_raw)
            cells[field_key] = ExtractedCell(
                field_key=field_key,
                raw_text=cell.value_raw,
                normalized=normalized,
                warning=warning,
                sheet_name=cell.ref.sheet_name,
                address=cell.ref.address,
                row_index=cell.ref.row_index,
                col_index=cell.ref.col_index,
            )

        identity_present = any(
            cells.get(key) is not None and cells[key].normalized for key in _IDENTITY_FIELDS
        )
        quantity_cell = cells.get("quantity")
        quantity_ok = quantity_cell is not None and quantity_cell.normalized is not None

        if not identity_present and not quantity_ok:
            if items or len(skipped) >= MAX_LEADING_SKIPS:
                terminated_at = excel_row
                break
            # 还没采到第一条，多半是单位行/说明行 —— 跳过而不是终止
            skipped.append(excel_row)
            continue

        sku_norm = normalize_sku(_raw_of(cells, "internal_sku"))
        cpn_norm = normalize_customer_part(_raw_of(cells, "customer_part_number"))

        if sku_norm:
            sku_counter[sku_norm] += 1
        if cpn_norm:
            cpn_counter[cpn_norm] += 1

        key = line_key(
            sku_norm=sku_norm,
            sku_ordinal=sku_counter[sku_norm] if sku_norm else 1,
            customer_part_norm=cpn_norm,
            cpn_ordinal=cpn_counter[cpn_norm] if cpn_norm else 1,
            sheet_name=table.sheet_name,
            row_index=excel_row,
        )

        unit_cell = cells.get("unit")
        currency_cell = cells.get("currency")

        items.append(
            ExtractedLineItem(
                line_key=key,
                row_index=excel_row,
                row_offset=offset,
                sheet_name=table.sheet_name,
                sku_norm=sku_norm,
                customer_part_norm=cpn_norm,
                unit_norm=unit_cell.normalized if unit_cell else None,
                currency=currency_cell.normalized if currency_cell else None,
                cells=cells,
            )
        )

    if not items:
        warnings.append(f"工作表 {table.sheet_name!r} 定位到表头但没有提取到任何行项目")

    duplicate_skus = sorted(sku for sku, count in sku_counter.items() if count > 1)
    if duplicate_skus:
        warnings.append("存在重复 SKU（将按序号区分并标记为多候选）：" + "、".join(duplicate_skus))

    return LineItemExtraction(
        items=tuple(items),
        skipped_rows=tuple(skipped),
        terminated_at_row=terminated_at,
        warnings=tuple(warnings),
    )


def _raw_of(cells: Mapping[str, ExtractedCell], key: str) -> object:
    cell = cells.get(key)
    return cell.raw_text if cell else None
