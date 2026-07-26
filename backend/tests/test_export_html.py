"""HTML 报告导出测试。SPEC §15.2 第 2 条、§13、Gate-0 第 12 条。

安全断言（每条都对应一个真实攻击面或一个真实断网场景）：
  - autoescape 生效：单据里的 `<script>` 必须以实体出现，不得成为可执行标签
  - 零 JS：全文不含 `<script`
  - 无外部引用：全文不含 `http://` / `https://`
  - CSP meta 在 `<head>` 内
内容断言：免责声明逐字、覆盖横幅、四个计数、每条差异都渲染出证据、渲染确定性。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.domain.enums import (
    CoverageState,
    DocumentRole,
    MatchMethod,
    MultiplicityState,
    Severity,
)
from app.domain.models import MatchGroupDraft
from app.exports.html import (
    COVERAGE_WARNING,
    CSP_CONTENT,
    DISCLAIMER,
    EXPLANATION_TEMPLATES,
    PARAM_LOCALIZERS,
    _apply_template,
    build_report_view,
    localize_buckets,
    localize_group_key,
    localize_params,
    localize_prose,
    localize_role_list,
    localize_signature,
    render_explanation,
    render_report,
)
from app.extraction.snapshot import CorrectionInput
from app.pipeline import ProjectResult, process_document, run_project
from tests.conftest import (
    BASE_GRAND_TOTAL,
    BASE_ITEMS,
    document_input,
    order_rows,
    write_xlsx,
)

XSS_PAYLOAD = "<script>alert(1)</script>"
GENERATED_AT = "2026-07-26 10:30:00"

#: (工作表名, 标题, 单号标签, 单号)。工作表名刻意用中文——工作表名会作为证据的一部分
#: 原样进报告，用英文会污染「报告里不得出现英文枚举标识符」这条断言。
_TITLES = {
    DocumentRole.QUOTATION: ("报价单", "QUOTATION", "Quotation No.", "Q2026-001"),
    DocumentRole.PURCHASE_ORDER: ("采购订单", "PURCHASE ORDER", "PO No.", "PO-8899"),
    DocumentRole.PROFORMA_INVOICE: ("形式发票", "PROFORMA INVOICE", "PI No.", "PI-2026-001"),
}

#: order_rows 里「Delivery Time」之后、行项目表之前的插入点。
_REMARKS_ROW_INDEX = 10


def _build_rows(
    role: DocumentRole,
    *,
    items: list[tuple[str, str, int, str, str, str]] | None = None,
    grand_total: str = BASE_GRAND_TOTAL,
    remarks: str | None = None,
    doc_no: str | None = None,
) -> tuple[str, list[Sequence[Any]]]:
    sheet_name, title, label, number = _TITLES[role]
    rows = order_rows(
        title=title,
        doc_label=label,
        doc_no=doc_no if doc_no is not None else number,
        date="2026-07-15",
        items=items if items is not None else BASE_ITEMS,
        grand_total=grand_total,
    )
    if remarks is not None:
        rows.insert(_REMARKS_ROW_INDEX, ["Remarks", remarks])
    return sheet_name, rows


def _run(
    tmp_path: Path, roles: dict[DocumentRole, dict[str, Any]], *, project_id: str = "proj-html"
) -> ProjectResult:
    processed = {}
    for role, raw_kwargs in roles.items():
        kwargs = dict(raw_kwargs)
        corrections: tuple[CorrectionInput, ...] = tuple(kwargs.pop("corrections", ()))
        sheet_name, rows = _build_rows(role, **kwargs)
        path = write_xlsx(tmp_path / f"{role.value.lower()}.xlsx", {sheet_name: rows})
        processed[role] = process_document(
            document_id=role.value.lower(),
            role=role,
            src=document_input(path),
            corrections=corrections,
        )
    return run_project(project_id, processed)


def _three_docs_with_planted_differences(tmp_path: Path) -> ProjectResult:
    """一份「什么都有」的结果：数量冲突、单价冲突、行金额算错、备注含 XSS 载荷。"""
    po_items = [
        ("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"),
        *BASE_ITEMS[1:],
    ]
    pi_items = [
        BASE_ITEMS[0],
        # 单价改了但金额没跟着改 —— 会同时产出 VALUE_CONFLICT 与 CALCULATION_ERROR
        ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1200.00"),
        BASE_ITEMS[2],
    ]
    return _run(
        tmp_path,
        {
            DocumentRole.QUOTATION: {},
            DocumentRole.PURCHASE_ORDER: {
                "items": po_items,
                "grand_total": "3320.00",
                "remarks": "客户指定纸箱",
            },
            # 备注字段里塞攻击载荷：真实场景就是客户在单据里粘了一段 HTML
            DocumentRole.PROFORMA_INVOICE: {"items": pi_items, "remarks": XSS_PAYLOAD},
        },
    )


def _two_docs(tmp_path: Path) -> ProjectResult:
    return _run(
        tmp_path,
        {DocumentRole.PURCHASE_ORDER: {}, DocumentRole.PROFORMA_INVOICE: {}},
    )


def _severity_count(result: ProjectResult, label: str) -> int:
    view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
    return next(c.count for c in view.counts if c.label == label)


def _explanation_keys_in_source() -> set[str]:
    """AST 扫 `app/comparison`：所有写死的 `explanation_key` / `reason_key` 字面量。

    只遍历「某一次真实结果」的 key 是不够的——没被那一次走到的分支照样会在生产里
    露出一句英文 key。源码级扫描连没走到的分支一起覆盖。
    """
    root = Path(__file__).resolve().parent.parent / "app" / "comparison"
    keys: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # values.py 的 _incomparable(...) / _uncertain(...) 第一个位置参数就是 reason_key
            if name.endswith(("incomparable", "uncertain")) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
            for keyword in node.keywords:
                if keyword.arg not in ("explanation_key", "reason_key"):
                    continue
                # 走 ast.walk 是为了抓到 `result.reason_key or "incomparable"` 这种兜底写法
                keys.update(
                    sub.value
                    for sub in ast.walk(keyword.value)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                )
    return keys


# --------------------------------------------------------------------------------
# 安全
# --------------------------------------------------------------------------------


class TestSecurity:
    def test_单据里的script标签被转义为实体(self, tmp_path: Path) -> None:
        """Jinja2 的 autoescape 默认是 False，不显式打开这里就是一个存储型 XSS。"""
        result = _three_docs_with_planted_differences(tmp_path)
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)

        assert XSS_PAYLOAD not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_项目名里的html被转义(self, tmp_path: Path) -> None:
        """项目名同样是用户输入，不能因为「是我们自己的字段」就信任它。"""
        html = render_report(_two_docs(tmp_path), f"客户A{XSS_PAYLOAD}", generated_at=GENERATED_AT)
        assert XSS_PAYLOAD not in html
        assert "&lt;script&gt;" in html

    def test_生成时间里的html被转义(self, tmp_path: Path) -> None:
        html = render_report(
            _two_docs(tmp_path), "测试项目", generated_at=f"2026-01-01{XSS_PAYLOAD}"
        )
        assert XSS_PAYLOAD not in html

    def test_报告零js(self, tmp_path: Path) -> None:
        """折叠只用 <details>/<summary>，一行脚本都不许有。"""
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        lowered = html.lower()
        assert "<script" not in lowered
        assert "javascript:" not in lowered
        assert not re.search(r"\son[a-z]+\s*=", lowered), "报告里不得出现任何事件处理属性"

    def test_文件内无任何外部http引用(self, tmp_path: Path) -> None:
        """Gate-0 第 12 条机械断言：断网 + 后端停机也要能打开。"""
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        assert "http://" not in html
        assert "https://" not in html
        assert "//cdn" not in html

    def test_不引用任何外部资源标签(self, tmp_path: Path) -> None:
        """CSS 必须内联在 <style> 里：没有 <link>、没有 <img>、没有 @import。"""
        html = render_report(_two_docs(tmp_path), "测试项目", generated_at=GENERATED_AT)
        lowered = html.lower()
        assert "<link" not in lowered
        assert "<img" not in lowered
        assert "@import" not in lowered
        assert "<style>" in lowered

    def test_head内含csp_meta(self, tmp_path: Path) -> None:
        html = render_report(_two_docs(tmp_path), "测试项目", generated_at=GENERATED_AT)
        head = html.split("</head>", 1)[0]
        assert 'http-equiv="Content-Security-Policy"' in head
        assert "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\"" in head

    def test_csp内容与模块常量同源(self, tmp_path: Path) -> None:
        """安全头只能有一个来源。

        常量与模板各写一份的话，改了常量而模板照旧的那次改动没有任何东西会红——
        报告照常发出去，CSP 却是旧的。
        """
        html = render_report(_two_docs(tmp_path), "测试项目", generated_at=GENERATED_AT)
        head = html.split("</head>", 1)[0]
        assert f'content="{CSP_CONTENT}"' in head
        assert "@@CSP" not in html, "模板占位符没有被替换，报告会带着占位符发出去"

    def test_报告不泄露服务器绝对路径(self, tmp_path: Path) -> None:
        """SPEC §15.1 / §12.1：原始文件名只作元数据，服务器路径不得出现在任何产物里。

        报告是要发给客户和老板的，`C:\\Users\\...\\uploads\\` 会同时泄露部署结构
        与操作系统账号名。
        """
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        assert str(tmp_path) not in html
        assert tmp_path.name not in html
        assert not re.search(r"[A-Za-z]:[\\/]", html), "报告里出现了 Windows 盘符路径"
        assert "/home/" not in html and "/Users/" not in html

    def test_折叠使用details元素(self, tmp_path: Path) -> None:
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        assert "<details>" in html
        assert "<summary>" in html


# --------------------------------------------------------------------------------
# 内容
# --------------------------------------------------------------------------------


class TestContent:
    def test_免责声明逐字出现(self, tmp_path: Path) -> None:
        html = render_report(_two_docs(tmp_path), "测试项目", generated_at=GENERATED_AT)
        assert DISCLAIMER in html
        assert DISCLAIMER == "本工具只能辅助核对，不判断哪份文件正确，不构成贸易、法律或财务结论。"

    def test_顶部含项目名与生成时间(self, tmp_path: Path) -> None:
        html = render_report(_two_docs(tmp_path), "宁波日出 7 月订单", generated_at=GENERATED_AT)
        assert "宁波日出 7 月订单" in html
        assert GENERATED_AT in html

    def test_两份单据时显示覆盖横幅并列出缺席角色(self, tmp_path: Path) -> None:
        """SPEC §1.3：集合驱动比较引入的新失效模式，不得隐藏。"""
        html = render_report(_two_docs(tmp_path), "测试项目", generated_at=GENERATED_AT)
        assert COVERAGE_WARNING in html
        assert "缺席角色 = 未检查，不等于无差异" in html
        assert "报价单" in html
        assert "未上传" in html

    def test_三份齐全时不显示覆盖横幅(self, tmp_path: Path) -> None:
        result = _three_docs_with_planted_differences(tmp_path)
        assert len(result.snapshot.compared_roles) == 3
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert COVERAGE_WARNING not in html

    def test_概览含四个风险等级计数(self, tmp_path: Path) -> None:
        result = _three_docs_with_planted_differences(tmp_path)
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        for label in ("严重", "警告", "待复核", "提示"):
            assert label in html
        counts = result.comparison.counts_by_severity()
        assert counts[Severity.CRITICAL] > 0
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        assert [c.count for c in view.counts] == [
            counts[Severity.CRITICAL],
            counts[Severity.WARNING],
            counts[Severity.REVIEW],
            counts[Severity.INFO],
        ]
        assert view.total == len(result.comparison.differences)

    def test_严重度不只靠颜色区分(self, tmp_path: Path) -> None:
        """色觉障碍用户必须能读：每一级都有独立文字标签 + 形状记号。"""
        view = build_report_view(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        marks = {c.mark for c in view.counts}
        labels = {c.label for c in view.counts}
        assert len(marks) == 4
        assert len(labels) == 4

    def test_每条差异都渲染出证据(self, tmp_path: Path) -> None:
        """Gate-0 第 9 条在报告层的对应断言。"""
        result = _three_docs_with_planted_differences(tmp_path)
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)

        assert view.differences
        for diff in view.differences:
            assert diff.evidence, f"{diff.subject_label}/{diff.field_label} 没有渲染出证据"
            for ev in diff.evidence:
                assert ev.source_label != "证据记录缺失"
        # 每条差异各自一个 <details>
        assert html.count("<details>") == len(view.differences)

    def test_证据含文件名工作表单元格与原文(self, tmp_path: Path) -> None:
        result = _three_docs_with_planted_differences(tmp_path)
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)

        sample = next(ev for diff in view.differences for ev in diff.evidence)
        assert sample.filename.endswith(".xlsx")
        assert sample.filename in html
        assert sample.cell_reference in html
        assert sample.sheet_name in html

    def test_取值标出机器读取或人工修正(self, tmp_path: Path) -> None:
        """SPEC §9.6：报告要发给老板和客户，「机器读的还是人填的」必须精确到字段。"""
        result = _three_docs_with_planted_differences(tmp_path)
        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert "机器读取" in html
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        assert all(
            value.source_label in ("机器读取", "人工修正")
            for diff in view.differences
            for value in diff.values
        )

    def test_人工修正的取值标出来源并保留机器原读数(self, tmp_path: Path) -> None:
        """人工改过的数字不能和机器读数长得一样——报告是要发给客户的。"""
        result = _run(
            tmp_path,
            {
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {
                    "corrections": (
                        CorrectionInput(
                            scope="DOCUMENT",
                            line_key="",
                            field_name="grand_total",
                            user_value="9999.00",
                            reason="按客户盖章件更正",
                        ),
                    )
                },
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        corrected = [
            value
            for diff in view.differences
            if diff.field_label == "总金额"
            for value in diff.values
            if value.is_correction
        ]
        assert corrected, "人工修正没有出现在任何差异里"
        assert corrected[0].source_label == "人工修正"
        assert corrected[0].parser_value == BASE_GRAND_TOTAL
        assert corrected[0].correction_reason == "按客户盖章件更正"

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert "人工修正" in html
        assert f"机器原读数：{BASE_GRAND_TOTAL}" in html
        assert "修正理由：按客户盖章件更正" in html

    def test_单份单据时显式提示未执行比较(self, tmp_path: Path) -> None:
        """无法判断时显式失败：不能让「零差异」被读成「一致」。"""
        result = _run(tmp_path, {DocumentRole.PURCHASE_ORDER: {}})
        assert not result.snapshot.runnable
        html = render_report(result, "只上传了一份", generated_at=GENERATED_AT)
        assert "本次未执行任何比较" in html
        assert COVERAGE_WARNING in html
        assert "本次比较未发现差异" in html

    def test_报告为中文单语不出现英文枚举标识符(self, tmp_path: Path) -> None:
        """硬约束 #1：英文枚举只活在 API/DB/golden 里。"""
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        for token in ("PURCHASE_ORDER", "PROFORMA_INVOICE", "QUOTATION"):
            assert token not in html
        for token in ("VALUE_CONFLICT", "CALCULATION_ERROR", "CRITICAL", "WARNING"):
            assert token not in html

    def test_三份完全一致时严重计数为零(self, tmp_path: Path) -> None:
        """Gate-0 第 5 条在报告层的对应断言（SPEC §2.4 称它是最重要的一条）。

        期望值来自「三份文件内容相同」这个植入意图本身：内容一致就不该有致命差异。
        一份满屏红色的报告等于没有报告——用户会学会忽略它。
        """
        result = _run(tmp_path, {role: {} for role in DocumentRole})
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        criticals = [d for d in view.differences if d.severity_label == "严重"]
        assert not criticals, [(d.subject_label, d.field_label, d.explanation) for d in criticals]
        assert _severity_count(result, "严重") == 0

    def test_买方砍价在报告里不是严重(self, tmp_path: Path) -> None:
        """SPEC §9.4 有向表：quantity 在 Q→PO 是 INFO（买方下单行为，正常业务）。

        期望值抄自规格的严重度表，不是跑一遍实现倒推的。这条规则存在的理由是：
        一律判 CRITICAL 会把真正致命的 PO↔PI 错误淹没在正常谈判噪音里。
        """
        po_items = [
            ("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"),
            *BASE_ITEMS[1:],
        ]
        result = _run(
            tmp_path,
            {
                DocumentRole.QUOTATION: {},
                DocumentRole.PURCHASE_ORDER: {"items": po_items, "grand_total": "3320.00"},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        quantity = [d for d in view.differences if d.field_label == "数量"]
        assert quantity, "植入的数量变化没有被发现（召回必须是 100%）"
        assert quantity[0].severity_label == "提示"
        assert quantity[0].stage_label == "报价单 → 采购订单"
        assert _severity_count(result, "严重") == 0, "买方砍价不该产出任何致命差异"

    def test_卖方改价在报告里是严重(self, tmp_path: Path) -> None:
        """SPEC §9.4 有向表：unit_price 在 PO→PI 是 CRITICAL（卖方确认环节，错了直接损失）。"""
        pi_items = [
            BASE_ITEMS[0],
            ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1250.00"),
            BASE_ITEMS[2],
        ]
        result = _run(
            tmp_path,
            {
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {"items": pi_items, "grand_total": "3120.00"},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        price = [d for d in view.differences if d.field_label == "单价"]
        assert price, "植入的单价错误没有被发现（召回必须是 100%）"
        assert price[0].severity_label == "严重"
        assert price[0].stage_label == "采购订单 → 形式发票"

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        # 两边的原始数字都必须出现，用户要能自己核对而不是只看结论
        assert "2.40" in html
        assert "2.50" in html

    def test_无法比较与匹配歧义在报告里可见(self, tmp_path: Path) -> None:
        """SPEC §9.1：INCOMPARABLE 绝不能坍缩成「不一致」（假警报）或不产出（危险的沉默），
        **必须实际产出且 UI 可见**；§8.3：多成员组一律 AMBIGUOUS_MATCH 交人工。
        """
        pi_items = [
            BASE_ITEMS[0],
            # 单位从 PCS 变成 SETS —— 不做跨单位换算，只能判无法比较
            ("AB-200", "Ceramic Plate 8in", 500, "SETS", "2.40", "1200.00"),
            BASE_ITEMS[2],
            # 同一 SKU 在同一份单据里出现第二行 —— 不求和、不推断拆合
            ("AB-100", "Ceramic Mug 350ml", 10, "PCS", "1.25", "12.50"),
        ]
        result = _run(
            tmp_path,
            {
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {"items": pi_items, "grand_total": "3082.50"},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        types = {d.type_label for d in view.differences}
        assert "无法比较" in types, sorted(types)
        assert "匹配存在歧义" in types, sorted(types)

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert "无法比较" in html
        assert "匹配存在歧义" in html
        assert "不做求和" in html, "报告必须说清为什么不给结论，而不是只标一个类型"

    def test_derived证据显示算式依据(self, tmp_path: Path) -> None:
        """SPEC §10：CALCULATION_ERROR 的证据走 DERIVED，记录参与运算的格 + 算式。

        只显示「金额算错了」而不显示算式，用户无法判断是自己填错还是工具读错。
        """
        result = _three_docs_with_planted_differences(tmp_path)
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        derived = [
            ev for d in view.differences for ev in d.evidence if ev.source_label == "推导（算式）"
        ]
        assert derived, "算术校验差异没有产出 DERIVED 证据"
        assert any(ev.derived_from for ev in derived), "DERIVED 证据没有记录算式依据"

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert "推导（算式）" in html
        assert "依据：" in html

    def test_差异表含风险等级类型主体字段与说明(self, tmp_path: Path) -> None:
        html = render_report(
            _three_docs_with_planted_differences(tmp_path), "测试项目", generated_at=GENERATED_AT
        )
        for header in ("风险等级", "差异类型", "型号 / 主体", "字段", "各单据取值", "说明"):
            assert f"<th>{header}</th>" in html
        assert "取值冲突" in html
        assert "AB-100" in html


# --------------------------------------------------------------------------------
# 说明文案渲染（explanation_key -> 中文）
# --------------------------------------------------------------------------------


class TestExplanation:
    def test_已登记的key渲染成中文句子(self) -> None:
        text = render_explanation(
            "line_arithmetic_mismatch",
            {
                "role": "PROFORMA_INVOICE",
                "sku": "AB-200",
                "expected": "1250.00",
                "actual": "1200.00",
            },
        )
        assert "形式发票" in text
        assert "PROFORMA_INVOICE" not in text
        assert "1250.00" in text

    def test_未知key不抛异常且可见(self) -> None:
        """静默丢弃一条差异比显示一句难看的话危险得多。"""
        text = render_explanation("never_registered_key", {"a": "1"})
        assert "never_registered_key" in text
        assert "a=1" in text

    def test_缺参数不抛异常(self) -> None:
        text = render_explanation("value_conflict", {})
        assert "（未提供）" in text

    def test_每个引擎产出的key都有中文模板(self, tmp_path: Path) -> None:
        """漏一个模板就会在报告里露出一句英文 key。"""
        result = _three_docs_with_planted_differences(tmp_path)
        for diff in result.comparison.differences:
            assert diff.explanation_key in EXPLANATION_TEMPLATES, diff.explanation_key

    def test_参数里的角色标识符被翻译(self) -> None:
        text = render_explanation(
            "value_conflict",
            {"field": "单价", "buckets": "PURCHASE_ORDER、QUOTATION=1.25 | PROFORMA_INVOICE=2.50"},
        )
        assert "采购订单、报价单=1.25" in text
        assert "形式发票=2.50" in text

    def test_模板写坏时给出可见回退而不抛异常(self) -> None:
        """模板串里未闭合的花括号是源码级笔误。整份报告不能因此渲染失败，
        但也不能静默把这条差异吞掉——回退句子里必须带上原始参数。
        """
        good = _apply_template("{field}在各单据上不一致", {"field": "单价"})
        assert good == "单价在各单据上不一致"
        assert _apply_template("未闭合的 { 花括号", {}) is None

        text = render_explanation("never_registered_key", {"field": "单价", "buckets": "x"})
        assert "never_registered_key" in text
        assert "field=单价" in text

    def test_源码里每个explanation_key都有中文模板(self) -> None:
        """比「遍历一次真实结果」更强的闸门：没被那次场景走到的分支照样会露出英文 key。"""
        keys = _explanation_keys_in_source()
        assert len(keys) >= 12, f"扫描退化了，只在 app/comparison 里找到 {sorted(keys)}"
        missing = sorted(keys - set(EXPLANATION_TEMPLATES))
        assert not missing, f"这些 explanation_key 会在报告里露出英文原文：{missing}"


# --------------------------------------------------------------------------------
# 内部标识符 -> 中文（边界：单据原文一个字都不许动）
# --------------------------------------------------------------------------------


class TestIdentifierLocalization:
    """英文枚举标识符必须翻译（硬约束 #1），**单据原文必须原样保留**（SPEC §10）。

    这两条同时成立才是对的。对全部参数一律 `str.replace("QUOTATION", "报价单")`
    满足了前者、违反了后者：报告在自称「引用原文」的位置显示一个被改过的字符串，
    与 SPEC §15.2 第 1 条要防的是同一种失效，只是换了个载体。
    """

    def test_单据原文里的英文角色词不被翻译(self, tmp_path: Path) -> None:
        """外贸单据里 QUOTATION 是常见词：单号、备注「AS PER QUOTATION」都会出现它。"""
        result = _run(
            tmp_path,
            {
                DocumentRole.QUOTATION: {"doc_no": "QUOTATION-2026-001"},
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        doc_no = next(d for d in view.differences if d.field_label == "单据号")

        assert any(v.value == "QUOTATION-2026-001" for v in doc_no.values)
        # 同一行的「取值」列与「说明」列必须说同一件事
        assert "QUOTATION-2026-001" in doc_no.explanation
        assert "报价单-2026-001" not in doc_no.explanation

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert html.count("QUOTATION-2026-001") >= 2

    def test_buckets只翻译等号左边的角色名单(self) -> None:
        out = localize_buckets("PURCHASE_ORDER、QUOTATION=AS PER QUOTATION | PROFORMA_INVOICE=PO")
        assert out == "采购订单、报价单=AS PER QUOTATION | 形式发票=PO"

    def test_角色名单逐token精确匹配(self) -> None:
        """子串替换与逐 token 替换的分水岭：QUOTATION_SHEET 不是一个角色。"""
        assert localize_role_list("QUOTATION、PROFORMA_INVOICE") == "报价单、形式发票"
        assert localize_role_list("PURCHASE_ORDER") == "采购订单"
        assert localize_role_list("QUOTATION_SHEET") == "QUOTATION_SHEET"

    def test_引擎自拼的句子里角色标识符仍被翻译(self) -> None:
        """`detail` / `reason` 是引擎自己拼的中文散文，里面嵌了英文标识符，
        这类参数才允许子串替换。"""
        assert localize_prose("QUOTATION 的数量无法解析为数值") == "报价单 的数量无法解析为数值"

    def test_匹配组键翻成中文且不泄露内部键(self) -> None:
        assert localize_group_key("SKU:AB-100") == "AB-100"
        assert localize_group_key("NOSKU:PURCHASE_ORDER:16") == "采购订单第 16 行（未标型号）"

    def test_成员分布编码翻成中文(self) -> None:
        """`Q1:P2:I0` 是给调试与 DB 查询用的紧凑编码，业务员读不懂。"""
        assert localize_signature("Q1:P2:I0") == "报价单 1 行 / 采购订单 2 行 / 形式发票 0 行"
        # 形状不认识就原样输出——猜错比不翻译更糟
        assert localize_signature("未知形状") == "未知形状"

    def test_签名形状必须逐段对齐才翻译(self) -> None:
        """少一段 / 换了缩写 / 顺序不对，一律原样输出。

        `Q1:P2` 若被翻成「报价单 1 行 / 采购订单 2 行」，读者只会理解成
        形式发票不参与该组——而 AMBIGUOUS_MATCH 恰恰是最需要人工看准的场景。
        """
        assert localize_signature("Q1:P2") == "Q1:P2"  # 少一段
        assert localize_signature("Q1:P2:I0:X3") == "Q1:P2:I0:X3"  # 多一段
        assert localize_signature("P1:Q2:I0") == "P1:Q2:I0"  # 顺序不对
        assert localize_signature("Q1:Q2:Q0") == "Q1:Q2:Q0"  # 同一角色重复
        assert localize_signature("X1:P2:I0") == "X1:P2:I0"  # 未知缩写
        assert localize_signature("Qx:P2:I0") == "Qx:P2:I0"  # 计数不是数字
        assert localize_signature("Q:P2:I0") == "Q:P2:I0"  # 缺计数
        assert localize_signature("") == ""

    def test_签名的生产方与消费方读同一份定义(self) -> None:
        """生产方换了顺序而中文化没跟着换，是最容易漂移的一处。"""
        group = MatchGroupDraft(
            group_key="SKU:AB-100",
            match_method=MatchMethod.SKU_EXACT,
            match_reason="",
            multiplicity_state=MultiplicityState.UNIQUE_PER_ROLE,
            coverage_state=CoverageState.FULL,
            members=(),
        )
        # 生产方产出的签名必须能被消费方认出来（不是原样退回）
        assert localize_signature(group.role_signature) != group.role_signature
        assert localize_signature(group.role_signature) == (
            "报价单 0 行 / 采购订单 0 行 / 形式发票 0 行"
        )

    def test_匹配歧义说明里没有紧凑角色编码(self, tmp_path: Path) -> None:
        pi_items = [
            *BASE_ITEMS,
            ("AB-100", "Ceramic Mug 350ml", 10, "PCS", "1.25", "12.50"),
        ]
        result = _run(
            tmp_path,
            {
                DocumentRole.PURCHASE_ORDER: {},
                DocumentRole.PROFORMA_INVOICE: {"items": pi_items, "grand_total": "3082.50"},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        ambiguous = next(d for d in view.differences if d.type_label == "匹配存在歧义")
        assert "形式发票 2 行" in ambiguous.explanation
        assert not re.search(r"[QPI]\d:[QPI]\d", ambiguous.explanation)

    def test_未登记的参数原样输出(self) -> None:
        """默认策略必须是「一个字都不动」。

        露出一个英文标识符是**看得见**的 bug（下面那条全量断言会抓到）；
        悄悄改掉单据原文是**看不见**的，只有客户拿着原件对账时才会发现。
        """
        assert "field" not in PARAM_LOCALIZERS
        assert localize_params({"field": "QUOTATION"}) == {"field": "QUOTATION"}

    def test_无sku行的说明句不泄露内部身份串(self, tmp_path: Path) -> None:
        """没有型号的行（模具费 / 运费行）line_key 是 `pos:工作表!行号`，
        group_key 是 `NOSKU:PURCHASE_ORDER:16` —— 两个都是内部身份串，不该示人。
        """
        po_items = [*BASE_ITEMS, ("", "Mould Fee", 2, "SET", "300.00", "300.00")]
        result = _run(
            tmp_path,
            {
                DocumentRole.PURCHASE_ORDER: {"items": po_items, "grand_total": "3370.00"},
                DocumentRole.PROFORMA_INVOICE: {},
            },
        )
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        types = {d.type_label for d in view.differences}
        assert "行项目未对齐" in types, sorted(types)
        assert "金额计算不符" in types, sorted(types)

        html = render_report(result, "测试项目", generated_at=GENERATED_AT)
        for token in ("NOSKU:", "SKU:", "sku:", "cpn:", "pos:"):
            assert token not in html, f"内部身份串 {token} 泄露进报告"

    def test_任何说明句都不含英文角色标识符(self, tmp_path: Path) -> None:
        """「未登记参数原样输出」这个默认值的兜底闸门。

        将来引擎新增一个携带角色标识符的参数却忘了在 PARAM_LOCALIZERS 里登记策略，
        这条会红——这正是把默认值选成「不动」所需要付的代价，必须有人替它站岗。
        """
        result = _three_docs_with_planted_differences(tmp_path)
        view = build_report_view(result, "测试项目", generated_at=GENERATED_AT)
        assert len(view.differences) >= 5, "场景退化了，这条断言会在近乎空的集合上通过"
        leaks = [
            (diff.subject_label, diff.field_label, role.value)
            for diff in view.differences
            for role in DocumentRole
            if role.value in diff.explanation
        ]
        assert not leaks, leaks


# --------------------------------------------------------------------------------
# 确定性
# --------------------------------------------------------------------------------


class TestDeterminism:
    def test_同一输入渲染两次结果完全一致(self, tmp_path: Path) -> None:
        """模块内绝不调 datetime.now：否则快照测试与 Gate-0 第 15 条都无从谈起。"""
        result = _three_docs_with_planted_differences(tmp_path)
        first = render_report(result, "测试项目", generated_at=GENERATED_AT)
        second = render_report(result, "测试项目", generated_at=GENERATED_AT)
        assert first == second

    def test_相同输入不同运行结果一致(self, tmp_path: Path) -> None:
        first = render_report(
            _three_docs_with_planted_differences(tmp_path / "a"),
            "测试项目",
            generated_at=GENERATED_AT,
        )
        second = render_report(
            _three_docs_with_planted_differences(tmp_path / "b"),
            "测试项目",
            generated_at=GENERATED_AT,
        )
        assert first == second

    def test_模块不含时钟调用(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "app" / "exports" / "html.py").read_text(
            encoding="utf-8"
        )
        assert "datetime" not in source, "生成时间必须由调用方传入"
        assert "time.time" not in source
        assert "float(" not in source, "域内模块禁止出现 float("
