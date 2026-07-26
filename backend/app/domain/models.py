"""比较层的纯领域模型。**不含任何 ORM 对象。**

SPEC §11.2：全代码库唯一取值入口是 `ValueResolver.snapshot()`，它返回本模块的
`ProjectSnapshot`；`compare(snapshot, rules)` 是纯函数。

架构边界（有 import 级测试强制）：
`app.comparison` / `app.matching` / `app.exports` **禁止 import `app.db.models`**。
所有跨层数据都经过本模块的 frozen dataclass。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.domain.enums import (
    SIGNATURE_TAGS,
    ChainStage,
    CoverageState,
    DifferenceType,
    DocumentRole,
    EvidenceSourceType,
    IdentityStrength,
    MatchMethod,
    MultiplicityState,
    ParseStatus,
    ReviewStatus,
    Scope,
    Severity,
    SubjectKind,
    ValueSource,
)


@dataclass(frozen=True)
class EvidenceDraft:
    """一条证据。每个 Difference 必须至少关联一条（Gate-0 第 9 条全量断言）。"""

    evidence_id: str
    document_id: str
    role: DocumentRole
    source_type: EvidenceSourceType
    sheet_name: str | None = None
    cell_reference: str | None = None
    row_index: int | None = None
    col_index: int | None = None
    raw_text: str | None = None
    #: CALCULATION_ERROR 的证据 = 两个格 + 一个算式
    derived_from: tuple[str, ...] = ()
    parser_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ValueCell:
    """一个字段在某份文档里的取值 + 出处。

    SPEC §9.6：报告要发给老板和客户，「这个数是机器读的还是人填的」
    **必须精确到字段**，所以 source / parser_value / correction_reason 不能省。
    """

    value: str | None
    source: ValueSource = ValueSource.PARSER
    parser_value: str | None = None
    correction_reason: str | None = None
    confidence: str | None = None
    evidence_id: str | None = None
    warning: str | None = None

    @property
    def present(self) -> bool:
        return self.value is not None and self.value != ""


EMPTY_CELL = ValueCell(value=None)


@dataclass(frozen=True)
class SnapshotLineItem:
    """一行行项目的快照。字段值都已归一化为字符串（Decimal 走 format(d,'f')）。"""

    line_key: str
    document_id: str
    role: DocumentRole
    row_index: int
    sheet_name: str
    sku_norm: str | None
    customer_part_norm: str | None
    unit_norm: str | None
    currency: str | None
    fields: Mapping[str, ValueCell]

    def get(self, key: str) -> ValueCell:
        return self.fields.get(key, EMPTY_CELL)


@dataclass(frozen=True)
class SnapshotDocument:
    """一份文档的快照。"""

    document_id: str
    role: DocumentRole
    original_filename: str
    parse_status: ParseStatus
    currency: str | None
    fields: Mapping[str, ValueCell]
    line_items: tuple[SnapshotLineItem, ...]
    evidence: Mapping[str, EvidenceDraft]

    def get(self, key: str) -> ValueCell:
        return self.fields.get(key, EMPTY_CELL)


@dataclass(frozen=True)
class ProjectSnapshot:
    """比较引擎的唯一输入。

    `documents` 只包含**已上传且解析可用**的角色——未上传/解析失败的角色不进入
    比较集合，因而不产生任何 Difference（SPEC §9.8）。
    """

    project_id: str
    documents: Mapping[DocumentRole, SnapshotDocument]
    skipped_roles: tuple[DocumentRole, ...] = ()

    @property
    def compared_roles(self) -> frozenset[DocumentRole]:
        return frozenset(self.documents)

    @property
    def runnable(self) -> bool:
        """至少两份才能比较（SPEC §1.3）。"""
        return len(self.documents) >= 2


@dataclass(frozen=True)
class MatchMemberDraft:
    line_item_id: str
    line_key: str
    role: DocumentRole
    role_ordinal: int


@dataclass(frozen=True)
class MatchGroupDraft:
    """匹配组。替换原计划的三固定外键 LineItemMatch（SPEC §8.2）。

    三个固定外键在结构上只能表达 1:1:1，无法表达拆行/合行/同 SKU 重复多行。
    """

    group_key: str
    match_method: MatchMethod
    match_reason: str
    multiplicity_state: MultiplicityState
    coverage_state: CoverageState
    members: tuple[MatchMemberDraft, ...]
    match_confidence: str | None = None

    def members_of(self, role: DocumentRole) -> tuple[MatchMemberDraft, ...]:
        return tuple(m for m in self.members if m.role is role)

    @property
    def present_roles(self) -> frozenset[DocumentRole]:
        return frozenset(m.role for m in self.members)

    @property
    def role_signature(self) -> str:
        """'Q1:P2:I0' —— 每个角色的成员数，用于展示与调试。"""
        counts = {role: len(self.members_of(role)) for role in DocumentRole}
        return ":".join(f"{tag}{counts[role]}" for tag, role in SIGNATURE_TAGS)


@dataclass(frozen=True)
class DifferenceDraft:
    """比较引擎的产出。纯数据，由持久化层落库。

    `explanation_key` + `explanation_params` **不存拼好的句子**（SPEC §13.1）：
    句子在展示层渲染，golden test 不得对文案做字符串比对，否则措辞微调红一片。
    """

    difference_key: str
    identity_strength: IdentityStrength
    scope: Scope
    subject_kind: SubjectKind
    subject_key: str
    difference_type: DifferenceType
    severity: Severity
    severity_rule_id: str
    chain_stage: ChainStage
    values_by_document: Mapping[str, ValueCell]
    values_digest: str
    explanation_key: str
    explanation_params: Mapping[str, str]
    evidence_ids: tuple[str, ...]
    field_name: str | None = None
    baseline_role: DocumentRole | None = None
    target_role: DocumentRole | None = None
    confidence: str | None = None
    has_user_input: bool = False

    def sort_key(self) -> tuple[str, str, str, str]:
        """对外输出的稳定排序键（硬约束 #5）。

        绝不依赖 dict 迭代顺序、自增 id 或时间戳——否则「重复执行结果稳定」失守。
        """
        return (
            self.scope.value,
            self.subject_key,
            self.field_name or "",
            self.difference_type.value,
        )


def sort_differences(items: Sequence[DifferenceDraft]) -> tuple[DifferenceDraft, ...]:
    """唯一允许的差异排序入口。"""
    return tuple(sorted(items, key=lambda d: d.sort_key()))


@dataclass(frozen=True)
class ReviewState:
    """一条差异的人工裁决，摊平成纯数据。

    ④ 人工裁决存在 `difference_review` 表里，而 `app.exports` 禁止 import
    `app.db.models`。导出层需要裁决状态（发出去的报告不能把已经确认过的
    8 条又原封不动地列成待处理），所以由调用方在服务层解析好后，
    以本结构传进来——键是 `difference_key`。
    """

    status: ReviewStatus
    note: str | None = None
    #: 上轮裁决的前提值已变（`premise_digest` 对不上），需要人重新确认。
    stale_premise: bool = False
