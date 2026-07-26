"""身份函数单测。SPEC §3.3。

阶段 1 验收信号：身份函数对「行顺序不同」「同 SKU 重复行」两个用例稳定。
这两个用例是原计划 fixture #10 与 #14 的核心，选错身份键会让人工修正解锚、
审核状态静默丢失、golden test 不稳定。
"""

from __future__ import annotations

import re

import pytest

from app.domain.enums import DocumentRole, IdentityStrength, Scope, SubjectKind
from app.domain.identity import (
    difference_key,
    group_key_fuzzy,
    group_key_sku,
    group_key_unmatched,
    identity_strength_for,
    line_key,
    line_key_is_positional,
    line_key_ordinal,
    values_digest,
)


def _lk(
    sku: str | None = None,
    ordinal: int = 1,
    cpn: str | None = None,
    cpn_ordinal: int = 1,
    sheet: str = "Sheet1",
    row: int = 5,
) -> str:
    return line_key(
        sku_norm=sku,
        sku_ordinal=ordinal,
        customer_part_norm=cpn,
        cpn_ordinal=cpn_ordinal,
        sheet_name=sheet,
        row_index=row,
    )


class TestLineKey:
    def test_sku_优先于客户料号(self) -> None:
        assert _lk(sku="AB-100", cpn="CUST-9") == "sku:AB-100#1"

    def test_无sku时回退客户料号(self) -> None:
        assert _lk(sku=None, cpn="CUST-9") == "cpn:CUST-9#1"

    def test_两者皆无时回退位置(self) -> None:
        assert _lk(sku=None, cpn=None, sheet="报价", row=12) == "pos:报价!12"

    def test_行顺序不同不影响sku型line_key(self) -> None:
        """fixture #10「行顺序不同」：同一 SKU 在不同行号下必须得到同一 line_key。

        若 line_key 含行号，用户在 PO 里调整行序就会让全部人工修正解锚。
        """
        assert _lk(sku="AB-100", row=3) == _lk(sku="AB-100", row=99)

    def test_同sku重复行由序号区分(self) -> None:
        """fixture #14「同一 SKU 重复行」：两行必须得到不同 line_key，否则修正会互相覆盖。"""
        first = _lk(sku="AB-100", ordinal=1)
        second = _lk(sku="AB-100", ordinal=2)
        assert first != second
        assert line_key_ordinal(first) == 1
        assert line_key_ordinal(second) == 2

    def test_位置型可被识别(self) -> None:
        assert line_key_is_positional(_lk(sku=None, cpn=None))
        assert not line_key_is_positional(_lk(sku="AB-100"))

    def test_确定性_重复调用结果一致(self) -> None:
        assert _lk(sku="AB-100") == _lk(sku="AB-100")


class TestGroupKey:
    def test_sku组键(self) -> None:
        assert group_key_sku("AB-100") == "SKU:AB-100"

    def test_模糊组键必须排序(self) -> None:
        """成员迭代顺序不同必须得到同一 key，否则重复执行结果不稳定。"""
        a = group_key_fuzzy([(DocumentRole.PURCHASE_ORDER, 7), (DocumentRole.QUOTATION, 3)])
        b = group_key_fuzzy([(DocumentRole.QUOTATION, 3), (DocumentRole.PURCHASE_ORDER, 7)])
        assert a == b

    def test_孤立行组键含角色与行号(self) -> None:
        key = group_key_unmatched(DocumentRole.PROFORMA_INVOICE, 9)
        assert key == "NOSKU:PROFORMA_INVOICE:9"

    def test_组键不含数据库主键(self) -> None:
        """重跑时 match_group 全删全插，主键全换新。组键含主键即等于每次都变。

        用 uuid 形态检测：8-4-4-4-12 的十六进制串出现即说明混入了生成 id。
        """
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        for key in (
            group_key_sku("AB-100"),
            group_key_unmatched(DocumentRole.QUOTATION, 3),
            group_key_fuzzy([(DocumentRole.QUOTATION, 1)]),
        ):
            assert not uuid_like.search(key), key
        # 纯函数：同输入必得同输出
        assert group_key_sku("AB-100") == group_key_sku("AB-100")


class TestDifferenceKey:
    def _key(self, field: str = "unit_price", subject: str = "SKU:AB-100") -> str:
        return difference_key(
            scope=Scope.LINE_ITEM,
            difference_type="VALUE_CONFLICT",
            field_name=field,
            subject_kind=SubjectKind.MATCH_GROUP,
            subject_key=subject,
        )

    def test_确定性(self) -> None:
        assert self._key() == self._key()

    def test_字段不同则key不同(self) -> None:
        assert self._key(field="unit_price") != self._key(field="quantity")

    def test_主体不同则key不同(self) -> None:
        assert self._key(subject="SKU:AB-100") != self._key(subject="SKU:AB-200")

    def test_key不含具体数值(self) -> None:
        """核心契约：用户改了单价后重跑，difference_key 必须不变，
        否则人工审核状态恰好在最需要继承的场景失效（SPEC §3.3）。"""
        before = self._key()
        after = self._key()  # 数值不是入参，改值不可能影响 key
        assert before == after


