"""API 响应模型。SPEC §12.1。

列表统一信封 `{items, total}`。
错误响应 `{error_code, message, detail?}`，**message 不得含服务器绝对路径**。

`explanation` 不在这里拼句子：只返回 `explanation_key` + `explanation_params`，
由前端渲染成中文（SPEC §13.1）。golden test 因此不会被文案微调红一片。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Envelope[T](BaseModel):
    items: list[T]
    total: int


class ErrorBody(BaseModel):
    error_code: str
    message: str
    detail: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DocumentOut(BaseModel):
    id: str
    role: str
    original_filename: str
    file_size: int
    sha256: str
    parse_status: str
    parse_reason_code: str | None
    parse_detail: str | None
    revision: int


class ProjectOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: str
    updated_at: str
    compared_at: str | None
    documents: list[DocumentOut]
    #: 参与比较的角色。未上传/解析失败的角色进 skipped_roles，**不产生任何差异**。
    compared_roles: list[str]
    skipped_roles: list[str]
    severity_counts: dict[str, int]
    open_count: int


class ValueOut(BaseModel):
    value: str | None
    source: str
    parser_value: str | None
    correction_reason: str | None
    warning: str | None
    evidence_id: str | None


class EvidenceOut(BaseModel):
    evidence_id: str
    role: str
    original_filename: str
    source_type: str
    sheet_name: str | None
    cell_reference: str | None
    raw_text: str | None
    derived_from: list[str]


class DifferenceOut(BaseModel):
    difference_key: str
    scope: str
    subject_kind: str
    subject_key: str
    field_name: str | None
    difference_type: str
    severity: str
    severity_rule_id: str
    chain_stage: str
    baseline_role: str | None
    target_role: str | None
    identity_strength: str
    values_by_document: dict[str, ValueOut]
    explanation_key: str
    explanation_params: dict[str, str]
    evidence: list[EvidenceOut]
    has_user_input: bool
    review_status: str
    review_note: str | None
    #: 前提已变时展示「你上次是基于 X 判断的」
    stale_premise: dict[str, str] | None


class ReviewIn(BaseModel):
    review_status: str
    review_note: str | None = None


class CorrectionIn(BaseModel):
    role: str
    scope: str = "LINE_ITEM"
    line_key: str = ""
    field_name: str
    user_value: str | None = None
    reason: str | None = None


class HealthOut(BaseModel):
    status: str
    version: str
    mvp: str
    llm_enabled: bool
