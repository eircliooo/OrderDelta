"""项目服务。SPEC §11.3、§12、§15。

**重跑语义**（本模块存在的主要理由）：

    重跑 = DELETE difference / match_group / match_member / evidence
         + 全量 insert
         + **一行 difference_review 都不碰**

读取时按 difference_key 合成：

    前提未变（values_digest == premise_digest）     -> 沿用原裁决
    前提已变                                        -> NEEDS_CONFIRMATION + 保留备注
                                                       + 展示「你上次是基于 X 判断的」
    弱身份且跨轮（run_fingerprint 不同）            -> **不继承**
    本次未产生该 key                                -> 裁决行保留为孤儿，可清理

默认实现（delete-all + insert，不区分 review）会让用户改一个单价后 20 条审核标记
静默归零，而「人工审核后导出报告」正是本产品的核心价值。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Difference,
    DifferenceReview,
    Document,
    Evidence,
    MatchGroup,
    MatchMember,
    Project,
    UserCorrection,
    utcnow,
)
from app.domain.enums import (
    DocumentRole,
    IdentityStrength,
    ParseStatus,
    ProjectStatus,
    ReviewStatus,
)
from app.domain.fields import rules_digest
from app.domain.models import DifferenceDraft, ValueCell
from app.extraction.snapshot import CorrectionInput
from app.parsers.base import DocumentInput
from app.pipeline import DocumentProcessing, ProjectResult, process_document, run_project


class ServiceError(Exception):
    """带错误码的业务异常。message **不得包含服务器绝对路径**。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _new_id() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ 项目 CRUD


def create_project(session: Session, name: str) -> Project:
    if not name.strip():
        raise ServiceError("INVALID_NAME", "项目名称不能为空")
    project = Project(id=_new_id(), name=name.strip(), status=ProjectStatus.DRAFT)
    session.add(project)
    session.flush()
    return project


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ServiceError("PROJECT_NOT_FOUND", "项目不存在", status_code=404)
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.created_at.desc())))


def delete_project(session: Session, project_id: str) -> None:
    """删除项目：清库 + 清盘。

    措辞注意：只能说「删除数据库记录与磁盘上的原始文件」，
    **不得宣称「安全擦除」「不可恢复」**——在 SQLite + 普通文件系统 + SSD 上做不到。
    """
    project = get_project(session, project_id)
    documents = list(session.scalars(select(Document).where(Document.project_id == project_id)))
    for document in documents:
        stored = settings.files_dir / document.stored_filename
        # stored_filename 是服务端生成的 UUID，这里再确认一次不越界
        if stored.parent.resolve() != settings.files_dir.resolve():
            continue
        stored.unlink(missing_ok=True)

    # difference_review 刻意无 FK，必须显式删
    session.execute(delete(DifferenceReview).where(DifferenceReview.project_id == project_id))
    session.delete(project)
    session.flush()


# -------------------------------------------------------------------- 文档上传


