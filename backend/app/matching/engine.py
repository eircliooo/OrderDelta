"""行项目匹配。SPEC §8。

MVP-0 只做**第一级：内部 SKU 精确匹配**。二三级（客户料号映射、模糊候选）属 MVP-1，
接口形状已预留（MatchMethod 枚举里有对应值）。

红线（CLAUDE.md，有回归测试强制）：
**matching 层不做求和、不做拆合推断、不做套装展开。**
多成员组一律产出 AMBIGUOUS_MATCH 交人工——多行求和后比较等于替企业裁定
「分批交货 = 整批」，直接违反「不得自动判断哪份文件正确」。

两条不变量（Gate-0 第 7 条断言）：
  1. 每个 LineItem 恰好属于一个组（不强行匹配）
  2. 没有 LineItem 被静默丢弃（上一条的对偶）
"""

from __future__ import annotations

from collections import defaultdict

from app.domain.enums import (
    CoverageState,
    DocumentRole,
    MatchMethod,
    MultiplicityState,
)
from app.domain.identity import group_key_sku, group_key_unmatched
from app.domain.models import (
    MatchGroupDraft,
    MatchMemberDraft,
    ProjectSnapshot,
    SnapshotLineItem,
)

#: 角色的固定顺序，保证成员列表与 role_ordinal 确定性。
_ROLE_SEQUENCE: tuple[DocumentRole, ...] = (
    DocumentRole.QUOTATION,
    DocumentRole.PURCHASE_ORDER,
    DocumentRole.PROFORMA_INVOICE,
)


def _line_item_id(item: SnapshotLineItem) -> str:
    """快照阶段的行 id。DB 版由持久化层替换成真实主键。"""
    return f"{item.document_id}:{item.line_key}"


def _coverage(present: frozenset[DocumentRole], compared: frozenset[DocumentRole]) -> CoverageState:
    covered = present & compared
    if covered == compared:
        return CoverageState.FULL
    if len(covered) >= 2:
        return CoverageState.PARTIAL
    return CoverageState.ISOLATED


def _build_members(
    rows: list[SnapshotLineItem],
) -> tuple[MatchMemberDraft, ...]:
    """按角色顺序、角色内按行号排序，生成确定性的成员列表。"""
    members: list[MatchMemberDraft] = []
    for role in _ROLE_SEQUENCE:
        same_role = sorted(
            (r for r in rows if r.role is role), key=lambda r: (r.row_index, r.line_key)
        )
        for ordinal, item in enumerate(same_role, start=1):
            members.append(
                MatchMemberDraft(
                    line_item_id=_line_item_id(item),
                    line_key=item.line_key,
                    role=role,
                    role_ordinal=ordinal,
                )
            )
    return tuple(members)


def _multiplicity(rows: list[SnapshotLineItem]) -> MultiplicityState:
    counts: dict[DocumentRole, int] = defaultdict(int)
    for row in rows:
        counts[row.role] += 1
    if any(count > 1 for count in counts.values()):
        return MultiplicityState.MULTI_PER_ROLE
    return MultiplicityState.UNIQUE_PER_ROLE


def match_line_items(snapshot: ProjectSnapshot) -> tuple[MatchGroupDraft, ...]:
    """把各文档的行项目对齐成匹配组。

    返回按 group_key 排序的元组——**绝不依赖 dict 迭代顺序**，
    否则「重复执行结果稳定」失守。
    """
    compared = snapshot.compared_roles

    by_sku: dict[str, list[SnapshotLineItem]] = defaultdict(list)
    without_sku: list[SnapshotLineItem] = []

    for role in _ROLE_SEQUENCE:
        document = snapshot.documents.get(role)
        if document is None:
            continue
        for item in document.line_items:
            if item.sku_norm:
                by_sku[item.sku_norm].append(item)
            else:
                without_sku.append(item)

    groups: list[MatchGroupDraft] = []

    for sku, rows in by_sku.items():
        members = _build_members(rows)
        multiplicity = _multiplicity(rows)
        present = frozenset(r.role for r in rows)
        coverage = _coverage(present, compared)

        if multiplicity is MultiplicityState.MULTI_PER_ROLE:
            reason = (
                # 这段文本会原样进 HTML 报告与前端表格，不能带 Markdown 标记
                f"SKU {sku} 在同一份文档中出现多行，已按 SKU 归为一组但不做字段比较"
                "（不求和、不推断拆合），请人工确认对应关系"
            )
        elif coverage is CoverageState.FULL:
            reason = f"SKU {sku} 在参与比较的全部文档中各出现一次，按内部 SKU 精确匹配"
        else:
            roles = "、".join(sorted(r.value for r in present))
            reason = f"SKU {sku} 仅出现在 {roles}，按内部 SKU 精确匹配（覆盖不全）"

        groups.append(
            MatchGroupDraft(
                group_key=group_key_sku(sku),
                match_method=MatchMethod.SKU_EXACT,
                match_reason=reason,
                multiplicity_state=multiplicity,
                coverage_state=coverage,
                members=members,
                match_confidence="1",
            )
        )

    for item in without_sku:
        members = _build_members([item])
        groups.append(
            MatchGroupDraft(
                group_key=group_key_unmatched(item.role, item.row_index),
                match_method=MatchMethod.UNMATCHED,
                match_reason=(
                    f"{item.role.value} 第 {item.row_index} 行没有内部 SKU；"
                    "客户料号映射与模糊匹配属 MVP-1，本版不做，故不匹配"
                ),
                multiplicity_state=MultiplicityState.UNIQUE_PER_ROLE,
                coverage_state=CoverageState.ISOLATED,
                members=members,
                match_confidence=None,
            )
        )

    return tuple(sorted(groups, key=lambda g: g.group_key))


def assert_partition(snapshot: ProjectSnapshot, groups: tuple[MatchGroupDraft, ...]) -> None:
    """Gate-0 第 7 条：每行恰好属于一个组，且没有行被丢弃。

    在比较流程里实际调用，失败即抛——这是「不强行匹配」与「不静默丢弃」的机器证明。
    """
    all_line_ids = {
        _line_item_id(item)
        for document in snapshot.documents.values()
        for item in document.line_items
    }
    member_ids: list[str] = [m.line_item_id for g in groups for m in g.members]

    duplicates = len(member_ids) - len(set(member_ids))
    if duplicates:
        raise AssertionError(f"有 {duplicates} 行同时属于多个匹配组（违反 UNIQUE 约束）")

    missing = all_line_ids - set(member_ids)
    if missing:
        raise AssertionError(
            f"有 {len(missing)} 行没有进入任何匹配组，被静默丢弃：{sorted(missing)[:5]}"
        )

    extra = set(member_ids) - all_line_ids
    if extra:
        raise AssertionError(f"匹配组引用了不存在的行：{sorted(extra)[:5]}")
