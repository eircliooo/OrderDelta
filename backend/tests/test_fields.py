"""FieldSpec 注册表单测。SPEC §4、§6.2、§9.4。

三条负例是 SPEC §6.2 明文要求的——它们对应真实外贸单据里最常见的三种误命中，
一旦发生，每行都会报假 CALCULATION_ERROR。
"""

from __future__ import annotations

import pytest

from app.domain.enums import ChainStage, DocumentRole, Severity
from app.domain.fields import (
    ALL_FIELDS,
    DOCUMENT_ALIAS_INDEX,
    LINE_ITEM_ALIAS_INDEX,
    REQUIRED_COLUMN_CLASSES,
    ColumnClass,
    FieldScope,
    match_header,
    render_comparison_rules_md,
    severity_for,
    sku_presence_severity,
)


class TestAliasNegativeCases:
    """SPEC §6.2 明文要求的三条负例。"""

    def test_total_price_不得命中_unit_price(self) -> None:
        assert match_header("Total Price", FieldScope.LINE_ITEM) == "line_total"

    def test_箱数量_不得命中_quantity(self) -> None:
        assert match_header("箱数量", FieldScope.LINE_ITEM) != "quantity"

    def test_unit_price_不得命中_unit(self) -> None:
        assert match_header("Unit Price", FieldScope.LINE_ITEM) == "unit_price"

    def test_裸别名_price_已删除(self) -> None:
        """裸 price 会命中 Total Price 这类表头，SPEC §6.2 要求删除。"""
        assert match_header("price", FieldScope.LINE_ITEM) is None


class TestAliasMatching:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("QTY", "quantity"),
            ("Q'ty", "quantity"),
            ("数量", "quantity"),
            ("订购数量", "quantity"),
            ("Unit Price (USD)", "unit_price"),
            ("单价（USD）", "unit_price"),
            ("U/P", "unit_price"),
            ("Amount", "line_total"),
            ("金额", "line_total"),
            ("小计", "line_total"),
            ("Item No.", "internal_sku"),
            ("型号", "internal_sku"),
            ("Customer Part No.", "customer_part_number"),
            ("客户料号", "customer_part_number"),
            ("Description", "description"),
            ("品名", "description"),
            ("Color", "color"),
            ("颜色", "color"),
            ("CTNS", "carton_count"),
            ("箱数", "carton_count"),
            ("PCS/CTN", "packaging_quantity"),
            ("装箱数量", "packaging_quantity"),
            ("Unit", "unit"),
            ("单位", "unit"),
        ],
    )
    def test_命中(self, header: str, expected: str) -> None:
        assert match_header(header, FieldScope.LINE_ITEM) == expected

    def test_全角半角统一(self) -> None:
        assert match_header("ＱＴＹ", FieldScope.LINE_ITEM) == "quantity"

    def test_大小写与空白无关(self) -> None:
        assert match_header("  uNiT   pRiCe  ", FieldScope.LINE_ITEM) == "unit_price"

    def test_未知表头返回none(self) -> None:
        assert match_header("客户特殊要求编码", FieldScope.LINE_ITEM) is None

    def test_禁止子串包含(self) -> None:
        """'数' 是 '数量' 的子串，但精确查找必须不命中。"""
        assert match_header("数", FieldScope.LINE_ITEM) is None

    def test_文档级与行项目级索引互不污染(self) -> None:
        """'total amount' 在表头里是行金额，在表尾是总金额。"""
        assert match_header("Total Amount", FieldScope.LINE_ITEM) == "line_total"
        assert match_header("Total Amount", FieldScope.DOCUMENT) == "grand_total"


