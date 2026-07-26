"""SQLAlchemy 模型。SPEC §3.2。

四类数据的写权限互斥在这里体现为表结构（SPEC §3.1）：

  ① 文档所述  extracted_field / line_item      只有解析器写
  ② 人工断言  user_correction                   只有人写，锚领域坐标
  ③ 计算产物  difference / match_group / member  只有比较引擎写，可整体删除重算
  ④ 人工裁决  difference_review                 只有人写，**重跑时一行都不碰**

因此：
  - `difference` 表**不含** review_status / review_note
  - `difference_review` **刻意不设 FK** —— 加 FK 就等于把裁决绑回产物的生命周期
  - `user_correction` 锚 (document_id, scope, line_key, field_name)，**不锚主键**
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    compared_at: Mapped[str | None] = mapped_column(String(32), default=None)
    comparison_input_fingerprint: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow, onupdate=utcnow)

    documents: Mapped[list[Document]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "document"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    superseded_at: Mapped[str | None] = mapped_column(String(32), default=None)

    original_filename: Mapped[str] = mapped_column(String(400))
    #: 随机 UUID 文件名，**不含任何用户可控成分**（防路径穿越）
    stored_filename: Mapped[str] = mapped_column(String(80))
    mime_type: Mapped[str] = mapped_column(String(200))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))

    parser_name: Mapped[str | None] = mapped_column(String(64), default=None)
    parser_version: Mapped[str | None] = mapped_column(String(32), default=None)
    parse_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    parse_reason_code: Mapped[str | None] = mapped_column(String(48), default=None)
    parse_detail: Mapped[str | None] = mapped_column(Text, default=None)
    parse_diagnostics: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="documents")

    __table_args__ = (
        Index(
            "ux_document_active_role",
            "project_id",
            "role",
            unique=True,
            sqlite_where=superseded_at.is_(None),
        ),
    )


class UserCorrection(Base):
    """② 人工断言。**锚在领域坐标上，不锚 extracted_field.id。**

    重解析同一文件时主键全换新，锚主键等于所有修正静默变孤儿。
    锚领域坐标免费得到正确行为：重解析 -> 修正存活；替换文件 -> 随 Document 级联消失。
    """

    __tablename__ = "user_correction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    line_key: Mapped[str] = mapped_column(String(200), default="")
    field_name: Mapped[str] = mapped_column(String(64))
    user_value: Mapped[str | None] = mapped_column(Text, default=None)
    value_type: Mapped[str] = mapped_column(String(24), default="TEXT")
    superseded_value: Mapped[str | None] = mapped_column(Text, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "document_id", "scope", "line_key", "field_name", name="ux_correction_anchor"
        ),
    )


class MatchGroup(Base):
    """③ 计算产物。替换原计划的三固定外键 LineItemMatch（SPEC §8.2）。"""

    __tablename__ = "match_group"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    group_key: Mapped[str] = mapped_column(String(200))
    match_method: Mapped[str] = mapped_column(String(32))
    match_confidence: Mapped[str | None] = mapped_column(String(32), default=None)
    match_reason: Mapped[str] = mapped_column(Text)
    role_signature: Mapped[str] = mapped_column(String(32))
    multiplicity_state: Mapped[str] = mapped_column(String(24))
    coverage_state: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="RESOLVED")
    user_decision: Mapped[str | None] = mapped_column(Text, default=None)

    members: Mapped[list[MatchMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("project_id", "group_key", name="ux_group_key"),)


class MatchMember(Base):
    __tablename__ = "match_member"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    match_group_id: Mapped[str] = mapped_column(
        ForeignKey("match_group.id", ondelete="CASCADE"), index=True
    )
    #: 一行只能属于一个组 —— 这是「不强行匹配」的机器证明
    line_item_ref: Mapped[str] = mapped_column(String(300), unique=True)
    line_key: Mapped[str] = mapped_column(String(200))
    document_role: Mapped[str] = mapped_column(String(32))
    role_ordinal: Mapped[int] = mapped_column(Integer)
    selection_state: Mapped[str] = mapped_column(String(24), default="AUTO_SELECTED")

    group: Mapped[MatchGroup] = relationship(back_populates="members")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(24))
    sheet_name: Mapped[str | None] = mapped_column(String(200), default=None)
    cell_reference: Mapped[str | None] = mapped_column(String(24), default=None)
    row_index: Mapped[int | None] = mapped_column(Integer, default=None)
    col_index: Mapped[int | None] = mapped_column(Integer, default=None)
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    derived_from: Mapped[str | None] = mapped_column(Text, default=None)
    parser_metadata: Mapped[str | None] = mapped_column(Text, default=None)


class Difference(Base):
    """③ 计算产物。**不含 review_status / review_note** —— 裁决在 difference_review。"""

    __tablename__ = "difference"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    difference_key: Mapped[str] = mapped_column(String(64))
    identity_strength: Mapped[str] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(16))
    subject_kind: Mapped[str] = mapped_column(String(24))
    subject_key: Mapped[str] = mapped_column(String(200))
    field_name: Mapped[str | None] = mapped_column(String(64), default=None)
    difference_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    severity_rule_id: Mapped[str] = mapped_column(String(120))
    chain_stage: Mapped[str] = mapped_column(String(32))
    baseline_role: Mapped[str | None] = mapped_column(String(32), default=None)
    target_role: Mapped[str | None] = mapped_column(String(32), default=None)
    values_by_document: Mapped[str] = mapped_column(Text)
    values_digest: Mapped[str] = mapped_column(String(64))
    has_user_input: Mapped[int] = mapped_column(Integer, default=0)
    explanation_key: Mapped[str] = mapped_column(String(64))
    explanation_params: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "difference_key", name="ux_difference_key"),)


class DifferenceReview(Base):
    """④ 人工裁决。**刻意不设 FK。**

    重跑 = 全删全插 difference，一行 review 都不碰；读取时按 difference_key 合成。
    加 FK 就等于把裁决绑回产物的生命周期，方案退化成「重跑即丢审核状态」。
    """

    __tablename__ = "difference_review"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    difference_key: Mapped[str] = mapped_column(String(64))
    identity_strength: Mapped[str] = mapped_column(String(8))
    review_status: Mapped[str] = mapped_column(String(32))
    review_note: Mapped[str | None] = mapped_column(Text, default=None)
    #: 做出判断时的 values_digest。与新一轮不同即表示前提已变。
    premise_digest: Mapped[str] = mapped_column(String(64))
    #: 做出判断时项目的输入指纹。用来区分「同一轮」与「重跑后」：
    #: 弱身份差异（重复 SKU 第 2 行、无 SKU 行、多候选组）在同一轮内照常显示裁决，
    #: 跨轮则**不继承**——宁可可见地丢，不可错挂到另一行上。
    run_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    premise_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "difference_key", name="ux_review_key"),)
