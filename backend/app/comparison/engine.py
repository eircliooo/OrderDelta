"""差异引擎。SPEC §9。

三段产出：
  1. 文档级字段比较   scope=DOCUMENT
  2. 行项目比较       scope=LINE_ITEM（按匹配组）
  3. 文档内算术校验   scope=CALCULATION

关键规则（每条都对应一类真实假警报）：
  - 严重度按 chain_stage 查表，**不按字段**
  - N 元分桶，**一条冲突只产出一条差异**
  - 多重性阻断比较（数据本身歧义）；覆盖缺口**不阻断**比较（只是范围问题）
  - 未上传/解析失败的角色不进入比较集合，不产生任何 Difference
  - sum(line_total) != grand_total 判 REVIEW「未解释差额」，**不判 CRITICAL**

架构边界：本模块**禁止 import `app.db.models`**（有 import 级测试强制）。
输入只有 `ProjectSnapshot` 与匹配组，输出只有 frozen dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations

from app.comparison.values import compare_values
from app.domain.enums import (
    ROLE_ORDER,
    SEVERITY_ORDER,
    ChainStage,
    CoverageState,
    DifferenceType,
    DocumentRole,
    EvidenceSourceType,
    MultiplicityState,
    Scope,
    Severity,
    SubjectKind,
    ValueSource,
    Verdict,
    chain_stage_for,
)
from app.domain.fields import (
    DOCUMENT_FIELD_KEYS,
    LINE_ITEM_FIELD_KEYS,
    FieldScope,
    FieldSpec,
    MissingPolicy,
    document_spec,
    line_item_spec,
    severity_for,
    sku_presence_severity,
)
from app.domain.identity import difference_key, identity_strength_for, values_digest
from app.domain.models import (
    DifferenceDraft,
    EvidenceDraft,
    MatchGroupDraft,
    ProjectSnapshot,
    SnapshotDocument,
    SnapshotLineItem,
    ValueCell,
    sort_differences,
)
from app.matching.engine import assert_partition
from app.normalization.numbers import minimal_unit, quantize_money


@dataclass(frozen=True)
class ComparisonResult:
    differences: tuple[DifferenceDraft, ...]
    extra_evidence: tuple[EvidenceDraft, ...]

    def counts_by_severity(self) -> dict[Severity, int]:
        counts = dict.fromkeys(Severity, 0)
        for diff in self.differences:
            counts[diff.severity] += 1
        return counts


# --------------------------------------------------------------------------- 工具


def _worst_stage(
    spec: FieldSpec, scope: FieldScope, roles: list[tuple[DocumentRole, DocumentRole]]
) -> tuple[ChainStage, DocumentRole, DocumentRole, Severity]:
    """在若干候选角色对中挑严重度最高的一对。

    并列时按 ROLE_ORDER 取靠前的一对 —— 确定性优先，绝不依赖集合迭代顺序。
    """
    best: tuple[ChainStage, DocumentRole, DocumentRole, Severity] | None = None
    for left, right in sorted(roles, key=lambda pair: (ROLE_ORDER[pair[0]], ROLE_ORDER[pair[1]])):
        stage = chain_stage_for(left, right)
        severity = severity_for(spec.key, scope, stage)
        if best is None or SEVERITY_ORDER[severity] < SEVERITY_ORDER[best[3]]:
            best = (stage, left, right, severity)
    assert best is not None
    return best


def _cross_bucket_pairs(
    buckets: dict[str, tuple[DocumentRole, ...]],
) -> list[tuple[DocumentRole, DocumentRole]]:
    """取值落在不同桶里的角色对。"""
    pairs: list[tuple[DocumentRole, DocumentRole]] = []
    bucket_list = list(buckets.values())
    for i, left_roles in enumerate(bucket_list):
        for right_roles in bucket_list[i + 1 :]:
            for left in left_roles:
                for right in right_roles:
                    pairs.append((left, right))
    return pairs


def _evidence_ids(cells: dict[DocumentRole, ValueCell]) -> tuple[str, ...]:
    ids = [cell.evidence_id for cell in cells.values() if cell.evidence_id]
    return tuple(sorted(set(ids)))


def _values_map(cells: dict[DocumentRole, ValueCell]) -> dict[str, ValueCell]:
    return {role.value: cell for role, cell in sorted(cells.items(), key=lambda kv: kv[0].value)}


def _digest(cells: dict[DocumentRole, ValueCell]) -> str:
    return values_digest({role.value: cell.value for role, cell in cells.items()})


def _has_user_input(cells: dict[DocumentRole, ValueCell]) -> bool:
    return any(cell.source is ValueSource.USER_CORRECTION for cell in cells.values())


def _make_difference(
    *,
    scope: Scope,
    subject_kind: SubjectKind,
    subject_key: str,
    difference_type: DifferenceType,
    severity: Severity,
    severity_rule_id: str,
    chain_stage: ChainStage,
    cells: dict[DocumentRole, ValueCell],
    explanation_key: str,
    explanation_params: dict[str, str],
    evidence_ids: tuple[str, ...],
    field_name: str | None = None,
    baseline_role: DocumentRole | None = None,
    target_role: DocumentRole | None = None,
    member_line_keys: tuple[str, ...] = (),
    multiplicity_unique: bool = True,
) -> DifferenceDraft:
    return DifferenceDraft(
        difference_key=difference_key(
            scope=scope,
            difference_type=difference_type.value,
            field_name=field_name,
            subject_kind=subject_kind,
            subject_key=subject_key,
        ),
        identity_strength=identity_strength_for(
            subject_kind=subject_kind,
            subject_key=subject_key,
            member_line_keys=member_line_keys,
            multiplicity_is_unique=multiplicity_unique,
        ),
        scope=scope,
        subject_kind=subject_kind,
        subject_key=subject_key,
        difference_type=difference_type,
        severity=severity,
        severity_rule_id=severity_rule_id,
        chain_stage=chain_stage,
        values_by_document=_values_map(cells),
        values_digest=_digest(cells),
        explanation_key=explanation_key,
        explanation_params=explanation_params,
        evidence_ids=evidence_ids,
        field_name=field_name,
        baseline_role=baseline_role,
        target_role=target_role,
        has_user_input=_has_user_input(cells),
    )


# --------------------------------------------------------------- 通用字段比较流程


def _compare_field(
    *,
    spec: FieldSpec,
    field_scope: FieldScope,
    scope: Scope,
    subject_kind: SubjectKind,
    subject_key: str,
    cells: dict[DocumentRole, ValueCell],
    compared_roles: frozenset[DocumentRole],
    currency_by_role: dict[DocumentRole, str | None],
    unit_by_role: dict[DocumentRole, str | None],
    member_line_keys: tuple[str, ...] = (),
    multiplicity_unique: bool = True,
) -> list[DifferenceDraft]:
    """一个字段在一组文档上的完整判定。"""
    present = {role: cell for role, cell in cells.items() if cell.present}
    missing = sorted(
        (role for role in compared_roles if role not in present), key=lambda r: ROLE_ORDER[r]
    )
    out: list[DifferenceDraft] = []

    if not present:
        return out  # 全都没有这个字段 -> 不是差异，是这类单据本来就不写

    common = {
        "scope": scope,
        "subject_kind": subject_kind,
        "subject_key": subject_key,
        "field_name": spec.key,
        "member_line_keys": member_line_keys,
        "multiplicity_unique": multiplicity_unique,
    }

    if missing and spec.missing_policy is MissingPolicy.REPORT:
        pairs = [(p, m) for p in present for m in missing]
        stage, left, right, severity = _worst_stage(spec, field_scope, pairs)
        out.append(
            _make_difference(
                **common,  # type: ignore[arg-type]
                difference_type=DifferenceType.MISSING_VALUE,
                severity=severity,
                severity_rule_id=f"{spec.key}@{stage.value}",
                chain_stage=stage,
                cells=cells,
                explanation_key="missing_value",
                explanation_params={
                    "field": spec.label_zh,
                    "missing_roles": "、".join(r.value for r in missing),
                    "present_roles": "、".join(sorted(r.value for r in present)),
                },
                evidence_ids=_evidence_ids(present),
                baseline_role=left,
                target_role=right,
            )
        )

    if len(present) < 2:
        return out

    result = compare_values(
        spec, present, currency_by_role=currency_by_role, unit_by_role=unit_by_role
    )

    if result.verdict is Verdict.EQUAL:
        return out

    if result.verdict in (Verdict.INCOMPARABLE, Verdict.UNCERTAIN):
        pairs = list(combinations(sorted(present, key=lambda r: ROLE_ORDER[r]), 2))
        stage, left, right, _ = _worst_stage(spec, field_scope, pairs)
        is_incomparable = result.verdict is Verdict.INCOMPARABLE
        out.append(
            _make_difference(
                **common,  # type: ignore[arg-type]
                difference_type=(
                    DifferenceType.INCOMPARABLE
                    if is_incomparable
                    else DifferenceType.EXTRACTION_UNCERTAIN
                ),
                # 无法比较绝不升级为 CRITICAL —— 说成「不一致」是假警报
                severity=Severity.REVIEW,
                severity_rule_id=f"{spec.key}@incomparable",
                chain_stage=stage,
                cells=present,
                explanation_key=result.reason_key or "incomparable",
                explanation_params={
                    "field": spec.label_zh,
                    "detail": result.detail or "",
                },
                evidence_ids=_evidence_ids(present),
                baseline_role=left,
                target_role=right,
            )
        )
        return out

    pairs = _cross_bucket_pairs(result.buckets)
    stage, left, right, severity = _worst_stage(spec, field_scope, pairs)
    difference_type = (
        DifferenceType.SEMANTIC_DIFFERENCE
        if spec.comparator.value == "text_semantic"
        else DifferenceType.VALUE_CONFLICT
    )
    out.append(
        _make_difference(
            **common,  # type: ignore[arg-type]
            difference_type=difference_type,
            severity=severity,
            severity_rule_id=f"{spec.key}@{stage.value}",
            chain_stage=stage,
            cells=present,
            explanation_key="value_conflict",
            explanation_params={
                "field": spec.label_zh,
                "buckets": " | ".join(
                    f"{'、'.join(r.value for r in roles)}={key}"
                    for key, roles in result.buckets.items()
                ),
            },
            evidence_ids=_evidence_ids(present),
            baseline_role=left,
            target_role=right,
        )
    )
    return out


# ------------------------------------------------------------------- 文档级比较


def _compare_document_fields(snapshot: ProjectSnapshot) -> list[DifferenceDraft]:
    compared = snapshot.compared_roles
    currency_by_role = {role: doc.currency for role, doc in snapshot.documents.items()}
    out: list[DifferenceDraft] = []

    for key in DOCUMENT_FIELD_KEYS:
        spec = document_spec(key)
        cells = {role: doc.get(key) for role, doc in snapshot.documents.items()}
        out.extend(
            _compare_field(
                spec=spec,
                field_scope=FieldScope.DOCUMENT,
                scope=Scope.DOCUMENT,
                subject_kind=SubjectKind.DOCUMENT_ROLE,
                subject_key="PROJECT",
                cells=cells,
                compared_roles=compared,
                currency_by_role=currency_by_role,
                unit_by_role={},
            )
        )
    return out


# ------------------------------------------------------------------- 行项目比较


def _line_by_id(snapshot: ProjectSnapshot) -> dict[str, SnapshotLineItem]:
    return {
        f"{doc.document_id}:{item.line_key}": item
        for doc in snapshot.documents.values()
        for item in doc.line_items
    }


def _compare_groups(
    snapshot: ProjectSnapshot, groups: tuple[MatchGroupDraft, ...]
) -> list[DifferenceDraft]:
    compared = snapshot.compared_roles
    index = _line_by_id(snapshot)
    out: list[DifferenceDraft] = []

    for group in groups:
        members = [index[m.line_item_id] for m in group.members if m.line_item_id in index]
        if not members:
            continue
        member_keys = tuple(sorted(m.line_key for m in group.members))
        present_roles = group.present_roles & compared

        # 多重性阻断字段比较：不求和、不推断拆合（CLAUDE.md 红线）
        if group.multiplicity_state is MultiplicityState.MULTI_PER_ROLE:
            cells = {
                item.role: item.get("internal_sku") for item in members if item.role in compared
            }
            out.append(
                _make_difference(
                    scope=Scope.LINE_ITEM,
                    subject_kind=SubjectKind.MATCH_GROUP,
                    subject_key=group.group_key,
                    difference_type=DifferenceType.AMBIGUOUS_MATCH,
                    severity=Severity.REVIEW,
                    severity_rule_id="match@multi_per_role",
                    chain_stage=ChainStage.WITHIN_DOCUMENT,
                    cells=cells,
                    explanation_key="ambiguous_match",
                    explanation_params={
                        "group": group.group_key,
                        "signature": group.role_signature,
                        "reason": group.match_reason,
                    },
                    evidence_ids=_evidence_ids(cells),
                    field_name=None,
                    member_line_keys=member_keys,
                    multiplicity_unique=False,
                )
            )
            continue

        # 覆盖缺口不阻断比较，只额外产出一条 UNMATCHED_LINE_ITEM
        if group.coverage_state is not CoverageState.FULL:
            cells = {
                item.role: item.get("internal_sku") for item in members if item.role in compared
            }
            severity = sku_presence_severity(frozenset(present_roles), compared)
            missing_roles = sorted(compared - present_roles, key=lambda r: ROLE_ORDER[r])
            out.append(
                _make_difference(
                    scope=Scope.LINE_ITEM,
                    subject_kind=SubjectKind.MATCH_GROUP,
                    subject_key=group.group_key,
                    difference_type=DifferenceType.UNMATCHED_LINE_ITEM,
                    severity=severity,
                    severity_rule_id="sku_presence@"
                    + "+".join(sorted(r.value for r in present_roles)),
                    chain_stage=(
                        chain_stage_for(
                            sorted(present_roles, key=lambda r: ROLE_ORDER[r])[0],
                            missing_roles[0],
                        )
                        if present_roles and missing_roles
                        else ChainStage.WITHIN_DOCUMENT
                    ),
                    cells=cells,
                    explanation_key="unmatched_line_item",
                    explanation_params={
                        "group": group.group_key,
                        "present_roles": "、".join(sorted(r.value for r in present_roles)),
                        "missing_roles": "、".join(r.value for r in missing_roles),
                    },
                    evidence_ids=_evidence_ids(cells),
                    field_name="internal_sku",
                    member_line_keys=member_keys,
                )
            )
            if group.coverage_state is CoverageState.ISOLATED:
                continue  # 只有一个角色，没有可比对象

        by_role = {item.role: item for item in members if item.role in compared}
        currency_by_role = {role: item.currency for role, item in by_role.items()}
        unit_by_role = {role: item.unit_norm for role, item in by_role.items()}
        # 行级没有币种时回退到文档级币种
        for role in by_role:
            if not currency_by_role.get(role):
                doc = snapshot.documents.get(role)
                currency_by_role[role] = doc.currency if doc else None

        for key in LINE_ITEM_FIELD_KEYS:
            if key == "internal_sku":
                continue  # SKU 是匹配依据，不再作为差异字段
            spec = line_item_spec(key)
            cells = {role: item.get(key) for role, item in by_role.items()}
            out.extend(
                _compare_field(
                    spec=spec,
                    field_scope=FieldScope.LINE_ITEM,
                    scope=Scope.LINE_ITEM,
                    subject_kind=SubjectKind.MATCH_GROUP,
                    subject_key=group.group_key,
                    cells=cells,
                    compared_roles=frozenset(by_role),
                    currency_by_role=currency_by_role,
                    unit_by_role=unit_by_role,
                    member_line_keys=member_keys,
                )
            )

    return out


# --------------------------------------------------------------------- 算术校验


def _decimal(cell: ValueCell) -> Decimal | None:
    if not cell.present or cell.value is None:
        return None
    try:
        return Decimal(cell.value)
    except (InvalidOperation, ValueError):
        return None


def _derived_evidence(
    document: SnapshotDocument, suffix: str, sources: tuple[str, ...], text: str
) -> EvidenceDraft:
    return EvidenceDraft(
        evidence_id=f"{document.document_id}:calc:{suffix}",
        document_id=document.document_id,
        role=document.role,
        source_type=EvidenceSourceType.DERIVED,
        raw_text=text,
        derived_from=sources,
    )


def _verify_calculations(
    snapshot: ProjectSnapshot,
) -> tuple[list[DifferenceDraft], list[EvidenceDraft]]:
    differences: list[DifferenceDraft] = []
    evidence: list[EvidenceDraft] = []

    for role in sorted(snapshot.documents, key=lambda r: ROLE_ORDER[r]):
        document = snapshot.documents[role]
        currency = document.currency
        tolerance = minimal_unit(currency)
        line_total_sum = Decimal(0)
        sum_usable = True
        #: 参与 Σ 的每个行金额单元格。SPEC §10 要求 DERIVED 证据记录**参与运算的
        #: 全部单元格**，只记 grand_total 等于让用户自己去找那笔差额出在哪一行。
        summed_evidence_ids: list[str] = []

        for item in document.line_items:
            quantity = _decimal(item.get("quantity"))
            unit_price = _decimal(item.get("unit_price"))
            line_total = _decimal(item.get("line_total"))

            if line_total is None:
                sum_usable = False
            else:
                line_total_sum += line_total
                line_total_eid = item.get("line_total").evidence_id
                if line_total_eid:
                    summed_evidence_ids.append(line_total_eid)

            if quantity is None or unit_price is None or line_total is None:
                continue

            expected = quantize_money(quantity * unit_price, currency)
            actual = quantize_money(line_total, currency)
            if abs(expected - actual) <= tolerance:
                continue

            cells = {
                role: ValueCell(
                    value=f"{quantity} × {unit_price} = {expected}，表上为 {actual}",
                    evidence_id=item.get("line_total").evidence_id,
                )
            }
            sources = tuple(
                sorted(
                    eid
                    for eid in (
                        item.get("quantity").evidence_id,
                        item.get("unit_price").evidence_id,
                        item.get("line_total").evidence_id,
                    )
                    if eid
                )
            )
            ev = _derived_evidence(
                document,
                f"line:{item.line_key}",
                sources,
                f"{quantity} × {unit_price} = {expected}（表上 {actual}）",
            )
            evidence.append(ev)
            differences.append(
                _make_difference(
                    scope=Scope.CALCULATION,
                    subject_kind=SubjectKind.DOCUMENT_ROLE,
                    subject_key=f"{role.value}#{item.line_key}",
                    difference_type=DifferenceType.CALCULATION_ERROR,
                    severity=Severity.CRITICAL,
                    severity_rule_id="line_total@within_document",
                    chain_stage=ChainStage.WITHIN_DOCUMENT,
                    cells=cells,
                    explanation_key="line_arithmetic_mismatch",
                    explanation_params={
                        "role": role.value,
                        "sku": item.sku_norm or item.line_key,
                        "expected": format(expected, "f"),
                        "actual": format(actual, "f"),
                    },
                    evidence_ids=(ev.evidence_id, *sources),
                    field_name="line_total",
                    member_line_keys=(item.line_key,),
                )
            )

        grand_total = _decimal(document.get("grand_total"))
        if grand_total is None or not sum_usable or not document.line_items:
            continue

        expected_total = quantize_money(line_total_sum, currency)
        actual_total = quantize_money(grand_total, currency)
        delta = actual_total - expected_total
        if abs(delta) <= tolerance:
            continue

        cells = {
            role: ValueCell(
                value=format(actual_total, "f"),
                evidence_id=document.get("grand_total").evidence_id,
            )
        }
        grand_total_eid = document.get("grand_total").evidence_id
        sources = tuple(sorted({eid for eid in (*summed_evidence_ids, grand_total_eid) if eid}))
        ev = _derived_evidence(
            document,
            "grand_total",
            sources,
            f"Σ行金额（{len(summed_evidence_ids)} 行）= {expected_total}，"
            f"总金额 = {actual_total}，差额 {delta}",
        )
        evidence.append(ev)
        differences.append(
            _make_difference(
                scope=Scope.CALCULATION,
                subject_kind=SubjectKind.DOCUMENT_ROLE,
                subject_key=role.value,
                difference_type=DifferenceType.CALCULATION_ERROR,
                # 真实 PI 几乎总有运费/折扣/模具费 —— 判 CRITICAL 就是稳定误报
                severity=Severity.REVIEW,
                severity_rule_id="grand_total@unexplained_delta",
                chain_stage=ChainStage.WITHIN_DOCUMENT,
                cells=cells,
                explanation_key="unexplained_total_delta",
                explanation_params={
                    "role": role.value,
                    "sum_of_lines": format(expected_total, "f"),
                    "grand_total": format(actual_total, "f"),
                    "delta": format(delta, "f"),
                },
                evidence_ids=(ev.evidence_id, *sources),
                field_name="grand_total",
            )
        )

    return differences, evidence


# ------------------------------------------------------------------------ 入口


def compare(snapshot: ProjectSnapshot, groups: tuple[MatchGroupDraft, ...]) -> ComparisonResult:
    """比较引擎唯一入口。纯函数：同一输入必得同一输出。"""
    assert_partition(snapshot, groups)

    differences: list[DifferenceDraft] = []
    differences.extend(_compare_document_fields(snapshot))
    differences.extend(_compare_groups(snapshot, groups))

    calc_differences, calc_evidence = _verify_calculations(snapshot)
    differences.extend(calc_differences)

    return ComparisonResult(
        differences=sort_differences(differences),
        extra_evidence=tuple(sorted(calc_evidence, key=lambda e: e.evidence_id)),
    )