class TestRegistryIntegrity:
    def test_别名索引非空(self) -> None:
        assert len(LINE_ITEM_ALIAS_INDEX) > 50
        assert len(DOCUMENT_ALIAS_INDEX) > 50

    def test_必需列类被两个以上字段覆盖(self) -> None:
        classes = {spec.column_class for spec in ALL_FIELDS if spec.scope is FieldScope.LINE_ITEM}
        assert classes >= REQUIRED_COLUMN_CLASSES
        assert ColumnClass.QUANTITY in classes
        assert ColumnClass.PRICE_OR_AMOUNT in classes

    def test_每个字段都有三段严重度(self) -> None:
        for spec in ALL_FIELDS:
            for stage in (
                ChainStage.OFFER_TO_ORDER,
                ChainStage.ORDER_TO_CONFIRMATION,
                ChainStage.OFFER_TO_CONFIRMATION,
            ):
                assert stage in spec.severity_by_stage, f"{spec.key} 缺 {stage}"


class TestSeverityByChainStage:
    """SPEC §9.4：本次修订在领域层面最重要的一条。"""

    def test_买方砍价不是critical(self) -> None:
        """Q→PO 单价变化是买方下单行为，一律 CRITICAL 会淹没真正致命的错误。"""
        assert (
            severity_for("unit_price", FieldScope.LINE_ITEM, ChainStage.OFFER_TO_ORDER)
            is Severity.INFO
        )

    def test_卖方确认环节单价不同是critical(self) -> None:
        assert (
            severity_for("unit_price", FieldScope.LINE_ITEM, ChainStage.ORDER_TO_CONFIRMATION)
            is Severity.CRITICAL
        )

    def test_同一字段在不同阶段严重度不同(self) -> None:
        a = severity_for("quantity", FieldScope.LINE_ITEM, ChainStage.OFFER_TO_ORDER)
        b = severity_for("quantity", FieldScope.LINE_ITEM, ChainStage.ORDER_TO_CONFIRMATION)
        assert a is not b

    def test_文档内恒等式恒为critical(self) -> None:
        assert (
            severity_for("line_total", FieldScope.LINE_ITEM, ChainStage.WITHIN_DOCUMENT)
            is Severity.CRITICAL
        )

    def test_交期不是一律critical(self) -> None:
        """原计划把「交期不同」一律定为 CRITICAL；PI 上的交期常是估计值。"""
        assert (
            severity_for("delivery_terms", FieldScope.DOCUMENT, ChainStage.OFFER_TO_ORDER)
            is not Severity.CRITICAL
        )


class TestSkuPresenceSeverity:
    ALL = frozenset(DocumentRole)

    def test_仅报价单出现是info(self) -> None:
        """报价单是菜单，客户只订一部分是正常业务。"""
        assert sku_presence_severity(frozenset({DocumentRole.QUOTATION}), self.ALL) is Severity.INFO

    def test_已下单但pi漏货是critical(self) -> None:
        assert (
            sku_presence_severity(
                frozenset({DocumentRole.QUOTATION, DocumentRole.PURCHASE_ORDER}),
                self.ALL,
            )
            is Severity.CRITICAL
        )

    def test_未经报价的成交项是review(self) -> None:
        assert (
            sku_presence_severity(
                frozenset({DocumentRole.PURCHASE_ORDER, DocumentRole.PROFORMA_INVOICE}),
                self.ALL,
            )
            is Severity.REVIEW
        )

    def test_两份文件场景下未参与角色不算缺失(self) -> None:
        """SPEC §1.3：只上传 PO+PI 时，「Q 里没有」不是缺失，是根本没参与。"""
        compared = frozenset({DocumentRole.PURCHASE_ORDER, DocumentRole.PROFORMA_INVOICE})
        present = frozenset({DocumentRole.PURCHASE_ORDER, DocumentRole.PROFORMA_INVOICE})
        assert sku_presence_severity(present, compared) is Severity.INFO


def test_comparison_rules_可生成且含关键内容() -> None:
    """硬约束 #12：docs/comparison-rules.md 由注册表生成，禁止手写。"""
    text = render_comparison_rules_md()
    assert "禁止手写" in text
    assert "`unit_price`" in text
    assert "Q→PO" in text
    assert "SKU 存在性有向表" in text
    for spec in ALL_FIELDS:
        assert f"`{spec.key}`" in text
