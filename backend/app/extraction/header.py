"""表头打分与列映射。SPEC §6.1、§6.2。

原计划这一段只有一句「支持表格表头」，实现者必然退化成「假定第 1 行是表头」，
而真实外贸单据是：3-8 行公司抬头 + 合并大标题 + 跨两行表头 + 中段空行 + 尾部小计行。

算法（确定性、可单测、无模糊匹配）：

    扫前 N 行，逐行统计命中别名表的列数            -> 单行候选
    同一循环里为每对 (row i, row i+1) 再打一次分   -> 双行候选（覆盖合并/跨行表头）
    按 (分数降序, 单行优先, 行号升序) 取胜者        -> 确定性，无并列歧义

    硬门槛：至少命中 2 个不同列类，
            且必须同时包含「数量类」与「单价或金额类」
    不满足 -> 不落 header_row，交由调用方置 NEEDS_REVIEW / NO_TABLE_FOUND

冲突处理（SPEC §6.2）：同一字段被多列命中 -> **两边都不映射**，进 unmapped_headers。
宁可少提取，也不要把 Total Price 认成 unit_price。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.domain.fields import (
    COLUMN_CLASS_BY_FIELD,
    MIN_DISTINCT_COLUMN_CLASSES,
    REQUIRED_COLUMN_CLASSES,
    ColumnClass,
    FieldScope,
    match_header,
)
from app.parsers.base import ParsedTable, ParseLimits


@dataclass(frozen=True)
class ColumnMapping:
    """一列 -> 一个行项目字段。保存命中的原始表头（SPEC §6.2 要求）。"""

    col_offset: int
    field_key: str
    header_text: str
    header_address: str


@dataclass(frozen=True)
class HeaderDetection:
    found: bool
    sheet_name: str
    header_row_offsets: tuple[int, ...]
    columns: tuple[ColumnMapping, ...]
    unmapped_headers: tuple[str, ...]
    score: int
    reason: str

    @property
    def field_to_column(self) -> dict[str, ColumnMapping]:
        return {c.field_key: c for c in self.columns}

    @property
    def first_data_row_offset(self) -> int:
        return (self.header_row_offsets[-1] + 1) if self.header_row_offsets else 0


@dataclass(frozen=True)
class _Candidate:
    rows: tuple[int, ...]
    #: col_offset -> (字段 key, 命中的表头原文)
    hits: dict[int, tuple[str, str]]
    unmapped: tuple[str, ...]

    @property
    def score(self) -> int:
        return len(self.hits)


def _cell_text(table: ParsedTable, row_offset: int, col_offset: int) -> str:
    row = table.cells[row_offset]
    if col_offset >= len(row):
        return ""
    value = row[col_offset].value_raw
    return value or ""


def _cell_address(table: ParsedTable, row_offset: int, col_offset: int) -> str:
    row = table.cells[row_offset]
    if col_offset >= len(row):
        return ""
    return row[col_offset].ref.address


def _width(table: ParsedTable) -> int:
    return max((len(row) for row in table.cells), default=0)


def _build_candidate(table: ParsedTable, rows: tuple[int, ...]) -> _Candidate:
    """为一个候选表头（单行或双行）计算命中。

    双行候选按顺序尝试三种文本：拼接、下行单独、上行单独。
    这样既覆盖「跨两行表头」，也覆盖「上行是合并大标题、真表头在下行」。
    全程只做归一化后精确字典查找，**不做子串包含、不做模糊匹配**。
    """
    hits: dict[int, tuple[str, str]] = {}
    unmapped: list[str] = []

    for col in range(_width(table)):
        texts = [_cell_text(table, r, col) for r in rows]
        candidates: list[str] = []
        if len(texts) > 1:
            joined = " ".join(t for t in texts if t).strip()
            if joined:
                candidates.append(joined)
            candidates.extend(t for t in reversed(texts) if t)
        elif texts and texts[0]:
            candidates.append(texts[0])

        matched: tuple[str, str] | None = None
        for text in candidates:
            key = match_header(text, FieldScope.LINE_ITEM)
            if key is not None:
                matched = (key, text)
                break

        if matched is not None:
            hits[col] = matched
        elif candidates:
            unmapped.append(candidates[0])

    return _Candidate(rows=rows, hits=hits, unmapped=tuple(unmapped))


def _resolve_conflicts(candidate: _Candidate) -> tuple[dict[int, tuple[str, str]], list[str]]:
    """同一字段被多列命中 -> 两边都不映射（SPEC §6.2）。"""
    counts = Counter(key for key, _ in candidate.hits.values())
    kept: dict[int, tuple[str, str]] = {}
    dropped: list[str] = []
    for col, (key, text) in candidate.hits.items():
        if counts[key] > 1:
            dropped.append(text)
        else:
            kept[col] = (key, text)
    return kept, dropped


def _passes_threshold(field_keys: set[str]) -> tuple[bool, str]:
    classes = {COLUMN_CLASS_BY_FIELD.get(key, ColumnClass.OTHER) for key in field_keys}
    if len(classes) < MIN_DISTINCT_COLUMN_CLASSES:
        return False, f"仅命中 {len(classes)} 个列类，少于门槛 {MIN_DISTINCT_COLUMN_CLASSES}"
    missing = REQUIRED_COLUMN_CLASSES - classes
    if missing:
        names = "、".join(sorted(c.value for c in missing))
        return False, f"缺少必需列类：{names}（必须同时有数量类与单价/金额类）"
    return True, "命中数量类与单价/金额类，达到门槛"


def detect_header(table: ParsedTable, limits: ParseLimits) -> HeaderDetection:
    """在一个工作表里定位行项目表的表头。"""
    row_count = len(table.cells)
    if row_count == 0:
        return HeaderDetection(
            found=False,
            sheet_name=table.sheet_name,
            header_row_offsets=(),
            columns=(),
            unmapped_headers=(),
            score=0,
            reason="工作表为空",
        )

    scan_limit = min(row_count, limits.max_header_scan_rows)

    candidates: list[_Candidate] = [_build_candidate(table, (i,)) for i in range(scan_limit)]
    candidates.extend(
        _build_candidate(table, (i, i + 1)) for i in range(min(scan_limit, row_count - 1))
    )

    # 确定性排序：分数降序 -> 单行优先 -> 行号升序。绝不依赖列表构造顺序。
    candidates.sort(key=lambda c: (-c.score, len(c.rows), c.rows[0]))
    best = candidates[0]

    if best.score == 0:
        return HeaderDetection(
            found=False,
            sheet_name=table.sheet_name,
            header_row_offsets=(),
            columns=(),
            unmapped_headers=best.unmapped,
            score=0,
            reason="前若干行没有任何单元格命中已知表头别名",
        )

    kept, dropped = _resolve_conflicts(best)
    ok, reason = _passes_threshold({key for key, _ in kept.values()})

    columns = tuple(
        ColumnMapping(
            col_offset=col,
            field_key=key,
            header_text=text,
            header_address=_cell_address(table, best.rows[-1], col),
        )
        for col, (key, text) in sorted(kept.items())
    )
    unmapped = tuple(sorted({*best.unmapped, *dropped}))

    return HeaderDetection(
        found=ok,
        sheet_name=table.sheet_name,
        header_row_offsets=best.rows if ok else (),
        columns=columns if ok else (),
        unmapped_headers=unmapped,
        score=len(kept),
        reason=reason,
    )


def best_header(
    tables: tuple[ParsedTable, ...], limits: ParseLimits
) -> tuple[ParsedTable | None, HeaderDetection | None]:
    """多工作表时选命中最强的那个表（覆盖「表在第二个 sheet」的版面变体）。

    并列时取靠前的工作表——确定性优先。
    """
    best_pair: tuple[ParsedTable, HeaderDetection] | None = None
    for table in tables:
        detection = detect_header(table, limits)
        if not detection.found:
            continue
        if best_pair is None or detection.score > best_pair[1].score:
            best_pair = (table, detection)
    if best_pair is None:
        return None, None
    return best_pair
