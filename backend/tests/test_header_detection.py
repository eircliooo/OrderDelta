"""表头定位与行项目提取单测。SPEC §6.1、§16.6。

三个「脏 XLSX」版面是 SPEC §16.6 明文要求的解析器单测输入。它们代表真实外贸单据的
三种典型版面，只断言 header_row / column_map / LineItem 条数——**小计行不得进 LineItem**。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.extraction.header import best_header, detect_header
from app.extraction.line_items import extract_line_items
from app.parsers.base import DEFAULT_LIMITS
from app.parsers.xlsx import XlsxParser
from tests.conftest import (
    DIRTY_A,
    DIRTY_A_MERGES,
    DIRTY_B,
    DIRTY_B_MERGES,
    DIRTY_C,
    document_input,
    write_xlsx,
)


def _parse(tmp_path: Path, name: str, sheets, merges=None):  # type: ignore[no-untyped-def]
    src = document_input(write_xlsx(tmp_path / name, sheets, merges))
    return XlsxParser().parse(src)


class TestDirtyA:
    """前 5 行公司抬头 + 合并大标题。"""

    def test_跳过抬头正确定位表头(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "a.xlsx", DIRTY_A, DIRTY_A_MERGES)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        assert detection.found
        # 表头在第 9 个 Excel 行 -> grid offset 8
        assert detection.header_row_offsets == (8,)

    def test_六列全部映射(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "a.xlsx", DIRTY_A, DIRTY_A_MERGES)
        _, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert detection is not None
        assert set(detection.field_to_column) == {
            "internal_sku",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "line_total",
        }

    def test_合计行不进line_item(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "a.xlsx", DIRTY_A, DIRTY_A_MERGES)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        result = extract_line_items(table, detection)
        assert len(result.items) == 3
        assert [i.sku_norm for i in result.items] == ["AB-100", "AB-200", "AB-300"]

    def test_数值走decimal(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "a.xlsx", DIRTY_A, DIRTY_A_MERGES)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        first = extract_line_items(table, detection).items[0]
        assert Decimal(first.cells["unit_price"].normalized or "") == Decimal("1.25")
        assert Decimal(first.cells["quantity"].normalized or "") == Decimal("1000")


class TestDirtyB:
    """双行表头 + 单位行。"""

    def test_识别双行表头(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "b.xlsx", DIRTY_B, DIRTY_B_MERGES)
        _, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert detection is not None and detection.found
        assert len(detection.header_row_offsets) == 2
        assert detection.header_row_offsets == (2, 3)

    def test_跨行与合并表头都被映射(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "b.xlsx", DIRTY_B, DIRTY_B_MERGES)
        _, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert detection is not None
        mapped = set(detection.field_to_column)
        assert {"packaging_quantity", "carton_count", "unit_price", "line_total"} <= mapped
        assert "quantity" in mapped

    def test_单位行不终止扫描(self, tmp_path: Path) -> None:
        """表头正下方的 PCS/USD 单位行如果被当成终止信号，整张表会被判空。"""
        doc = _parse(tmp_path, "b.xlsx", DIRTY_B, DIRTY_B_MERGES)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        result = extract_line_items(table, detection)
        assert len(result.items) == 2
        assert result.terminated_at_row is None


class TestDirtyC:
    """中段空行 + 尾部小计/运费/合计 + 纯中文表头。"""

    def test_中文表头全部命中(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "c.xlsx", DIRTY_C)
        _, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert detection is not None and detection.found
        assert set(detection.field_to_column) == {
            "internal_sku",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "line_total",
            "remarks",
        }

    def test_中段空行不终止且尾部三行不进line_item(self, tmp_path: Path) -> None:
        doc = _parse(tmp_path, "c.xlsx", DIRTY_C)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        result = extract_line_items(table, detection)
        assert len(result.items) == 3
        skus = [i.sku_norm for i in result.items]
        assert skus == ["AB-100", "AB-200", "AB-300"]
        # 小计 / 运费 / 合计 三行都不得成为行项目
        assert all("小计" not in (i.cells["description"].raw_text or "") for i in result.items)


class TestThreshold:
    def test_缺数量列时不认为找到表格(self, tmp_path: Path) -> None:
        """硬门槛：必须同时命中数量类与单价/金额类。"""
        sheets = {
            "S": [
                ["Item No.", "Description", "Unit Price", "Amount"],
                ["AB-100", "Mug", "1.25", "1250.00"],
            ]
        }
        doc = _parse(tmp_path, "no_qty.xlsx", sheets)
        detection = detect_header(doc.tables[0], DEFAULT_LIMITS)
        assert not detection.found
        assert "数量" in detection.reason

    def test_缺价格列时不认为找到表格(self, tmp_path: Path) -> None:
        sheets = {"S": [["Item No.", "Description", "Qty"], ["AB-100", "Mug", 10]]}
        doc = _parse(tmp_path, "no_price.xlsx", sheets)
        detection = detect_header(doc.tables[0], DEFAULT_LIMITS)
        assert not detection.found

    def test_同字段被多列命中则两边都不映射(self, tmp_path: Path) -> None:
        """SPEC §6.2：宁可少提取，也不要挑错列。"""
        sheets = {
            "S": [
                ["Item No.", "Qty", "Quantity", "Unit Price", "Amount"],
                ["AB-100", 10, 20, "1.25", "12.50"],
            ]
        }
        doc = _parse(tmp_path, "dup.xlsx", sheets)
        detection = detect_header(doc.tables[0], DEFAULT_LIMITS)
        assert "quantity" not in detection.field_to_column
        assert not detection.found  # 丢掉数量列后达不到硬门槛
        assert any("Qty" in h or "Quantity" in h for h in detection.unmapped_headers)

    def test_未知表头进入unmapped(self, tmp_path: Path) -> None:
        sheets = {
            "S": [
                ["Item No.", "Qty", "Unit Price", "客户特殊编码"],
                ["AB-100", 10, "1.25", "X1"],
            ]
        }
        doc = _parse(tmp_path, "unmapped.xlsx", sheets)
        detection = detect_header(doc.tables[0], DEFAULT_LIMITS)
        assert detection.found
        assert "客户特殊编码" in detection.unmapped_headers


class TestMultiSheet:
    def test_表在第二个sheet也能找到(self, tmp_path: Path) -> None:
        sheets = {
            "封面": [["公司简介"], ["我们是一家..."]],
            "报价": [
                ["Item No.", "Description", "Qty", "Unit Price", "Amount"],
                ["AB-100", "Mug", 1000, "1.25", "1250.00"],
            ],
        }
        doc = _parse(tmp_path, "multi.xlsx", sheets)
        table, detection = best_header(doc.tables, DEFAULT_LIMITS)
        assert table is not None and detection is not None
        assert table.sheet_name == "报价"


class TestDeterminism:
    def test_重复解析结果一致(self, tmp_path: Path) -> None:
        """硬约束 #5：同一输入重复执行结果必须稳定。"""
        first = _parse(tmp_path, "d1.xlsx", DIRTY_C)
        second = _parse(tmp_path, "d2.xlsx", DIRTY_C)
        d1 = detect_header(first.tables[0], DEFAULT_LIMITS)
        d2 = detect_header(second.tables[0], DEFAULT_LIMITS)
        assert d1.header_row_offsets == d2.header_row_offsets
        assert [c.field_key for c in d1.columns] == [c.field_key for c in d2.columns]
        assert d1.unmapped_headers == d2.unmapped_headers
