"""纵向闭环：文件 -> 解析 -> 提取 -> 快照 -> 匹配 -> 比较 -> 差异 + 证据。

SPEC §19 阶段 2。这是全系统唯一的编排入口，API 层和 golden 测试都调它，
保证「网页上跑的」和「测试里跑的」是同一条路径。

纯函数：同一输入必得同一输出（Gate-0 第 15 条）。不碰数据库、不碰文件系统
（除了读取用户已上传的文件）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.comparison.engine import ComparisonResult, compare
from app.domain.enums import DocumentRole, ParseReasonCode, ParseStatus
from app.domain.models import EvidenceDraft, MatchGroupDraft, ProjectSnapshot, SnapshotDocument
from app.extraction.doc_fields import DocFieldExtraction, extract_document_fields
from app.extraction.header import best_header
from app.extraction.line_items import LineItemExtraction, extract_line_items
from app.extraction.snapshot import CorrectionInput, build_document_snapshot, build_project_snapshot
from app.matching.engine import match_line_items
from app.parsers.base import (
    DEFAULT_LIMITS,
    DocumentInput,
    ParsedDocument,
    ParseLimits,
    select_parser,
)
from app.parsers.xlsx import XlsxParser

#: MVP-0 只注册 XLSX 解析器。MVP-1 在这里追加 PdfParser，其余各层一行不改。
PARSERS = (XlsxParser(),)


@dataclass(frozen=True)
class DocumentProcessing:
    """单份文档从文件到快照的全过程结果。"""

    role: DocumentRole
    parsed: ParsedDocument
    doc_fields: DocFieldExtraction
    line_items: LineItemExtraction
    snapshot: SnapshotDocument | None
    #: 表头没定位到时为 True —— 解析成功但拿不到行项目，必须显式告诉用户
    header_missing: bool = False

    @property
    def usable(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True)
class ProjectResult:
    snapshot: ProjectSnapshot
    groups: tuple[MatchGroupDraft, ...]
    comparison: ComparisonResult
    processing: dict[DocumentRole, DocumentProcessing]

    @property
    def evidence(self) -> dict[str, EvidenceDraft]:
        merged: dict[str, EvidenceDraft] = {}
        for doc in self.snapshot.documents.values():
            merged.update(doc.evidence)
        for extra in self.comparison.extra_evidence:
            merged[extra.evidence_id] = extra
        return merged


def process_document(
    *,
    document_id: str,
    role: DocumentRole,
    src: DocumentInput,
    corrections: tuple[CorrectionInput, ...] = (),
    limits: ParseLimits = DEFAULT_LIMITS,
) -> DocumentProcessing:
    """解析并提取一份文档。失败时 snapshot 为 None，原因在 parsed.reason_code。"""
    parser, capability = select_parser(src, PARSERS)
    if parser is None:
        rejected = ParsedDocument(
            parser_name="none",
            parser_version="0",
            status=ParseStatus.REJECTED,
            reason_code=capability.reason_code or ParseReasonCode.UNSUPPORTED_EXT,
            detail=capability.detail,
        )
        return DocumentProcessing(
            role=role,
            parsed=rejected,
            doc_fields=DocFieldExtraction(fields={}, warnings=()),
            line_items=LineItemExtraction((), (), None, ()),
            snapshot=None,
        )

    parsed = parser.parse(src, limits)
    empty_fields = DocFieldExtraction(fields={}, warnings=())
    empty_items = LineItemExtraction((), (), None, ())

    if not parsed.usable:
        return DocumentProcessing(
            role=role,
            parsed=parsed,
            doc_fields=empty_fields,
            line_items=empty_items,
            snapshot=None,
        )

    doc_fields = extract_document_fields(parsed.tables)
    table, detection = best_header(parsed.tables, limits)

    if table is None or detection is None:
        # 解析成功但没找到订单表格 —— 显式失败，不假装成功
        degraded = ParsedDocument(
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            status=ParseStatus.NEEDS_REVIEW,
            reason_code=ParseReasonCode.NO_TABLE_FOUND,
            detail="未能在工作簿中定位到行项目表（需同时出现数量列与单价/金额列）",
            diagnostics=parsed.diagnostics,
            tables=parsed.tables,
            blocks=parsed.blocks,
            warnings=parsed.warnings,
        )
        return DocumentProcessing(
            role=role,
            parsed=degraded,
            doc_fields=doc_fields,
            line_items=empty_items,
            snapshot=None,
            header_missing=True,
        )

    line_items = extract_line_items(table, detection)
    snapshot = build_document_snapshot(
        document_id=document_id,
        role=role,
        original_filename=src.original_filename,
        parse_status=parsed.status,
        doc_fields=doc_fields,
        line_items=line_items,
        corrections=corrections,
    )
    return DocumentProcessing(
        role=role,
        parsed=parsed,
        doc_fields=doc_fields,
        line_items=line_items,
        snapshot=snapshot,
    )


def run_project(
    project_id: str,
    processed: dict[DocumentRole, DocumentProcessing],
) -> ProjectResult:
    """把已处理的文档跑完匹配与比较。

    **只有 snapshot 可用的角色进入比较集合**（SPEC §9.8）——未上传或解析失败的角色
    不产生任何 Difference，否则两文件场景会被几十条假缺失淹没。
    """
    documents = {
        role: item.snapshot for role, item in processed.items() if item.snapshot is not None
    }
    skipped = tuple(
        sorted(
            (role for role, item in processed.items() if item.snapshot is None),
            key=lambda r: r.value,
        )
    )
    snapshot = build_project_snapshot(project_id, documents, skipped)

    if not snapshot.runnable:
        return ProjectResult(
            snapshot=snapshot,
            groups=(),
            comparison=ComparisonResult(differences=(), extra_evidence=()),
            processing=processed,
        )

    groups = match_line_items(snapshot)
    comparison = compare(snapshot, groups)
    return ProjectResult(
        snapshot=snapshot, groups=groups, comparison=comparison, processing=processed
    )
