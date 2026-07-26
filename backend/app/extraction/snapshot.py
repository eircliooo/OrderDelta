"""把解析+提取结果装配成 `ProjectSnapshot`。SPEC §11.2。

`ProjectSnapshot` 是比较引擎的唯一输入，**不含任何 ORM 对象**。DB 版的
ValueResolver 复用本模块，只是把 ExtractedLineItem 的来源换成数据库读出的行。

人工修正在这里合流（SPEC §11.1）：`user_correction` 锚在领域坐标
`(document_id, scope, line_key, field_name)` 上，覆盖解析器读数时保留
`parser_value` 与 `correction_reason`——报告要能说清「这个数是机器读的还是人填的」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import (
    DocumentRole,
    EvidenceSourceType,
    ParseStatus,
    ValueSource,
)
from app.domain.models import (
    EvidenceDraft,
    ProjectSnapshot,
    SnapshotDocument,
    SnapshotLineItem,
    ValueCell,
)
from app.extraction.doc_fields import DocFieldExtraction, ExtractedDocField
from app.extraction.line_items import ExtractedCell, LineItemExtraction


@dataclass(frozen=True)
class CorrectionInput:
    """一条人工修正。scope=DOCUMENT 时 line_key 为空串。"""

    scope: str
    line_key: str
    field_name: str
    user_value: str | None
    reason: str | None = None


def evidence_id_for(document_id: str, sheet_name: str, address: str) -> str:
    """确定性 Evidence id —— 重跑时必须稳定，绝不用自增主键或 uuid。"""
    return f"{document_id}:{sheet_name}!{address}"


def _corrections_index(
    corrections: tuple[CorrectionInput, ...],
) -> dict[tuple[str, str, str], CorrectionInput]:
    return {(c.scope, c.line_key, c.field_name): c for c in corrections}


def _cell_from_doc_field(
    document_id: str,
    field: ExtractedDocField,
    evidence: dict[str, EvidenceDraft],
    role: DocumentRole,
) -> ValueCell:
    eid = evidence_id_for(document_id, field.sheet_name, field.address)
    evidence.setdefault(
        eid,
        EvidenceDraft(
            evidence_id=eid,
            document_id=document_id,
            role=role,
            source_type=EvidenceSourceType.XLSX_CELL,
            sheet_name=field.sheet_name,
            cell_reference=field.address,
            raw_text=field.raw_text,
            parser_metadata={"label": field.label_text, "label_cell": field.label_address},
        ),
    )
    return ValueCell(
        value=field.normalized,
        source=ValueSource.PARSER,
        parser_value=field.normalized,
        evidence_id=eid,
        warning=field.warning,
    )


def _cell_from_line_cell(
    document_id: str,
    cell: ExtractedCell,
    evidence: dict[str, EvidenceDraft],
    role: DocumentRole,
) -> ValueCell:
    eid = evidence_id_for(document_id, cell.sheet_name, cell.address)
    evidence.setdefault(
        eid,
        EvidenceDraft(
            evidence_id=eid,
            document_id=document_id,
            role=role,
            source_type=EvidenceSourceType.XLSX_CELL,
            sheet_name=cell.sheet_name,
            cell_reference=cell.address,
            row_index=cell.row_index,
            col_index=cell.col_index,
            raw_text=cell.raw_text,
        ),
    )
    return ValueCell(
        value=cell.normalized,
        source=ValueSource.PARSER,
        parser_value=cell.normalized,
        evidence_id=eid,
        warning=cell.warning,
    )


def _apply_correction(base: ValueCell, correction: CorrectionInput | None) -> ValueCell:
    if correction is None:
        return base
    return ValueCell(
        value=correction.user_value,
        source=ValueSource.USER_CORRECTION,
        parser_value=base.parser_value,
        correction_reason=correction.reason,
        confidence=base.confidence,
        evidence_id=base.evidence_id,
        warning=base.warning,
    )


def build_document_snapshot(
    *,
    document_id: str,
    role: DocumentRole,
    original_filename: str,
    parse_status: ParseStatus,
    doc_fields: DocFieldExtraction,
    line_items: LineItemExtraction,
    corrections: tuple[CorrectionInput, ...] = (),
) -> SnapshotDocument:
    evidence: dict[str, EvidenceDraft] = {}
    index = _corrections_index(corrections)

    fields: dict[str, ValueCell] = {}
    for key, extracted in doc_fields.fields.items():
        doc_cell = _cell_from_doc_field(document_id, extracted, evidence, role)
        fields[key] = _apply_correction(doc_cell, index.get(("DOCUMENT", "", key)))

    # 只有人工修正、解析器没提到的文档级字段也要能进快照
    for (scope, line_key, field_name), correction in index.items():
        if scope == "DOCUMENT" and line_key == "" and field_name not in fields:
            fields[field_name] = ValueCell(
                value=correction.user_value,
                source=ValueSource.USER_CORRECTION,
                parser_value=None,
                correction_reason=correction.reason,
            )

    snapshot_items: list[SnapshotLineItem] = []
    for item in line_items.items:
        item_fields: dict[str, ValueCell] = {}
        for key, cell in item.cells.items():
            value_cell = _cell_from_line_cell(document_id, cell, evidence, role)
            item_fields[key] = _apply_correction(
                value_cell, index.get(("LINE_ITEM", item.line_key, key))
            )
        snapshot_items.append(
            SnapshotLineItem(
                line_key=item.line_key,
                document_id=document_id,
                role=role,
                row_index=item.row_index,
                sheet_name=item.sheet_name,
                sku_norm=item.sku_norm,
                customer_part_norm=item.customer_part_norm,
                unit_norm=item.unit_norm,
                currency=item.currency,
                fields=item_fields,
            )
        )

    return SnapshotDocument(
        document_id=document_id,
        role=role,
        original_filename=original_filename,
        parse_status=parse_status,
        currency=_document_currency(fields, snapshot_items),
        fields=fields,
        line_items=tuple(snapshot_items),
        evidence=evidence,
    )


def _document_currency(
    fields: Mapping[str, ValueCell], items: list[SnapshotLineItem]
) -> str | None:
    """文档币种：优先文档级字段，其次行级**一致**的币种。

    行级币种不一致时返回 None —— 混合币种必须交给比较引擎标 INCOMPARABLE，
    不能在这里挑一个当代表。
    """
    doc_currency = fields.get("currency")
    if doc_currency is not None and doc_currency.present:
        return doc_currency.value
    line_currencies = {item.currency for item in items if item.currency}
    if len(line_currencies) == 1:
        return next(iter(line_currencies))
    return None


def build_project_snapshot(
    project_id: str,
    documents: Mapping[DocumentRole, SnapshotDocument],
    skipped_roles: tuple[DocumentRole, ...] = (),
) -> ProjectSnapshot:
    return ProjectSnapshot(
        project_id=project_id,
        documents=dict(documents),
        skipped_roles=skipped_roles,
    )
