"""文档级字段提取（Key-Value 区）。SPEC §6.1 第二层、§4.1。

策略：在网格里找「标签单元格」，再在**右邻**（同行后续非空格）或**下方**（同列下一行）
找值。取值必须通过该字段 value_kind 的校验才被采纳——这一条是防误取的关键：

  表头行里的 `Total` 右邻是另一个表头文本（非数值）-> 校验失败 -> 跳过
  表尾行里的 `合计` 右邻是 3270.00（数值）        -> 校验通过 -> 采纳

「下方取值」只在标签所在行非空单元格 <= 3 时启用。否则表头行 `Total` 正下方的
行金额会被误当成总金额。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import ValueKind
from app.domain.fields import DOCUMENT_ALIAS_INDEX, FieldScope, document_spec, match_header
from app.normalization.currency import parse_currency
from app.normalization.dates import parse_date
from app.normalization.incoterm import parse_incoterm
from app.normalization.numbers import fmt, parse_decimal
from app.normalization.text import normalize_header, normalize_text
from app.parsers.base import ParsedCell, ParsedTable

#: 标签右侧最多看几个非空单元格。
MAX_RIGHT_LOOKAHEAD = 3
#: 标签下方最多看几行。
MAX_BELOW_LOOKAHEAD = 2
#: 启用「下方取值」的行非空单元格上限（超过就认为这是表头行而非标签行）。
LABEL_ROW_MAX_CELLS = 3


@dataclass(frozen=True)
class ExtractedDocField:
    field_key: str
    raw_text: str | None
    normalized: str | None
    warning: str | None
    sheet_name: str
    address: str
    label_text: str
    label_address: str

    #: incoterm 拆出的三段（其余字段为 None）
    extra: Mapping[str, str | None] | None = None


@dataclass(frozen=True)
class DocFieldExtraction:
    fields: Mapping[str, ExtractedDocField]
    warnings: tuple[str, ...]


def _is_label(text: str) -> bool:
    """值单元格本身是另一个标签时不得被采纳（避免 `Date` 取到 `Buyer`）。"""
    return normalize_header(text) in DOCUMENT_ALIAS_INDEX


def _validate(field_key: str, raw: str) -> tuple[str | None, str | None] | None:
    """按 value_kind 校验并归一化。返回 None 表示该候选值不合格。"""
    spec = document_spec(field_key)
    text = raw.strip()
    if not text or _is_label(text):
        return None

    if spec.value_kind in (ValueKind.DECIMAL, ValueKind.MONEY):
        parsed = parse_decimal(text)
        if parsed.value is None:
            return None
        return fmt(parsed.value), parsed.warning

    if spec.value_kind is ValueKind.CURRENCY:
        currency = parse_currency(text)
        if currency.code is None and not currency.ambiguous:
            return None
        return currency.code, currency.warning

    if spec.value_kind is ValueKind.DATE:
        parsed_date = parse_date(text)
        if parsed_date.iso is None and not parsed_date.ambiguous:
            return None
        return parsed_date.iso, parsed_date.warning

    if field_key == "incoterm":
        incoterm = parse_incoterm(text)
        if incoterm.term is None:
            return None
        return incoterm.term, incoterm.warning

    return normalize_text(text) or None, None


def _row_non_empty(row: tuple[ParsedCell, ...]) -> int:
    return sum(1 for c in row if c.value_raw and c.value_raw.strip())


def _value_candidates(table: ParsedTable, row_offset: int, col_offset: int) -> list[ParsedCell]:
    """按确定顺序给出候选值单元格：先右后下。"""
    row = table.cells[row_offset]
    out: list[ParsedCell] = []

    seen = 0
    for col in range(col_offset + 1, len(row)):
        cell = row[col]
        if not (cell.value_raw and cell.value_raw.strip()):
            continue
        out.append(cell)
        seen += 1
        if seen >= MAX_RIGHT_LOOKAHEAD:
            break

    if _row_non_empty(row) <= LABEL_ROW_MAX_CELLS:
        below_stop = min(row_offset + 1 + MAX_BELOW_LOOKAHEAD, len(table.cells))
        for offset in range(row_offset + 1, below_stop):
            below = table.cells[offset]
            if col_offset >= len(below):
                continue
            cell = below[col_offset]
            if cell.value_raw and cell.value_raw.strip():
                out.append(cell)
                break

    return out


def extract_document_fields(tables: tuple[ParsedTable, ...]) -> DocFieldExtraction:
    """扫描所有工作表提取文档级字段。

    同一字段的多个命中：**取第一个通过校验的**（行主序），后续命中忽略并记 warning。
    确定性优先——绝不依赖 dict 迭代顺序。
    """
    found: dict[str, ExtractedDocField] = {}
    warnings: list[str] = []

    for table in tables:
        for row_offset, row in enumerate(table.cells):
            for col_offset, cell in enumerate(row):
                label = cell.value_raw
                if not label or not label.strip():
                    continue
                field_key = match_header(label, FieldScope.DOCUMENT)
                if field_key is None:
                    continue

                for candidate in _value_candidates(table, row_offset, col_offset):
                    validated = _validate(field_key, candidate.value_raw or "")
                    if validated is None:
                        continue
                    normalized, warning = validated

                    if field_key in found:
                        warnings.append(
                            f"字段 {field_key} 有多处候选，已采用首个："
                            f"{found[field_key].address}（忽略 {candidate.ref.address}）"
                        )
                        break

                    extra: dict[str, str | None] | None = None
                    if field_key == "incoterm":
                        parsed = parse_incoterm(candidate.value_raw or "")
                        extra = {
                            "incoterm_named_place": parsed.named_place,
                            "incoterm_version": parsed.version,
                        }

                    found[field_key] = ExtractedDocField(
                        field_key=field_key,
                        raw_text=candidate.value_raw,
                        normalized=normalized,
                        warning=warning,
                        sheet_name=candidate.ref.sheet_name,
                        address=candidate.ref.address,
                        label_text=label,
                        label_address=cell.ref.address,
                        extra=extra,
                    )
                    break

    # incoterm 拆出的两段提升为独立字段（比较时不能只比 term）
    incoterm = found.get("incoterm")
    if incoterm is not None and incoterm.extra:
        for key, value in incoterm.extra.items():
            if value and key not in found:
                found[key] = ExtractedDocField(
                    field_key=key,
                    raw_text=incoterm.raw_text,
                    normalized=value,
                    warning=None,
                    sheet_name=incoterm.sheet_name,
                    address=incoterm.address,
                    label_text=incoterm.label_text,
                    label_address=incoterm.label_address,
                )

    return DocFieldExtraction(fields=found, warnings=tuple(warnings))
