"""HTTP 路由。SPEC §12.2。

前端用相对路径 `/api` + Vite proxy，因此**不需要 CORS 中间件**——
这是唯一有返工代价的一条：硬编码 http://localhost:8000 后改同源代理要动每个调用点。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CorrectionIn,
    DifferenceOut,
    DocumentOut,
    Envelope,
    EvidenceOut,
    HealthOut,
    ProjectCreate,
    ProjectOut,
    ReviewIn,
    ValueOut,
)
from app.db.models import Difference, Document, Evidence, Project
from app.db.session import get_session_factory
from app.domain.enums import DocumentRole, ReviewStatus, Severity
from app.domain.models import ReviewState
from app.exports.html import render_report
from app.services import projects as svc

router = APIRouter(prefix="/api/v1")

VERSION = "0.1.0"


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


# ------------------------------------------------------------------------ 健康


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version=VERSION, mvp="MVP-0 (XLSX only)", llm_enabled=False)


# ------------------------------------------------------------------------ 项目


def _documents_out(session: Session, project_id: str) -> list[DocumentOut]:
    rows = session.scalars(
        select(Document)
        .where(Document.project_id == project_id, Document.superseded_at.is_(None))
        .order_by(Document.role)
    )
    return [
        DocumentOut(
            id=d.id,
            role=d.role,
            original_filename=d.original_filename,
            file_size=d.file_size,
            sha256=d.sha256,
            parse_status=d.parse_status,
            parse_reason_code=d.parse_reason_code,
            parse_detail=d.parse_detail,
            revision=d.revision,
        )
        for d in rows
    ]


def _project_out(session: Session, project: Project) -> ProjectOut:
    documents = _documents_out(session, project.id)
    usable = {d.role for d in documents if d.parse_status in ("OK", "NEEDS_REVIEW")}
    skipped = sorted({r.value for r in DocumentRole} - usable)

    differences = svc.stored_differences(session, project.id)
    reviews = svc.reviews_by_key(session, project.id)
    fingerprint = svc.input_fingerprint(session, project.id)

    counts = dict.fromkeys((s.value for s in Severity), 0)
    open_count = 0
    for diff in differences:
        counts[diff.severity] = counts.get(diff.severity, 0) + 1
        resolved = svc.resolve_review(
            diff.difference_key,
            diff.identity_strength,
            diff.values_digest,
            reviews.get(diff.difference_key),
            fingerprint,
        )
        if resolved.review_status in (ReviewStatus.OPEN, ReviewStatus.NEEDS_CONFIRMATION):
            open_count += 1

    return ProjectOut(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        compared_at=project.compared_at,
        documents=documents,
        compared_roles=sorted(usable),
        skipped_roles=skipped,
        severity_counts=counts,
        open_count=open_count,
    )


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, session: DbSession) -> ProjectOut:
    project = svc.create_project(session, body.name)
    return _project_out(session, project)


@router.get("/projects", response_model=Envelope[ProjectOut])
def list_projects(session: DbSession) -> Envelope[ProjectOut]:
    items = [_project_out(session, p) for p in svc.list_projects(session)]
    return Envelope(items=items, total=len(items))


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, session: DbSession) -> ProjectOut:
    return _project_out(session, svc.get_project(session, project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: DbSession) -> Response:
    svc.delete_project(session, project_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------ 文档


@router.post("/projects/{project_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    project_id: str,
    session: DbSession,
    role: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    try:
        parsed_role = DocumentRole(role)
    except ValueError as exc:
        raise svc.ServiceError("INVALID_ROLE", f"未知的文档角色：{role}") from exc

    payload = await file.read()
    document = svc.upload_document(
        session,
        project_id,
        parsed_role,
        file.filename or "unnamed.xlsx",
        file.content_type or "application/octet-stream",
        payload,
    )
    return DocumentOut(
        id=document.id,
        role=document.role,
        original_filename=document.original_filename,
        file_size=document.file_size,
        sha256=document.sha256,
        parse_status=document.parse_status,
        parse_reason_code=document.parse_reason_code,
        parse_detail=document.parse_detail,
        revision=document.revision,
    )


@router.delete("/projects/{project_id}/documents/{document_id}", status_code=204)
def delete_document(project_id: str, document_id: str, session: DbSession) -> Response:
    svc.delete_document(session, project_id, document_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------ 比较


@router.post("/projects/{project_id}/compare", response_model=ProjectOut)
def run_compare(project_id: str, session: DbSession) -> ProjectOut:
    svc.run_comparison(session, project_id)
    return _project_out(session, svc.get_project(session, project_id))


def _evidence_map(session: Session, project_id: str) -> dict[str, Evidence]:
    rows = session.scalars(select(Evidence).where(Evidence.project_id == project_id))
    return {row.id: row for row in rows}


def _filenames(session: Session, project_id: str) -> dict[str, str]:
    rows = session.scalars(select(Document).where(Document.project_id == project_id))
    return {row.id: row.original_filename for row in rows}


def _difference_out(
    diff: Difference,
    evidence_map: dict[str, Evidence],
    filenames: dict[str, str],
    resolved: svc.ResolvedReview,
) -> DifferenceOut:
    values = {
        role: ValueOut(**payload) for role, payload in json.loads(diff.values_by_document).items()
    }
    evidence: list[EvidenceOut] = []
    for eid in json.loads(diff.evidence_ids):
        row = evidence_map.get(eid)
        if row is None:
            continue
        evidence.append(
            EvidenceOut(
                evidence_id=row.id,
                role=row.role,
                original_filename=filenames.get(row.document_id, ""),
                source_type=row.source_type,
                sheet_name=row.sheet_name,
                cell_reference=row.cell_reference,
                raw_text=row.raw_text,
                derived_from=json.loads(row.derived_from or "[]"),
            )
        )
    return DifferenceOut(
        difference_key=diff.difference_key,
        scope=diff.scope,
        subject_kind=diff.subject_kind,
        subject_key=diff.subject_key,
        field_name=diff.field_name,
        difference_type=diff.difference_type,
        severity=diff.severity,
        severity_rule_id=diff.severity_rule_id,
        chain_stage=diff.chain_stage,
        baseline_role=diff.baseline_role,
        target_role=diff.target_role,
        identity_strength=diff.identity_strength,
        values_by_document=values,
        explanation_key=diff.explanation_key,
        explanation_params=json.loads(diff.explanation_params),
        evidence=evidence,
        has_user_input=bool(diff.has_user_input),
        review_status=resolved.review_status.value,
        review_note=resolved.review_note,
        stale_premise=(
            {k: str(v.get("value")) for k, v in resolved.stale_premise.items()}
            if resolved.stale_premise
            else None
        ),
    )


@router.get("/projects/{project_id}/differences", response_model=Envelope[DifferenceOut])
def list_differences(
    project_id: str,
    session: DbSession,
    severity: str | None = None,
    difference_type: str | None = None,
    sku: str | None = None,
    review_status: str | None = None,
    role: str | None = None,
) -> Envelope[DifferenceOut]:
    svc.get_project(session, project_id)
    rows = svc.stored_differences(session, project_id)
    reviews = svc.reviews_by_key(session, project_id)
    fingerprint = svc.input_fingerprint(session, project_id)
    evidence_map = _evidence_map(session, project_id)
    filenames = _filenames(session, project_id)

    items: list[DifferenceOut] = []
    for diff in rows:
        resolved = svc.resolve_review(
            diff.difference_key,
            diff.identity_strength,
            diff.values_digest,
            reviews.get(diff.difference_key),
            fingerprint,
        )
        out = _difference_out(diff, evidence_map, filenames, resolved)
        if severity and out.severity != severity:
            continue
        if difference_type and out.difference_type != difference_type:
            continue
        if sku and sku.upper() not in out.subject_key.upper():
            continue
        if review_status and out.review_status != review_status:
            continue
        if role and role not in out.values_by_document:
            continue
        items.append(out)

    return Envelope(items=items, total=len(items))


@router.put("/projects/{project_id}/reviews/{difference_key}", response_model=DifferenceOut)
def set_review(
    project_id: str, difference_key: str, body: ReviewIn, session: DbSession
) -> DifferenceOut:
    try:
        status = ReviewStatus(body.review_status)
    except ValueError as exc:
        raise svc.ServiceError(
            "INVALID_REVIEW_STATUS", f"未知的审核状态：{body.review_status}"
        ) from exc

    svc.set_review(session, project_id, difference_key, status, body.review_note)
    diff = next(
        d for d in svc.stored_differences(session, project_id) if d.difference_key == difference_key
    )
    reviews = svc.reviews_by_key(session, project_id)
    resolved = svc.resolve_review(
        difference_key,
        diff.identity_strength,
        diff.values_digest,
        reviews.get(difference_key),
        svc.input_fingerprint(session, project_id),
    )
    return _difference_out(
        diff, _evidence_map(session, project_id), _filenames(session, project_id), resolved
    )


@router.post("/projects/{project_id}/corrections", status_code=201)
def add_correction(project_id: str, body: CorrectionIn, session: DbSession) -> dict[str, str]:
    try:
        role = DocumentRole(body.role)
    except ValueError as exc:
        raise svc.ServiceError("INVALID_ROLE", f"未知的文档角色：{body.role}") from exc

    correction = svc.add_correction(
        session,
        project_id,
        role,
        body.scope,
        body.line_key,
        body.field_name,
        body.user_value,
        body.reason,
    )
    return {"id": correction.id, "field_name": correction.field_name}


# ------------------------------------------------------------------------ 报告


@router.get("/projects/{project_id}/report.html")
def report_html(project_id: str, session: DbSession, generated_at: str | None = None) -> Response:
    project = svc.get_project(session, project_id)
    # 报告走 build_result() 现场重算，**不读库里的 ③ 计算产物**，所以哪怕从未运行过
    # 检查它也能渲染出一份完整报告。而报告恰恰是最可能被转发给客户/工厂的那份东西：
    # 一份「没人跑过、也和操作界面上的 0 条差异互相矛盾」的结论发出去，
    # 比没有报告危险得多。要求先运行检查。
    if project.compared_at is None:
        raise svc.ServiceError(
            "COMPARISON_NOT_RUN",
            "该项目尚未运行检查，无法导出报告。请先点「运行检查」。",
            status_code=409,
        )
    result = svc.build_result(session, project.id)

    # 报告是**发出去**的东西：已裁决过的条目如果和从未看过的条目长得一样，
    # 收报告的人只能把全部条目重看一遍，审核工作等于白做。
    stored = svc.reviews_by_key(session, project_id)
    fingerprint = svc.input_fingerprint(session, project_id)
    reviews: dict[str, ReviewState] = {}
    for diff in result.comparison.differences:
        resolved = svc.resolve_review(
            diff.difference_key,
            diff.identity_strength.value,
            diff.values_digest,
            stored.get(diff.difference_key),
            fingerprint,
        )
        if resolved.review_status is not ReviewStatus.OPEN:
            reviews[diff.difference_key] = ReviewState(
                status=resolved.review_status,
                note=resolved.review_note,
                stale_premise=resolved.stale_premise is not None,
            )

    html = render_report(
        result,
        project_name=project.name,
        generated_at=generated_at or project.compared_at or project.created_at,
        reviews=reviews,
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report-{project.id[:8]}.html"'},
    )
