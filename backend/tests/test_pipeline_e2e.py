"""纵向闭环端到端测试。SPEC §19 阶段 2 验收信号。

两组手写 fixture：
  1. 三份完全一致  -> **CRITICAL 误报必须为 0**（Gate-0 第 5 条，最重要的一条）
  2. PO 改数量 + PI 改单价但没改金额 -> 精确命中三条关键差异

外加两文件场景（PO+PI）：缺席角色不得产生任何差异。
"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import (
    DifferenceType,
    DocumentRole,
    EvidenceSourceType,
    Scope,
    Severity,
)
from app.pipeline import ProjectResult, process_document, run_project
from tests.conftest import (
    BASE_GRAND_TOTAL,
    BASE_ITEMS,
    document_input,
    order_rows,
    write_xlsx,
)

_TITLES = {
    DocumentRole.QUOTATION: ("QUOTATION", "Quotation No.", "Q2026-001"),
    DocumentRole.PURCHASE_ORDER: ("PURCHASE ORDER", "PO No.", "PO-8899"),
    DocumentRole.PROFORMA_INVOICE: ("PROFORMA INVOICE", "PI No.", "PI-2026-001"),
}


def _build(
    tmp_path: Path,
    role: DocumentRole,
    *,
    items: list[tuple[str, str, int, str, str, str]] | None = None,
    grand_total: str = BASE_GRAND_TOTAL,
    **kwargs: str,
) -> tuple[str, object]:
    title, label, number = _TITLES[role]
    rows = order_rows(
        title=title,
        doc_label=label,
        doc_no=number,
        date="2026-07-15",
        items=items if items is not None else BASE_ITEMS,
        grand_total=grand_total,
        **kwargs,
    )
    path = write_xlsx(tmp_path / f"{role.value.lower()}.xlsx", {title[:20]: rows})
    return role.value.lower(), document_input(path)


def _run(tmp_path: Path, roles: dict[DocumentRole, dict[str, object]]) -> ProjectResult:
    processed = {}
    for role, kwargs in roles.items():
        doc_id, src = _build(tmp_path, role, **kwargs)  # type: ignore[arg-type]
        processed[role] = process_document(document_id=doc_id, role=role, src=src)  # type: ignore[arg-type]
    return run_project("proj-test", processed)


def _all_roles() -> dict[DocumentRole, dict[str, object]]:
    """三份文件、全部用默认内容。每次返回**新的** dict，避免共享可变默认值。"""
    return {role: {} for role in DocumentRole}


def _find(
    result: ProjectResult,
    *,
    field: str | None = None,
    subject: str | None = None,
    dtype: DifferenceType | None = None,
) -> list:
    return [
        d
        for d in result.comparison.differences
        if (field is None or d.field_name == field)
        and (subject is None or d.subject_key == subject)
        and (dtype is None or d.difference_type is dtype)
    ]


class TestIdenticalDocuments:
    """fixture #1：三份文件完全一致。"""

    def test_全部文档可用(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        assert set(result.snapshot.documents) == set(DocumentRole)
        for role, processing in result.processing.items():
            assert processing.usable, f"{role} 未能产出快照：{processing.parsed.reason_code}"

    def test_每份文档都提取到三行(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        for doc in result.snapshot.documents.values():
            assert len(doc.line_items) == 3
            assert [i.sku_norm for i in doc.line_items] == ["AB-100", "AB-200", "AB-300"]

    def test_文档级字段被提取(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        po = result.snapshot.documents[DocumentRole.PURCHASE_ORDER]
        assert po.get("currency").value == "USD"
        assert po.get("incoterm").value == "FOB"
        assert po.get("incoterm_named_place").value == "Ningbo"
        assert po.get("grand_total").value == "3070.00"
        assert po.get("document_date").value == "2026-07-15"

    def test_critical误报为零(self, tmp_path: Path) -> None:
        """Gate-0 第 5 条：这是全套验收里最重要的一条。"""
        result = _run(tmp_path, _all_roles())
        criticals = [d for d in result.comparison.differences if d.severity is Severity.CRITICAL]
        assert criticals == [], [
            (d.field_name, d.subject_key, d.explanation_params) for d in criticals
        ]

    def test_warning误报为零(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        warnings = [d for d in result.comparison.differences if d.severity is Severity.WARNING]
        assert warnings == [], [(d.field_name, d.subject_key) for d in warnings]

    def test_算术校验全部通过(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        assert _find(result, dtype=DifferenceType.CALCULATION_ERROR) == []

    def test_三行全部对齐(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _all_roles())
        assert len(result.groups) == 3
        assert all(len(g.members) == 3 for g in result.groups)
        assert _find(result, dtype=DifferenceType.UNMATCHED_LINE_ITEM) == []


class TestPlantedDifferences:
    """fixture #2/#3：PO 改数量、PI 改单价但没同步金额。"""

    @staticmethod
    def _roles() -> dict[DocumentRole, dict[str, object]]:
        po_items = [
            ("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"),
            *BASE_ITEMS[1:],
        ]
        pi_items = [
            BASE_ITEMS[0],
            # 单价改了但金额没跟着改 —— 真实世界最常见的一种错
            ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1200.00"),
            BASE_ITEMS[2],
        ]
        return {
            DocumentRole.QUOTATION: {},
            DocumentRole.PURCHASE_ORDER: {
                "items": po_items,
                "grand_total": "3320.00",
            },
            DocumentRole.PROFORMA_INVOICE: {"items": pi_items},
        }

    def test_数量冲突为critical(self, tmp_path: Path) -> None:
        result = _run(tmp_path, self._roles())
        hits = _find(result, field="quantity", subject="SKU:AB-100")
        assert len(hits) == 1
        assert hits[0].difference_type is DifferenceType.VALUE_CONFLICT
        assert hits[0].severity is Severity.CRITICAL
        assert hits[0].chain_stage.value == "ORDER_TO_CONFIRMATION"

    def test_单价冲突为critical(self, tmp_path: Path) -> None:
        result = _run(tmp_path, self._roles())
        hits = _find(result, field="unit_price", subject="SKU:AB-200")
        assert len(hits) == 1
        assert hits[0].severity is Severity.CRITICAL

    def test_pi内部算术错误被抓到(self, tmp_path: Path) -> None:
        """500 × 2.50 = 1250，表上写 1200。"""
        hits = _find(
            _run(tmp_path, self._roles()),
            dtype=DifferenceType.CALCULATION_ERROR,
        )
        line_errors = [
            h for h in hits if h.scope is Scope.CALCULATION and h.field_name == "line_total"
        ]
        assert len(line_errors) == 1
        assert line_errors[0].severity is Severity.CRITICAL
        assert line_errors[0].explanation_params["expected"] == "1250.00"
        assert line_errors[0].explanation_params["actual"] == "1200.00"

    def test_一条冲突只产出一条差异(self, tmp_path: Path) -> None:
        """N 元分桶：绝不两两组合产出 3 条，否则总览计数翻三倍。"""
        result = _run(tmp_path, self._roles())
        keys = [d.difference_key for d in result.comparison.differences]
        assert len(keys) == len(set(keys))

    def test_每条差异都有证据(self, tmp_path: Path) -> None:
        """Gate-0 第 9 条全量断言。"""
        result = _run(tmp_path, self._roles())
        evidence = result.evidence
        for diff in result.comparison.differences:
            assert diff.evidence_ids, f"{diff.field_name}@{diff.subject_key} 没有证据"
            for eid in diff.evidence_ids:
                assert eid in evidence, f"证据 {eid} 不存在"

    def test_确定性_重复运行结果一致(self, tmp_path: Path) -> None:
        """Gate-0 第 15 条。"""
        first = _run(tmp_path / "a", self._roles())
        second = _run(tmp_path / "b", self._roles())
        assert [d.difference_key for d in first.comparison.differences] == [
            d.difference_key for d in second.comparison.differences
        ]
        assert [d.severity for d in first.comparison.differences] == [
            d.severity for d in second.comparison.differences
        ]


class TestTwoDocumentsOnly:
    """SPEC §1.3：只有 PO + PI 也必须能跑，且缺席角色不产生差异。"""

    def test_两份文件可运行(self, tmp_path: Path) -> None:
        result = _run(
            tmp_path,
            {DocumentRole.PURCHASE_ORDER: {}, DocumentRole.PROFORMA_INVOICE: {}},
        )
        assert result.snapshot.runnable
        assert set(result.snapshot.documents) == {
            DocumentRole.PURCHASE_ORDER,
            DocumentRole.PROFORMA_INVOICE,
        }

    def test_缺席角色不产生任何差异(self, tmp_path: Path) -> None:
        """否则几十条假缺失会把真实差异彻底淹没。"""
        result = _run(
            tmp_path,
            {DocumentRole.PURCHASE_ORDER: {}, DocumentRole.PROFORMA_INVOICE: {}},
        )
        for diff in result.comparison.differences:
            assert DocumentRole.QUOTATION.value not in diff.values_by_document
            params = diff.explanation_params
            assert DocumentRole.QUOTATION.value not in params.get("missing_roles", "")

    def test_两份一致时critical为零(self, tmp_path: Path) -> None:
        result = _run(
            tmp_path,
            {DocumentRole.PURCHASE_ORDER: {}, DocumentRole.PROFORMA_INVOICE: {}},
        )
        assert [d for d in result.comparison.differences if d.severity is Severity.CRITICAL] == []


class TestUnmatchedItems:
    """SPEC §9.5：覆盖缺口不阻断比较，但要额外产出 UNMATCHED_LINE_ITEM。"""

    def test_pi遗漏sku产出critical(self, tmp_path: Path) -> None:
        result = _run(
            tmp_path,
            {
                DocumentRole.QUOTATION: {},
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {
                    "items": BASE_ITEMS[:2],
                    "grand_total": "2450.00",
                },
            },
        )
        hits = _find(result, subject="SKU:AB-300", dtype=DifferenceType.UNMATCHED_LINE_ITEM)
        assert len(hits) == 1
        # Q+PO 有而 PI 无 = 漏货
        assert hits[0].severity is Severity.CRITICAL

    def test_报价单多出的sku只是info(self, tmp_path: Path) -> None:
        """报价单是菜单，客户只订一部分是正常业务——一律 CRITICAL 会让报告不可用。"""
        result = _run(
            tmp_path,
            {
                DocumentRole.QUOTATION: {},
                DocumentRole.PURCHASE_ORDER: {
                    "items": BASE_ITEMS[:2],
                    "grand_total": "2450.00",
                },
                DocumentRole.PROFORMA_INVOICE: {
                    "items": BASE_ITEMS[:2],
                    "grand_total": "2450.00",
                },
            },
        )
        hits = _find(result, subject="SKU:AB-300", dtype=DifferenceType.UNMATCHED_LINE_ITEM)
        assert len(hits) == 1
        assert hits[0].severity is Severity.INFO


class TestUnexplainedTotalDelta:
    """CLAUDE.md 坑 #9：`sum(line_total) != grand_total` 判 REVIEW 而非 CRITICAL。

    真实 PI 几乎总有运费/折扣/模具费，判 CRITICAL 就是稳定误报。
    但 REVIEW 要**可操作**——业务员得能一眼看到那笔差额是拿哪些格子算出来的。
    """

    @staticmethod
    def _delta_case(tmp_path: Path) -> ProjectResult:
        # 行金额合计仍是 3070.00，总金额写 3200.00（差额 130.00 = 运费）
        return _run(
            tmp_path,
            {
                DocumentRole.QUOTATION: {},
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {"grand_total": "3200.00"},
            },
        )

    def test_未解释差额是review不是critical(self, tmp_path: Path) -> None:
        hits = _find(
            self._delta_case(tmp_path),
            field="grand_total",
            dtype=DifferenceType.CALCULATION_ERROR,
        )
        assert len(hits) == 1
        assert hits[0].severity is Severity.REVIEW
        assert hits[0].severity_rule_id == "grand_total@unexplained_delta"
        assert hits[0].explanation_params["delta"] == "130.00"

    def test_未解释差额的证据包含参与求和的每一行(self, tmp_path: Path) -> None:
        """SPEC §10：DERIVED 证据必须记录**参与运算的单元格** + 算式。

        只记 grand_total 一个格子，等于告诉用户「有 130 块对不上，自己去找」——
        差额恰恰要靠逐行金额才能定位。
        """
        result = self._delta_case(tmp_path)
        hit = _find(result, field="grand_total", dtype=DifferenceType.CALCULATION_ERROR)[0]
        by_id = result.evidence
        derived = [
            by_id[eid]
            for eid in hit.evidence_ids
            if eid in by_id and by_id[eid].source_type is EvidenceSourceType.DERIVED
        ]
        assert len(derived) == 1

        pi = result.snapshot.documents[DocumentRole.PROFORMA_INVOICE]
        line_total_ids = {i.get("line_total").evidence_id for i in pi.line_items}
        assert line_total_ids and None not in line_total_ids
        grand_total_id = pi.get("grand_total").evidence_id

        sources = set(derived[0].derived_from)
        assert line_total_ids <= sources, "Σ 的各行金额没进 derived_from"
        assert grand_total_id in sources
        # 算式里要说清 Σ 了几行，否则读者无从判断是不是漏加了行
        assert f"{len(pi.line_items)} 行" in derived[0].raw_text

    def test_未解释差额的证据在差异上可达(self, tmp_path: Path) -> None:
        """证据只挂在 evidence 列表里而 difference 不引用，UI 上就是看不见。"""
        result = self._delta_case(tmp_path)
        hit = _find(result, field="grand_total", dtype=DifferenceType.CALCULATION_ERROR)[0]
        pi = result.snapshot.documents[DocumentRole.PROFORMA_INVOICE]
        for item in pi.line_items:
            assert item.get("line_total").evidence_id in hit.evidence_ids
