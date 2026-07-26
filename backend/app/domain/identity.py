"""三个身份函数。SPEC §3.3。

这三个纯函数是整个系统最贵的契约：
  - line_key      锚定人工修正（user_correction）
  - group_key     锚定匹配组，必须重复执行稳定
  - difference_key 锚定人工裁决（difference_review），**不含具体数值**

选错会导致人工修正解锚、审核状态静默丢失、golden test 不稳定。
因此它们独立成模块、不依赖 ORM、不依赖任何可变全局状态，并有专门单测。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

from app.domain.enums import DocumentRole, IdentityStrength, Scope, SubjectKind

_HASH_HEX_LEN = 32  # sha256 前 16 字节


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_HASH_HEX_LEN]


# --------------------------------------------------------------------------- line_key


def line_key(
    *,
    sku_norm: str | None,
    sku_ordinal: int,
    customer_part_norm: str | None,
    cpn_ordinal: int,
    sheet_name: str,
    row_index: int,
) -> str:
    """行项目的冻结身份。

    **基于解析器读数生成，永不因人工修正改变。** 否则用户修正 SKU 会让自己的
    修正记录解锚（user_correction 锚在 line_key 上）。

    ordinal 从 1 开始，用于区分同一文档内重复的 SKU / 客户料号。
    """
    if sku_norm:
        return f"sku:{sku_norm}#{sku_ordinal}"
    if customer_part_norm:
        return f"cpn:{customer_part_norm}#{cpn_ordinal}"
    return f"pos:{sheet_name}!{row_index}"


def line_key_is_positional(key: str) -> bool:
    """位置型 line_key 在行序变化时不稳定，据此判定 identity_strength。"""
    return key.startswith("pos:")


def line_key_ordinal(key: str) -> int:
    """取出 line_key 尾部的序号；位置型返回 1。

    序号 >1 表示同一文档内该 SKU 重复出现，属于弱身份（SPEC §3.3）。
    """
    if "#" not in key:
        return 1
    try:
        return int(key.rsplit("#", 1)[1])
    except ValueError:  # pragma: no cover - line_key 由本模块生成，不应走到
        return 1


# --------------------------------------------------------------------------- group_key


def group_key_sku(sku_norm: str) -> str:
    """一级精确匹配的组键。"""
    return f"SKU:{sku_norm}"


def group_key_sku_disambiguated(sku_norm: str, attr: str, value: str) -> str:
    """同 SKU 按属性（颜色/规格）消歧后的组键。MVP-1 使用，形状现在定死。"""
    return f"SKU:{sku_norm}#{attr}={value}"


def group_key_fuzzy(members: Iterable[tuple[DocumentRole, int]]) -> str:
    """模糊候选组键：排序拼接的 ROLE:row_index。MVP-1 使用。

    必须排序，否则同一组在不同迭代顺序下产生不同 key，违反「重复执行结果稳定」。
    """
    parts = sorted(f"{role.value}:{row}" for role, row in members)
    return "FUZZY:" + "|".join(parts)


def group_key_unmatched(role: DocumentRole, row_index: int) -> str:
    """孤立行（无 SKU 或无对应行）的组键。"""
    return f"NOSKU:{role.value}:{row_index}"


# --------------------------------------------------------------------- difference_key


def difference_key(
    *,
    scope: Scope,
    difference_type: str,
    field_name: str | None,
    subject_kind: SubjectKind,
    subject_key: str,
) -> str:
    """差异的稳定身份。

    **刻意不含具体数值。** 含了值一变 key 就变，恰好在最需要继承审核状态的
    场景（用户改了单价后重跑）失效。前提变化通过 values_digest 单独判定。

    也刻意不含任何 DB 主键——重跑时 difference/match_group 全删全插，主键全换新。
    """
    payload = "|".join(
        (
            scope.value,
            difference_type,
            field_name or "",
            f"{subject_kind.value}:{subject_key}",
        )
    )
    return _digest(payload)


def values_digest(values_by_role: dict[str, str | None]) -> str:
    """差异所依据的「前提」摘要。

    重跑后 values_digest != premise_digest 即表示用户当初的判断依据已变化，
    审核状态置 NEEDS_CONFIRMATION 并展示旧前提（SPEC §11.3）。

    必须按角色名排序，否则 dict 顺序会污染摘要。
    """
    parts = [f"{role}={values_by_role[role] or ''}" for role in sorted(values_by_role)]
    return _digest("|".join(parts))


def identity_strength_for(
    *,
    subject_kind: SubjectKind,
    subject_key: str,
    member_line_keys: Sequence[str] = (),
    multiplicity_is_unique: bool = True,
) -> IdentityStrength:
    """判定差异身份强弱。WEAK 的差异重跑后不继承审核状态。

    WEAK 的三种来源（SPEC §3.3）：
      1. 匹配组内某角色有多行（AMBIGUOUS_MATCH 高发区）
      2. 成员行的 line_key 是位置型（无 SKU 也无客户料号）
      3. 成员行是同一 SKU 的第 2 行及以后（ordinal > 1）
    """
    if subject_kind is SubjectKind.DOCUMENT_ROLE:
        return IdentityStrength.STRONG
    if not multiplicity_is_unique:
        return IdentityStrength.WEAK
    if subject_key.startswith(("NOSKU:", "FUZZY:")):
        return IdentityStrength.WEAK
    for key in member_line_keys:
        if line_key_is_positional(key) or line_key_ordinal(key) > 1:
            return IdentityStrength.WEAK
    return IdentityStrength.STRONG