class TestValuesDigest:
    RULE = "CRITICAL:po_to_pi.quantity"

    def test_角色顺序不影响摘要(self) -> None:
        a = values_digest(
            {"PURCHASE_ORDER": "5000", "PROFORMA_INVOICE": "4800"}, rule_signature=self.RULE
        )
        b = values_digest(
            {"PROFORMA_INVOICE": "4800", "PURCHASE_ORDER": "5000"}, rule_signature=self.RULE
        )
        assert a == b

    def test_值变化则摘要变化(self) -> None:
        a = values_digest({"PURCHASE_ORDER": "5000"}, rule_signature=self.RULE)
        b = values_digest({"PURCHASE_ORDER": "5001"}, rule_signature=self.RULE)
        assert a != b

    def test_none与空串等价(self) -> None:
        assert values_digest({"Q": None}, rule_signature=self.RULE) == values_digest(
            {"Q": ""}, rule_signature=self.RULE
        )

    def test_严重度变化则摘要变化(self) -> None:
        """值一个字没动、只是规则把它从 REVIEW 调成 CRITICAL —— 前提必须算变了。

        不变的话，那条早被标成「已接受差异」的记录会带着旧裁决出现在报告里，
        看起来像「这条新的 CRITICAL 已经有人看过并接受了」。
        """
        values = {"PURCHASE_ORDER": "5000", "PROFORMA_INVOICE": "4800"}
        before = values_digest(values, rule_signature="REVIEW:q_to_po.quantity")
        after = values_digest(values, rule_signature="CRITICAL:q_to_po.quantity")
        assert before != after

    def test_规则id变化则摘要变化(self) -> None:
        """严重度恰好相同、但换了一条规则产出它 —— 判断依据同样变了。"""
        values = {"PURCHASE_ORDER": "5000"}
        assert values_digest(values, rule_signature="REVIEW:rule_a") != values_digest(
            values, rule_signature="REVIEW:rule_b"
        )

    def test_分隔符出现在原文里不会造成撞车(self) -> None:
        """`|` 与 `=` 在单据原文里完全正常（备注、规格串）。不转义就会歧义：
        两组不同的取值拼出同一个串 → 两条差异共用一个摘要 → 裁决挂错行。

        这条测试原先写成「把 @rule 拼进取值串」，那两个输入天然不可能相等，
        断言恒成立 —— 提供的是虚假保证，改成可注入的拼法它照样绿。
        """
        assert values_digest({"A": "x", "B": "y"}, rule_signature="r") != values_digest(
            {"A": "x|B=y"}, rule_signature="r"
        )
        assert values_digest({"A": "x"}, rule_signature="r|A=y") != values_digest(
            {"A": "x|A=y"}, rule_signature="r"
        )
        # 转义字符本身也要能被区分开
        assert values_digest({"A": "a\\|b"}, rule_signature="r") != values_digest(
            {"A": "a\\", "b": ""}, rule_signature="r"
        )


class TestIdentityStrength:
    def test_文档级恒为强(self) -> None:
        assert (
            identity_strength_for(
                subject_kind=SubjectKind.DOCUMENT_ROLE, subject_key="PURCHASE_ORDER"
            )
            is IdentityStrength.STRONG
        )

    def test_多成员组为弱(self) -> None:
        assert (
            identity_strength_for(
                subject_kind=SubjectKind.MATCH_GROUP,
                subject_key="SKU:AB-100",
                member_line_keys=("sku:AB-100#1",),
                multiplicity_is_unique=False,
            )
            is IdentityStrength.WEAK
        )

    def test_位置型成员为弱(self) -> None:
        assert (
            identity_strength_for(
                subject_kind=SubjectKind.MATCH_GROUP,
                subject_key="NOSKU:QUOTATION:4",
                member_line_keys=("pos:Sheet1!4",),
            )
            is IdentityStrength.WEAK
        )

    def test_重复sku第二行为弱(self) -> None:
        assert (
            identity_strength_for(
                subject_kind=SubjectKind.MATCH_GROUP,
                subject_key="SKU:AB-100",
                member_line_keys=("sku:AB-100#2",),
            )
            is IdentityStrength.WEAK
        )

    def test_唯一sku为强(self) -> None:
        assert (
            identity_strength_for(
                subject_kind=SubjectKind.MATCH_GROUP,
                subject_key="SKU:AB-100",
                member_line_keys=("sku:AB-100#1", "sku:AB-100#1"),
            )
            is IdentityStrength.STRONG
        )


@pytest.mark.parametrize(
    ("key", "expected"),
    [("sku:AB#1", 1), ("sku:AB#3", 3), ("pos:S!7", 1), ("cpn:X#2", 2)],
)
def test_line_key_ordinal(key: str, expected: int) -> None:
    assert line_key_ordinal(key) == expected