def _validate_upload(filename: str, mime_type: str, payload: bytes) -> str:
    """扩展名 + MIME + 魔数三重校验（SPEC §15.1）。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_suffixes:
        if suffix == ".pdf":
            raise ServiceError("UNSUPPORTED_EXT", "MVP-0 暂不支持 PDF，将在下一版本提供")
        raise ServiceError(
            "UNSUPPORTED_EXT",
            f"仅支持 {'/'.join(settings.allowed_suffixes)}，不支持 {suffix or '（无扩展名）'}",
        )
    if mime_type not in settings.allowed_mime_types:
        raise ServiceError("UNSUPPORTED_MIME", f"不支持的文件类型：{mime_type}")
    if len(payload) > settings.max_upload_bytes:
        raise ServiceError(
            "FILE_TOO_LARGE",
            f"文件超过 {settings.max_upload_bytes // (1024 * 1024)}MB 上限",
        )
    if payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # OLE2 复合文档：老式 .xls，或**加密过的** OOXML。给出具体原因而不是笼统的「损坏」。
        raise ServiceError("ENCRYPTED", "文件已加密或不是真正的 .xlsx，无法读取")
    if not payload.startswith(b"PK\x03\x04"):
        raise ServiceError("CORRUPT", "文件不是有效的 .xlsx（内容已损坏或扩展名与实际不符）")
    return suffix


def upload_document(
    session: Session,
    project_id: str,
    role: DocumentRole,
    filename: str,
    mime_type: str,
    payload: bytes,
) -> Document:
    """保存并解析一份文档。同一角色的旧文档会被标记为 superseded。"""
    get_project(session, project_id)  # 只为校验项目存在（不存在会抛 404）
    suffix = _validate_upload(filename, mime_type, payload)

    settings.ensure_dirs()
    #: **随机内部文件名**，原始文件名只作元数据 —— 杜绝路径穿越
    stored_filename = f"{uuid.uuid4().hex}{suffix}"
    target = settings.files_dir / stored_filename
    target.write_bytes(payload)

    previous = list(
        session.scalars(
            select(Document).where(
                Document.project_id == project_id,
                Document.role == role.value,
                Document.superseded_at.is_(None),
            )
        )
    )
    revision = 1
    for old in previous:
        old.superseded_at = utcnow()
        revision = max(revision, old.revision + 1)
        # 替换文件时旧的人工修正随 Document 级联消失（SPEC §11.1）
        (settings.files_dir / old.stored_filename).unlink(missing_ok=True)

    document = Document(
        id=_new_id(),
        project_id=project_id,
        role=role.value,
        revision=revision,
        original_filename=filename,
        stored_filename=stored_filename,
        mime_type=mime_type,
        file_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    session.add(document)
    session.flush()

    processing = _process(session, document)
    document.parser_name = processing.parsed.parser_name
    document.parser_version = processing.parsed.parser_version
    document.parse_status = processing.parsed.status.value
    document.parse_reason_code = (
        processing.parsed.reason_code.value if processing.parsed.reason_code else None
    )
    document.parse_detail = processing.parsed.detail
    document.parse_diagnostics = json.dumps(dict(processing.parsed.diagnostics), ensure_ascii=False)
    # 上传（含同角色替换）改变了比较输入，上一轮结果整体作废并重置状态。
    invalidate_comparison(session, project_id)
    session.flush()
    return document


def _usable_count(session: Session, project_id: str) -> int:
    documents = _active_documents(session, project_id)
    return sum(
        1
        for d in documents
        if d.parse_status in (ParseStatus.OK.value, ParseStatus.NEEDS_REVIEW.value)
    )


def _active_documents(session: Session, project_id: str) -> list[Document]:
    return list(
        session.scalars(
            select(Document)
            .where(Document.project_id == project_id, Document.superseded_at.is_(None))
            .order_by(Document.role)
        )
    )


def _drop_computed(session: Session, project_id: str) -> None:
    """删掉③计算产物（match_group / match_member / difference / evidence）。

    **④ 人工裁决一行都不碰**——`difference_review` 有独立生命周期，
    重跑时靠 `difference_key` 重新挂回来。
    """
    group_ids = list(
        session.scalars(select(MatchGroup.id).where(MatchGroup.project_id == project_id))
    )
    if group_ids:
        session.execute(delete(MatchMember).where(MatchMember.match_group_id.in_(group_ids)))
    session.execute(delete(MatchGroup).where(MatchGroup.project_id == project_id))
    session.execute(delete(Difference).where(Difference.project_id == project_id))
    session.execute(delete(Evidence).where(Evidence.project_id == project_id))
    session.flush()


def invalidate_comparison(session: Session, project_id: str) -> None:
    """文档集合一变，上一轮比较的输入就不成立了：③ 计算产物整体作废。

    **必须挂在每一条改动文档集合的路径上**（上传 / 替换 / 删除），不能只挂删除：
    界面上根本没有「删除单份文档」这个操作（`frontend/src/api/client.ts` 里只有
    `deleteProject`），用户换文件的唯一方式是**用同角色重新上传**走 supersede 分支——
    那条路会把旧文件从磁盘 unlink 掉，却留下整套按旧文件算出来的差异与证据。

    不作废的后果是两个结论互相打架：`GET /differences` 读库里的旧结果（旧数值、
    旧文件名，而那个文件已经不在磁盘上了），`report.html` 走 `build_result()` 现场
    重解析（新数值、新文件名）。同一批数据，屏幕和导出的报告说两套话，
    而屏幕那套还带着看起来有效的人工裁决。

    ④ 人工裁决（`difference_review`）一行都不碰——用户可能只是传错了一份文件。
    """
    _drop_computed(session, project_id)
    project = session.get(Project, project_id)
    if project is None:
        return
    project.compared_at = None
    project.comparison_input_fingerprint = None
    # 与 upload_document 同一套口径：能不能跑取决于可用文档数，不是「刚删过东西」。
    project.status = (
        ProjectStatus.READY if _usable_count(session, project_id) >= 2 else ProjectStatus.DRAFT
    )


def delete_document(session: Session, project_id: str, document_id: str) -> None:
    document = session.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise ServiceError("DOCUMENT_NOT_FOUND", "文档不存在", status_code=404)
    (settings.files_dir / document.stored_filename).unlink(missing_ok=True)
    session.delete(document)
    session.flush()
    invalidate_comparison(session, project_id)
    session.flush()


# ---------------------------------------------------------------------- 处理


def _corrections_for(session: Session, document_id: str) -> tuple[CorrectionInput, ...]:
    rows = session.scalars(select(UserCorrection).where(UserCorrection.document_id == document_id))
    return tuple(
        CorrectionInput(
            scope=row.scope,
            line_key=row.line_key,
            field_name=row.field_name,
            user_value=row.user_value,
            reason=row.reason,
        )
        for row in sorted(rows, key=lambda r: (r.scope, r.line_key, r.field_name))
    )


def _process(session: Session, document: Document) -> DocumentProcessing:
    """从磁盘上的原始文件重新解析。

    MVP-0 不落库 extracted_field / line_item（属 MVP-1）：解析是确定性的，
    原始文件始终在，重解析比维护两份真相更不容易出错。见 docs/limitations.md。
    """
    path = settings.files_dir / document.stored_filename
    src = DocumentInput(
        path=path,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        file_size=document.file_size,
        sha256=document.sha256,
    )
    return process_document(
        document_id=document.id,
        role=DocumentRole(document.role),
        src=src,
        corrections=_corrections_for(session, document.id),
    )


def input_fingerprint(session: Session, project_id: str) -> str:
    """项目输入指纹：文档内容 + 人工修正 + **比较规则版本**。

    SPEC §3.2 把 `comparison_input_fingerprint` 定义为三段子哈希
    `docs:…|corrections:…|rules:…`。`rules:` 段不可省：它决定「弱身份差异跨轮不继承」
    里的「跨轮」怎么算。少了它，改完比较规则重跑会被判成同一轮，
    弱身份差异（多成员组之类）的旧裁决会原地留着——而规则刚变，那正是最该重看的时候。

    强身份差异的继承走的是另一条路（前提摘要，见 `domain/identity.py::values_digest`），
    规则版本在那边通过严重度签名参与。两条路都要堵，只堵一条等于没堵。
    """
    docs: list[str] = []
    corrections: list[str] = []
    for document in _active_documents(session, project_id):
        docs.append(f"{document.role}:{document.sha256}")
        for correction in _corrections_for(session, document.id):
            corrections.append(
                f"{document.role}:{correction.scope}:{correction.line_key}:"
                f"{correction.field_name}={correction.user_value}"
            )
    payload = (
        f"docs:{'|'.join(sorted(docs))}"
        f"|corrections:{'|'.join(sorted(corrections))}"
        f"|rules:{rules_digest()}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_result(session: Session, project_id: str) -> ProjectResult:
    documents = _active_documents(session, project_id)
    processed = {
        DocumentRole(d.role): _process(session, d) for d in sorted(documents, key=lambda d: d.role)
    }
    return run_project(project_id, processed)


# ------------------------------------------------------------------ 比较与落库


def run_comparison(session: Session, project_id: str) -> ProjectResult:
    project = get_project(session, project_id)
    result = build_result(session, project_id)

    if not result.snapshot.runnable:
        raise ServiceError(
            "NOT_ENOUGH_DOCUMENTS",
            "至少需要两份可解析的文件才能运行检查（未上传或解析失败的角色不参与比较）",
        )

    _drop_computed(session, project_id)

    for evidence in result.evidence.values():
        session.add(
            Evidence(
                id=evidence.evidence_id,
                project_id=project_id,
                document_id=evidence.document_id,
                role=evidence.role.value,
                source_type=evidence.source_type.value,
                sheet_name=evidence.sheet_name,
                cell_reference=evidence.cell_reference,
                row_index=evidence.row_index,
                col_index=evidence.col_index,
                raw_text=evidence.raw_text,
                derived_from=json.dumps(list(evidence.derived_from), ensure_ascii=False),
                parser_metadata=json.dumps(dict(evidence.parser_metadata), ensure_ascii=False),
            )
        )

    for group in result.groups:
        group_row = MatchGroup(
            id=_new_id(),
            project_id=project_id,
            group_key=group.group_key,
            match_method=group.match_method.value,
            match_confidence=group.match_confidence,
            match_reason=group.match_reason,
            role_signature=group.role_signature,
            multiplicity_state=group.multiplicity_state.value,
            coverage_state=group.coverage_state.value,
        )
        session.add(group_row)
        session.flush()
        for member in group.members:
            session.add(
                MatchMember(
                    id=_new_id(),
                    match_group_id=group_row.id,
                    line_item_ref=member.line_item_id,
                    line_key=member.line_key,
                    document_role=member.role.value,
                    role_ordinal=member.role_ordinal,
                )
            )

    for diff in result.comparison.differences:
        session.add(
            Difference(
                id=_new_id(),
                project_id=project_id,
                difference_key=diff.difference_key,
                identity_strength=diff.identity_strength.value,
                scope=diff.scope.value,
                subject_kind=diff.subject_kind.value,
                subject_key=diff.subject_key,
                field_name=diff.field_name,
                difference_type=diff.difference_type.value,
                severity=diff.severity.value,
                severity_rule_id=diff.severity_rule_id,
                chain_stage=diff.chain_stage.value,
                baseline_role=diff.baseline_role.value if diff.baseline_role else None,
                target_role=diff.target_role.value if diff.target_role else None,
                values_by_document=json.dumps(
                    {role: _cell_json(cell) for role, cell in diff.values_by_document.items()},
                    ensure_ascii=False,
                ),
                values_digest=diff.values_digest,
                has_user_input=int(diff.has_user_input),
                explanation_key=diff.explanation_key,
                explanation_params=json.dumps(dict(diff.explanation_params), ensure_ascii=False),
                evidence_ids=json.dumps(list(diff.evidence_ids), ensure_ascii=False),
                confidence=diff.confidence,
            )
        )

    project.status = ProjectStatus.COMPARED
    project.compared_at = utcnow()
    project.comparison_input_fingerprint = input_fingerprint(session, project_id)
    session.flush()
    return result


def _cell_json(cell: ValueCell) -> dict[str, str | None]:
    return {
        "value": cell.value,
        "source": cell.source.value,
        "parser_value": cell.parser_value,
        "correction_reason": cell.correction_reason,
        "confidence": cell.confidence,
        "evidence_id": cell.evidence_id,
        "warning": cell.warning,
    }


# ------------------------------------------------------------------ 审核合成


@dataclass(frozen=True)
class ResolvedReview:
    review_status: ReviewStatus
    review_note: str | None
    #: 前提已变时展示「你上次是基于 X 判断的」。结构与 values_by_document 相同：
    #: {角色: {value, source, parser_value, ...}}
    stale_premise: dict[str, dict[str, str | None]] | None
    inherited: bool


def resolve_review(
    diff_key: str,
    identity_strength: str,
    values_digest: str,
    review: DifferenceReview | None,
    current_fingerprint: str,
) -> ResolvedReview:
    """把持久化的裁决合成到本轮差异上。SPEC §11.3。"""
    if review is None:
        return ResolvedReview(ReviewStatus.OPEN, None, None, inherited=False)

    same_run = review.run_fingerprint == current_fingerprint
    if identity_strength == IdentityStrength.WEAK.value and not same_run:
        # 弱身份跨轮不继承 —— 宁可可见地丢，不可错挂到另一行上
        return ResolvedReview(ReviewStatus.OPEN, None, None, inherited=False)

    if review.premise_digest == values_digest:
        return ResolvedReview(
            ReviewStatus(review.review_status), review.review_note, None, inherited=True
        )

    return ResolvedReview(
        ReviewStatus.NEEDS_CONFIRMATION,
        review.review_note,
        json.loads(review.premise_snapshot) if review.premise_snapshot else None,
        inherited=True,
    )


def reviews_by_key(session: Session, project_id: str) -> dict[str, DifferenceReview]:
    rows = session.scalars(
        select(DifferenceReview).where(DifferenceReview.project_id == project_id)
    )
    return {row.difference_key: row for row in rows}


def set_review(
    session: Session,
    project_id: str,
    difference_key: str,
    status: ReviewStatus,
    note: str | None,
) -> DifferenceReview:
    get_project(session, project_id)
    diff = session.scalar(
        select(Difference).where(
            Difference.project_id == project_id,
            Difference.difference_key == difference_key,
        )
    )
    if diff is None:
        raise ServiceError("DIFFERENCE_NOT_FOUND", "差异不存在", status_code=404)

    existing = session.scalar(
        select(DifferenceReview).where(
            DifferenceReview.project_id == project_id,
            DifferenceReview.difference_key == difference_key,
        )
    )
    fingerprint = input_fingerprint(session, project_id)
    if existing is None:
        existing = DifferenceReview(
            id=_new_id(),
            project_id=project_id,
            difference_key=difference_key,
            identity_strength=diff.identity_strength,
            review_status=status.value,
            review_note=note,
            premise_digest=diff.values_digest,
            premise_snapshot=diff.values_by_document,
            run_fingerprint=fingerprint,
        )
        session.add(existing)
    else:
        existing.review_status = status.value
        existing.review_note = note
        existing.identity_strength = diff.identity_strength
        existing.premise_digest = diff.values_digest
        existing.premise_snapshot = diff.values_by_document
        existing.run_fingerprint = fingerprint
        existing.updated_at = utcnow()
    session.flush()
    return existing


def add_correction(
    session: Session,
    project_id: str,
    role: DocumentRole,
    scope: str,
    line_key: str,
    field_name: str,
    user_value: str | None,
    reason: str | None,
) -> UserCorrection:
    """② 人工断言。锚领域坐标，绝不覆盖解析器读数。"""
    get_project(session, project_id)
    document = session.scalar(
        select(Document).where(
            Document.project_id == project_id,
            Document.role == role.value,
            Document.superseded_at.is_(None),
        )
    )
    if document is None:
        raise ServiceError("DOCUMENT_NOT_FOUND", "该角色尚未上传文档", status_code=404)

    existing = session.scalar(
        select(UserCorrection).where(
            UserCorrection.document_id == document.id,
            UserCorrection.scope == scope,
            UserCorrection.line_key == line_key,
            UserCorrection.field_name == field_name,
        )
    )
    if existing is not None:
        existing.user_value = user_value
        existing.reason = reason
        session.flush()
        return existing

    correction = UserCorrection(
        id=_new_id(),
        document_id=document.id,
        kind="OVERRIDE",
        scope=scope,
        line_key=line_key,
        field_name=field_name,
        user_value=user_value,
        reason=reason,
    )
    session.add(correction)
    session.flush()
    return correction


def stored_differences(session: Session, project_id: str) -> list[Difference]:
    """按稳定顺序读出差异。硬约束 #5：对外输出必须稳定排序。"""
    rows = list(session.scalars(select(Difference).where(Difference.project_id == project_id)))
    return sorted(
        rows,
        key=lambda d: (d.scope, d.subject_key, d.field_name or "", d.difference_type),
    )


def difference_drafts_from_result(result: ProjectResult) -> tuple[DifferenceDraft, ...]:
    return result.comparison.differences
