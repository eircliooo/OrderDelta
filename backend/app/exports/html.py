"""自包含 HTML 检查报告。SPEC §15.2 第 2 条、§13。

产出一个**单文件、零 JS、无任何外部引用**的静态页面：断网 + 后端停机也能打开
（Gate-0 第 12 条：文件内不得出现任何外部 `http(s)://`，机械断言）。

三条安全约束逐条落实：
  1. Jinja2 **显式 `autoescape=True`**（默认是 False，不写就是 XSS——单据里的
     备注、买方名称全是用户可控文本，会原样进报告）
  2. `<head>` 内嵌 CSP meta：`default-src 'none'`
  3. **零 JS**：折叠只用 `<details>` / `<summary>`，不写一行脚本、不挂事件属性

两条产品约束：
  - **中文单语**（硬约束 #1）：英文枚举标识符只存在于 API/DB/golden，
    进了报告一律翻成中文标签。
  - `explanation_key` + `explanation_params` **在这一层**渲染成中文句子
    （SPEC §13.1：数据层不存拼好的句子）。

生成时间由调用方传入，**本模块绝不读取系统时钟**（连 time 模块都不 import）——
否则同一输入两次渲染结果不同，快照测试与「确定性」验收（Gate-0 第 15 条）都无从谈起。

架构边界：本模块**禁止 import `app.db.models`**（SPEC §11.2）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from jinja2 import Environment, StrictUndefined

from app.domain.enums import (
    ROLE_ORDER,
    SEVERITY_ORDER,
    SIGNATURE_TAGS,
    ChainStage,
    DifferenceType,
    DocumentRole,
    EvidenceSourceType,
    ParseReasonCode,
    ParseStatus,
    ReviewStatus,
    Scope,
    Severity,
    SubjectKind,
    ValueSource,
)
from app.domain.fields import ALL_FIELDS
from app.domain.models import DifferenceDraft, EvidenceDraft, ReviewState, ValueCell
from app.pipeline import ProjectResult

# --------------------------------------------------------------------------------
# 逐字文案（测试对这两条做字面断言，改动即改变产品承诺）
# --------------------------------------------------------------------------------

#: SPEC §1.2「不是什么」的报告版表述。必须逐字出现在每一份报告里。
DISCLAIMER = "本工具只能辅助核对，不判断哪份文件正确，不构成贸易、法律或财务结论。"

#: SPEC §1.3：集合驱动比较引入的新失效模式，不得隐藏。
COVERAGE_WARNING = "缺席角色 = 未检查，不等于无差异"

#: SPEC §15.2 第 2 条 / 硬约束 #3。模板里用 `_CSP_PLACEHOLDER` 占位，
#: 由本常量在建模板时填入——**安全头只能有一个来源**，
#: 常量与模板各写一份的话，改了常量而模板照旧的那次改动没有任何东西会红。
CSP_CONTENT = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"

#: 模板源码里的 CSP 占位符。刻意选一个不可能出现在正常 HTML 里的记号。
_CSP_PLACEHOLDER = "@@CSP_CONTENT@@"


# --------------------------------------------------------------------------------
# 枚举 -> 中文标签（硬约束 #1：报告中文单语）
# --------------------------------------------------------------------------------

ROLE_LABEL: dict[DocumentRole, str] = {
    DocumentRole.QUOTATION: "报价单",
    DocumentRole.PURCHASE_ORDER: "采购订单",
    DocumentRole.PROFORMA_INVOICE: "形式发票",
}

SEVERITY_LABEL: dict[Severity, str] = {
    Severity.CRITICAL: "严重",
    Severity.WARNING: "警告",
    Severity.REVIEW: "待复核",
    Severity.INFO: "提示",
}

#: 严重度**不靠颜色单独承载**（考虑色觉障碍）：每一级都有独立的形状记号 + 文字标签，
#: 颜色只是第三重冗余。去掉全部颜色后报告依然可读。
SEVERITY_MARK: dict[Severity, str] = {
    Severity.CRITICAL: "▲",
    Severity.WARNING: "◆",
    Severity.REVIEW: "■",
    Severity.INFO: "●",
}

SEVERITY_CSS: dict[Severity, str] = {
    Severity.CRITICAL: "sev-critical",
    Severity.WARNING: "sev-warning",
    Severity.REVIEW: "sev-review",
    Severity.INFO: "sev-info",
}

SEVERITY_ROW_CSS: dict[Severity, str] = {
    Severity.CRITICAL: "row-critical",
    Severity.WARNING: "row-warning",
    Severity.REVIEW: "row-review",
    Severity.INFO: "row-info",
}

DIFFERENCE_TYPE_LABEL: dict[DifferenceType, str] = {
    DifferenceType.VALUE_CONFLICT: "取值冲突",
    DifferenceType.MISSING_VALUE: "字段缺失",
    DifferenceType.CALCULATION_ERROR: "金额计算不符",
    DifferenceType.UNMATCHED_LINE_ITEM: "行项目未对齐",
    DifferenceType.AMBIGUOUS_MATCH: "匹配存在歧义",
    DifferenceType.SEMANTIC_DIFFERENCE: "表述差异",
    DifferenceType.EXTRACTION_UNCERTAIN: "提取不确定",
    DifferenceType.INCOMPARABLE: "无法比较",
}

SCOPE_LABEL: dict[Scope, str] = {
    Scope.DOCUMENT: "文档级",
    Scope.LINE_ITEM: "行项目",
    Scope.CALCULATION: "单据内计算",
}

CHAIN_STAGE_LABEL: dict[ChainStage, str] = {
    ChainStage.OFFER_TO_ORDER: "报价单 → 采购订单",
    ChainStage.ORDER_TO_CONFIRMATION: "采购订单 → 形式发票",
    ChainStage.OFFER_TO_CONFIRMATION: "报价单 → 形式发票",
    ChainStage.WITHIN_DOCUMENT: "单份单据内部",
}

VALUE_SOURCE_LABEL: dict[ValueSource, str] = {
    ValueSource.PARSER: "机器读取",
    ValueSource.USER_CORRECTION: "人工修正",
}

EVIDENCE_SOURCE_LABEL: dict[EvidenceSourceType, str] = {
    EvidenceSourceType.XLSX_CELL: "Excel 单元格",
    EvidenceSourceType.XLSX_RANGE: "Excel 区域",
    EvidenceSourceType.PDF_TEXT: "PDF 文本",
    EvidenceSourceType.DERIVED: "推导（算式）",
}

PARSE_STATUS_LABEL: dict[ParseStatus, str] = {
    ParseStatus.PENDING: "待处理",
    ParseStatus.OK: "解析成功",
    ParseStatus.NEEDS_REVIEW: "需人工复核",
    ParseStatus.REJECTED: "已拒绝",
    ParseStatus.FAILED: "解析失败",
}

#: 人工裁决状态 -> 中文。报告是**发出去**的东西：已经确认过的差异如果在报告里
#: 与从未看过的差异长得一样，收报告的人只能把全部条目重看一遍，审核就白做了。
REVIEW_STATUS_LABEL: dict[ReviewStatus, str] = {
    ReviewStatus.OPEN: "待处理",
    ReviewStatus.CONFIRMED_DIFFERENCE: "已确认存在差异",
    ReviewStatus.ACCEPTED_DIFFERENCE: "已接受该差异",
    ReviewStatus.NEEDS_CONFIRMATION: "前提已变，需重新确认",
    ReviewStatus.IGNORED: "已忽略",
    ReviewStatus.RESOLVED: "已解决",
}

REVIEW_STATUS_CSS: dict[ReviewStatus, str] = {
    ReviewStatus.OPEN: "rv-open",
    ReviewStatus.CONFIRMED_DIFFERENCE: "rv-confirmed",
    ReviewStatus.ACCEPTED_DIFFERENCE: "rv-accepted",
    ReviewStatus.NEEDS_CONFIRMATION: "rv-stale",
    ReviewStatus.IGNORED: "rv-ignored",
    ReviewStatus.RESOLVED: "rv-resolved",
}

PARSE_REASON_LABEL: dict[ParseReasonCode, str] = {
    ParseReasonCode.UNSUPPORTED_EXT: "不支持的文件类型",
    ParseReasonCode.ENCRYPTED: "文件已加密",
    ParseReasonCode.CORRUPT: "文件损坏",
    ParseReasonCode.INSUFFICIENT_TEXT: "可用文本过少",
    ParseReasonCode.UNSUPPORTED_TEXT_LAYER: "文本层不可用",
    ParseReasonCode.ROW_LIMIT: "超出行数上限",
    ParseReasonCode.SHEET_LIMIT: "超出工作表数上限",
    ParseReasonCode.NO_TABLE_FOUND: "未定位到订单表格",
    ParseReasonCode.FORMULA_WITHOUT_CACHE: "公式没有缓存值",
    ParseReasonCode.FILE_TOO_LARGE: "文件过大",
}

#: 字段 key -> 中文名。两个 scope 里同名字段（currency / remarks）标签一致。
FIELD_LABEL: dict[str, str] = {spec.key: spec.label_zh for spec in ALL_FIELDS}

#: 没有取到值时的占位。空白单元格在报告里必须**看得见**，不能是空白。
EMPTY_VALUE_TEXT = "（未提取到）"


# --------------------------------------------------------------------------------
# 内部标识符 -> 中文（身份串、角色名单、匹配组键）
#
# **这一节的分工是本模块最容易写错的地方。** 引擎产出的 explanation_params 里
# 混着三类东西：
#   ① 引擎自己拼的英文角色标识符（`missing_roles` = "QUOTATION、PROFORMA_INVOICE"）
#   ② 引擎自己拼的中文句子里嵌的角色标识符（`detail` = "QUOTATION 的数量无法解析"）
#   ③ **单据原文**（`buckets` 的等号右边、`sku`、`field`）
# 对三类一视同仁地做 `str.replace("QUOTATION", "报价单")` 会把 ③ 一起改掉：
# 备注写 "AS PER QUOTATION Q2026-001" 的单据（外贸单据里极常见）会在报告里变成
# "AS PER 报价单 Q2026-001" —— 报告恰恰宣称自己在**引用原文**，这是 §15.2 第 1 条
# 「在声称引用原文的位置静默显示一个错值」的同一种失效，只是换了个载体。
# 因此按**参数名**分派本地化策略，未登记的参数一律**原样输出**：
# 露出一个英文标识符是看得见的 bug（有全局断言兜底），改掉单据原文是看不见的。
# --------------------------------------------------------------------------------

#: 引擎拼接角色名单用的分隔符（见 comparison/engine.py 的 "、".join(...)）。
_ROLE_JOINER = "、"

#: 引擎拼接 buckets 用的分隔符：`ROLE、ROLE=值 | ROLE=值`。
_BUCKET_JOINER = " | "


def _role_of(name: str) -> DocumentRole | None:
    try:
        return DocumentRole(name)
    except ValueError:
        return None


def _role_label_of(name: str) -> str:
    role = _role_of(name)
    return ROLE_LABEL[role] if role is not None else name


def localize_role_list(text: str) -> str:
    """「、」分隔的角色标识符串 -> 中文标签。

    **逐 token 精确匹配，不做子串替换**——不是角色标识符的 token 原样保留。
    """
    return _ROLE_JOINER.join(_role_label_of(token.strip()) for token in text.split(_ROLE_JOINER))


def localize_prose(text: str) -> str:
    """引擎自己拼的中文句子里嵌了英文角色标识符，这里才允许子串替换。

    **绝不用于任何单据原文**（备注 / 产品描述 / 买方名称里出现 QUOTATION 一词
    完全正常，替换掉就是篡改原文）。三个角色标识符互不为子串，替换是确定的。
    """
    out = text
    for role in DocumentRole:
        out = out.replace(role.value, ROLE_LABEL[role])
    return out


def localize_buckets(text: str) -> str:
    """`ROLE、ROLE=值 | ROLE=值` -> `中文、中文=值 | 中文=值`。

    **只翻译等号左边的角色名单，等号右边是单据原文，一字不动。**
    值里本来就含 " | " 或 "=" 时按第一个等号切分即可，切歪的片段原样保留。
    """
    parts: list[str] = []
    for chunk in text.split(_BUCKET_JOINER):
        roles, sep, value = chunk.partition("=")
        parts.append(f"{localize_role_list(roles)}{sep}{value}" if sep else chunk)
    return _BUCKET_JOINER.join(parts)


def localize_signature(text: str) -> str:
    """`Q1:P2:I0` -> `报价单 1 行 / 采购订单 2 行 / 形式发票 0 行`。

    role_signature 是给调试和 DB 查询用的紧凑编码，业务员读不懂 `Q1:P2:I0`。
    形状不符合预期时**原样输出**——猜错比不翻译更糟。

    「符合预期」= 与 `SIGNATURE_TAGS` 逐段同序对齐，一段不多一段不少。
    宽松地接受 `Q1:P2` 会把「形式发票那段丢了」渲染成
    「报价单 1 行 / 采购订单 2 行」——读者只会理解成形式发票不参与该组，
    而这正是最需要人工介入时最不能给错的信息。
    """
    tokens = text.split(":")
    if len(tokens) != len(SIGNATURE_TAGS):
        return text
    parts: list[str] = []
    for token, (tag, role) in zip(tokens, SIGNATURE_TAGS, strict=True):
        if token[:1] != tag or not token[1:].isdigit():
            return text
        parts.append(f"{ROLE_LABEL[role]} {token[1:]} 行")
    return " / ".join(parts)


def _line_key_label(key: str) -> str:
    """line_key -> 中文可读串。内部身份串（`sku:AB-200#1`）不该原样示人。"""
    if key.startswith("sku:"):
        body, _, ordinal = key[4:].rpartition("#")
        sku = body or key[4:]
        return sku if ordinal in ("", "1") else f"{sku}（第 {ordinal} 次出现）"
    if key.startswith("cpn:"):
        body, _, ordinal = key[4:].rpartition("#")
        cpn = body or key[4:]
        suffix = "" if ordinal in ("", "1") else f"（第 {ordinal} 次出现）"
        return f"客户料号 {cpn}{suffix}"
    if key.startswith("pos:"):
        sheet, _, row = key[4:].partition("!")
        return f"工作表 {sheet} 第 {row} 行"
    return key


def localize_group_key(key: str) -> str:
    """group_key（`SKU:AB-100` / `NOSKU:PURCHASE_ORDER:16`）-> 中文可读串。

    group_key 是纯自然键，形状由 `app.domain.identity` 定死。它会同时出现在
    「型号 / 主体」列与说明句里，两处必须说同一句话。
    """
    if key.startswith("SKU:"):
        # SKU 段是单据原文，**不做任何替换**；`#attr=值` 是 MVP-1 的消歧后缀
        sku, sep, attr = key[4:].partition("#")
        return f"{sku}（{attr}）" if sep else sku
    if key.startswith("NOSKU:"):
        role_name, _, row = key[6:].partition(":")
        return f"{_role_label_of(role_name)}第 {row} 行（未标型号）"
    # FUZZY 等其余形状属 MVP-1，本版不产出；真出现时至少把角色名翻成中文
    return localize_prose(key)


# --------------------------------------------------------------------------------
# explanation_key -> 中文模板（SPEC §13.1：句子在展示层拼，不在数据层拼）
# --------------------------------------------------------------------------------

EXPLANATION_TEMPLATES: dict[str, str] = {
    "value_conflict": "{field}在各单据上取值不一致：{buckets}。",
    "missing_value": "{field}只在 {present_roles} 上提取到，{missing_roles} 未提取到该字段。",
    "unmatched_line_item": (
        "该行项目只出现在 {present_roles}，{missing_roles} 上没有对应行。比对分组：{group}。"
    ),
    "ambiguous_match": (
        "同一型号在单份单据里出现多行（成员分布 {signature}），本工具不做求和、"
        "不做拆合推断，请人工确认。比对分组：{group}；匹配理由：{reason}。"
    ),
    # `{sku}` 可能是型号，也可能是「工作表 X 第 N 行」（无型号行），
    # 所以后面不能再补一个「行」字，否则会出现「第 16 行 行：」。
    "line_arithmetic_mismatch": (
        "{role} 的 {sku}：数量 × 单价 应为 {expected}，表上写的是 {actual}。"
    ),
    "unexplained_total_delta": (
        "{role} 的合计与行金额之和存在未解释差额 {delta}"
        "（行金额合计 {sum_of_lines}，表上总金额 {grand_total}），"
        "可能来自运费 / 折扣 / 税费，需人工确认。"
    ),
    "incomparable_units": "{field}无法直接比较：{detail}",
    "incomparable_currency": "{field}无法直接比较：{detail}",
    "incomparable_delivery_terms": "{field}需人工换算后才能比较：{detail}",
    "unstructured_payment_terms": "{field}无法可靠结构化：{detail}",
    "unparsable_number": "{field}的提取结果无法解析为数值：{detail}",
    "ambiguous_currency_symbol": "{field}存在币种歧义：{detail}",
    "ambiguous_date": "{field}存在日期歧义：{detail}",
    "incomparable": "{field}在结构上无法比较：{detail}",
}

#: 模板缺参数时的占位。缺参数是代码缺陷，但报告不能因此整份渲染失败。
MISSING_PARAM_TEXT = "（未提供）"


class _MissingParams(dict[str, str]):
    """`str.format_map` 的兜底字典：缺参数返回占位符而不是抛 KeyError。"""

    def __missing__(self, key: str) -> str:
        return MISSING_PARAM_TEXT


#: 参数名 -> 本地化策略。**未登记的参数原样输出**（见本节顶部的分工说明）。
#: 登记新参数前先回答一个问题：它的内容是引擎拼的，还是从单据里读出来的？
PARAM_LOCALIZERS: dict[str, Callable[[str], str]] = {
    # ① 纯角色标识符（单个或「、」分隔）
    "role": localize_role_list,
    "present_roles": localize_role_list,
    "missing_roles": localize_role_list,
    # ② 引擎拼的中文句子，里面嵌了角色标识符
    "detail": localize_prose,
    "reason": localize_prose,
    # ③ 结构化串：角色名单要翻，单据原文不能动
    "buckets": localize_buckets,
    "group": localize_group_key,
    "signature": localize_signature,
    "sku": _line_key_label,
}


def _apply_template(template: str, params: Mapping[str, str]) -> str | None:
    """套用模板；模板本身写坏时返回 None 让调用方走可见回退，**绝不抛**。

    缺参数不算写坏（`_MissingParams` 会填占位符）；写坏指的是模板串里有
    未闭合的花括号或非法格式说明符这类源码级笔误。
    """
    try:
        return template.format_map(_MissingParams(dict(params)))
    except (IndexError, KeyError, ValueError):
        return None


def _verbatim(value: str) -> str:
    """未登记参数的默认策略：**一个字符都不动。**"""
    return value


def localize_params(params: Mapping[str, str]) -> dict[str, str]:
    """按参数名分派本地化策略。返回值的 key 顺序不影响渲染结果。"""
    return {name: PARAM_LOCALIZERS.get(name, _verbatim)(value) for name, value in params.items()}


def render_explanation(key: str, params: Mapping[str, str]) -> str:
    """把 explanation_key + explanation_params 渲染成中文句子。

    未知 key **不抛异常**：显式回退成「未登记的说明模板 + 原始参数」，
    宁可难看也要让用户看见有这么一条差异（静默丢弃差异是最危险的失败方式）。
    """
    localized = localize_params(params)
    detail = "；".join(f"{name}={value}" for name, value in sorted(localized.items()))
    template = EXPLANATION_TEMPLATES.get(key)
    if template is None:
        return f"（未登记的说明模板 {key}）{detail}"
    rendered = _apply_template(template, localized)
    if rendered is None:
        return f"（说明模板 {key} 渲染失败）{detail}"
    return rendered


# --------------------------------------------------------------------------------
# 视图模型：模板只读这些 frozen dataclass，不碰领域对象
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleValueView:
    """一个字段在某份单据上的取值 + 出处（SPEC §9.6）。"""

    role_label: str
    value: str
    source_label: str
    source_css: str
    is_correction: bool
    parser_value: str | None
    correction_reason: str | None
    warning: str | None


@dataclass(frozen=True)
class EvidenceView:
    role_label: str
    filename: str
    source_label: str
    sheet_name: str
    cell_reference: str
    raw_text: str
    derived_from: str


@dataclass(frozen=True)
class DifferenceView:
    ordinal: int
    severity_label: str
    severity_mark: str
    #: 徽章用（着色文字与边框）
    severity_css: str
    #: 整行底色用。与 severity_css 分开，模板里不做字符串切片。
    row_css: str
    type_label: str
    scope_label: str
    stage_label: str
    subject_label: str
    field_label: str
    values: tuple[RoleValueView, ...]
    explanation: str
    evidence: tuple[EvidenceView, ...]
    review_label: str
    review_css: str
    review_note: str | None
    #: 上轮裁决的前提值已变。报告里必须比「已确认」更醒目，否则读者会
    #: 照着一条基于旧数字做出的结论办事。
    review_stale: bool


@dataclass(frozen=True)
class DocumentView:
    role_label: str
    filename: str
    status_label: str
    line_count: int


@dataclass(frozen=True)
class AbsentRoleView:
    role_label: str
    reason: str


@dataclass(frozen=True)
class CountView:
    label: str
    mark: str
    css: str
    count: int


@dataclass(frozen=True)
class ReportView:
    project_name: str
    project_id: str
    generated_at: str
    runnable: bool
    documents: tuple[DocumentView, ...]
    absent_roles: tuple[AbsentRoleView, ...]
    show_coverage_banner: bool
    coverage_warning: str
    disclaimer: str
    counts: tuple[CountView, ...]
    total: int
    differences: tuple[DifferenceView, ...]
    #: 已经过人工裁决的条数（非 OPEN）。0 时报告不显示裁决列，
    #: 免得整列都是「待处理」白占版面。
    reviewed_count: int


# --------------------------------------------------------------------------------
# 视图装配
# --------------------------------------------------------------------------------


def _subject_label(diff: DifferenceDraft) -> str:
    """主体列：型号 / 单据 / 全局，一律显示成中文可读串。

    MATCH_GROUP 走与说明句同一个 `localize_group_key`——同一条差异的主体列
    和说明句必须说同一句话，两处各写一份翻译迟早会漂移。
    """
    key = diff.subject_key
    if diff.subject_kind is SubjectKind.DOCUMENT_ROLE:
        if key == "PROJECT":
            return "全部单据"
        head, sep, rest = key.partition("#")
        head_label = _role_label_of(head)
        return f"{head_label}·{_line_key_label(rest)}" if sep else head_label
    return localize_group_key(key)


def _value_view(role_name: str, cell: ValueCell) -> RoleValueView:
    return RoleValueView(
        role_label=_role_label_of(role_name),
        value=cell.value if cell.present and cell.value is not None else EMPTY_VALUE_TEXT,
        source_label=VALUE_SOURCE_LABEL[cell.source],
        source_css=("src src-fix" if cell.source is ValueSource.USER_CORRECTION else "src"),
        is_correction=cell.source is ValueSource.USER_CORRECTION,
        parser_value=cell.parser_value,
        correction_reason=cell.correction_reason,
        warning=cell.warning,
    )


def _value_views(diff: DifferenceDraft) -> tuple[RoleValueView, ...]:
    """各角色取值，按贸易链条顺序排列（绝不依赖 dict 迭代顺序）。"""

    def order(name: str) -> tuple[int, str]:
        role = _role_of(name)
        return (ROLE_ORDER[role], name) if role is not None else (len(ROLE_ORDER), name)

    return tuple(
        _value_view(name, diff.values_by_document[name])
        for name in sorted(diff.values_by_document, key=order)
    )


def _cell_locator(evidence_id: str) -> str:
    """`{document_id}:{sheet}!{addr}` -> `{sheet}!{addr}`。

    报告里不露内部 id：文档在同一行已经由「文件名」列指明，再拼一遍主键只是噪音。
    """
    _, sep, locator = evidence_id.partition(":")
    return locator if sep else evidence_id


def _evidence_view(evidence: EvidenceDraft, filename: str) -> EvidenceView:
    return EvidenceView(
        role_label=ROLE_LABEL[evidence.role],
        filename=filename,
        source_label=EVIDENCE_SOURCE_LABEL[evidence.source_type],
        sheet_name=evidence.sheet_name or "—",
        cell_reference=evidence.cell_reference or "—",
        raw_text=evidence.raw_text or "—",
        derived_from="、".join(_cell_locator(eid) for eid in evidence.derived_from),
    )


def _evidence_views(
    diff: DifferenceDraft,
    evidence_index: Mapping[str, EvidenceDraft],
    filename_by_document: Mapping[str, str],
) -> tuple[EvidenceView, ...]:
    views: list[EvidenceView] = []
    for evidence_id in diff.evidence_ids:
        evidence = evidence_index.get(evidence_id)
        if evidence is None:
            # 引擎保证每条差异都有证据（Gate-0 第 9 条）。真出现悬空 id 要看得见，
            # 不能静默跳过——那会让报告谎称「这条差异没有出处」。
            views.append(
                EvidenceView(
                    role_label="—",
                    filename="—",
                    source_label="证据记录缺失",
                    sheet_name="—",
                    cell_reference="—",
                    raw_text=f"证据 {evidence_id} 未在本次结果中找到",
                    derived_from="",
                )
            )
            continue
        views.append(_evidence_view(evidence, filename_by_document.get(evidence.document_id, "—")))
    return tuple(views)


def _absent_reason(result: ProjectResult, role: DocumentRole) -> str:
    processing = result.processing.get(role)
    if processing is None:
        return "未上传"
    parsed = processing.parsed
    status = PARSE_STATUS_LABEL.get(parsed.status, parsed.status.value)
    if parsed.reason_code is None:
        return status
    reason = PARSE_REASON_LABEL.get(parsed.reason_code, parsed.reason_code.value)
    return f"{status}：{reason}"


def build_report_view(
    result: ProjectResult,
    project_name: str,
    *,
    generated_at: str,
    reviews: Mapping[str, ReviewState] | None = None,
) -> ReportView:
    """把 `ProjectResult` 摊平成模板视图。纯函数，不读时钟、不读环境。

    `reviews` 按 `difference_key` 索引，由服务层从 `difference_review` 解析后传入
    （本层禁止 import `app.db.models`）。缺省即全部按「待处理」渲染。
    """
    review_by_key = reviews or {}
    snapshot = result.snapshot
    evidence_index = result.evidence
    filename_by_document = {
        doc.document_id: doc.original_filename for doc in snapshot.documents.values()
    }
    compared = sorted(snapshot.compared_roles, key=lambda r: ROLE_ORDER[r])

    documents = tuple(
        DocumentView(
            role_label=ROLE_LABEL[role],
            filename=snapshot.documents[role].original_filename,
            status_label=PARSE_STATUS_LABEL.get(
                snapshot.documents[role].parse_status,
                snapshot.documents[role].parse_status.value,
            ),
            line_count=len(snapshot.documents[role].line_items),
        )
        for role in compared
    )

    absent = tuple(
        AbsentRoleView(role_label=ROLE_LABEL[role], reason=_absent_reason(result, role))
        for role in sorted(DocumentRole, key=lambda r: ROLE_ORDER[r])
        if role not in snapshot.documents
    )

    counts_by_severity = result.comparison.counts_by_severity()
    counts = tuple(
        CountView(
            label=SEVERITY_LABEL[severity],
            mark=SEVERITY_MARK[severity],
            css=SEVERITY_CSS[severity],
            count=counts_by_severity.get(severity, 0),
        )
        for severity in sorted(Severity, key=lambda s: SEVERITY_ORDER[s])
    )

    def _review_of(diff: DifferenceDraft) -> ReviewState:
        return review_by_key.get(diff.difference_key, ReviewState(ReviewStatus.OPEN))

    differences = tuple(
        DifferenceView(
            ordinal=ordinal,
            severity_label=SEVERITY_LABEL[diff.severity],
            severity_mark=SEVERITY_MARK[diff.severity],
            severity_css=SEVERITY_CSS[diff.severity],
            row_css=SEVERITY_ROW_CSS[diff.severity],
            type_label=DIFFERENCE_TYPE_LABEL[diff.difference_type],
            scope_label=SCOPE_LABEL[diff.scope],
            stage_label=CHAIN_STAGE_LABEL[diff.chain_stage],
            subject_label=_subject_label(diff),
            field_label=(
                FIELD_LABEL.get(diff.field_name, diff.field_name) if diff.field_name else "—"
            ),
            values=_value_views(diff),
            explanation=render_explanation(diff.explanation_key, diff.explanation_params),
            evidence=_evidence_views(diff, evidence_index, filename_by_document),
            review_label=REVIEW_STATUS_LABEL[_review_of(diff).status],
            review_css=REVIEW_STATUS_CSS[_review_of(diff).status],
            review_note=_review_of(diff).note or None,
            review_stale=_review_of(diff).stale_premise
            or _review_of(diff).status is ReviewStatus.NEEDS_CONFIRMATION,
        )
        for ordinal, diff in enumerate(result.comparison.differences, start=1)
    )

    return ReportView(
        project_name=project_name,
        project_id=snapshot.project_id,
        generated_at=generated_at,
        runnable=snapshot.runnable,
        documents=documents,
        absent_roles=absent,
        # SPEC §1.3：参与比较的角色少于 3 个就必须醒目提示
        show_coverage_banner=len(compared) < len(DocumentRole),
        coverage_warning=COVERAGE_WARNING,
        disclaimer=DISCLAIMER,
        counts=counts,
        total=len(differences),
        differences=differences,
        reviewed_count=sum(
            1
            for diff in result.comparison.differences
            if _review_of(diff).status is not ReviewStatus.OPEN
        ),
    )


# --------------------------------------------------------------------------------
# 模板：单文件、零 JS、无外部引用
# --------------------------------------------------------------------------------

_TEMPLATE_SOURCE_RAW = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="@@CSP_CONTENT@@">
<title>{{ view.project_name }} — 订单差异检查报告</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 16px;
  background: #eef0f4;
  color: #1b1f24;
  font-size: 14px;
  line-height: 1.65;
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
               "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
}
.sheet {
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 32px 40px;
  background: #ffffff;
  border: 1px solid #d7dce3;
  border-radius: 4px;
}
h1 { margin: 0 0 4px; font-size: 22px; }
h2 {
  margin: 32px 0 12px;
  font-size: 16px;
  padding-bottom: 6px;
  border-bottom: 2px solid #1b1f24;
}
.meta { margin: 0; color: #55606d; font-size: 13px; }
.banner {
  margin: 20px 0 0;
  padding: 14px 16px;
  border: 2px solid #8a5a00;
  border-left-width: 8px;
  background: #fff6e0;
}
.banner strong { font-size: 15px; }
.banner ul { margin: 8px 0 0; padding-left: 22px; }
.notice {
  margin: 16px 0 0;
  padding: 12px 16px;
  border: 1px dashed #55606d;
  background: #f7f8fa;
  color: #333b45;
}
.disclaimer {
  margin: 16px 0 0;
  padding: 12px 16px;
  border: 1px solid #55606d;
  border-left-width: 6px;
  background: #f2f4f7;
  font-weight: 600;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td {
  border: 1px solid #ccd3dc;
  padding: 7px 9px;
  text-align: left;
  vertical-align: top;
}
th { background: #eef1f5; font-weight: 600; }
.counts { display: flex; flex-wrap: wrap; gap: 10px; margin: 0; padding: 0; }
.chip {
  list-style: none;
  min-width: 132px;
  padding: 10px 14px;
  border: 1px solid #ccd3dc;
  border-left-width: 8px;
  background: #f7f8fa;
}
.chip .num { display: block; font-size: 22px; font-weight: 700; }
.badge {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid currentColor;
  border-radius: 2px;
  white-space: nowrap;
  font-weight: 600;
}
.sev-critical { color: #8a1c1c; border-left-color: #8a1c1c; }
.sev-warning { color: #7a4b00; border-left-color: #7a4b00; }
.sev-review { color: #17418a; border-left-color: #17418a; }
.sev-info { color: #414a55; border-left-color: #414a55; }
tr.row-critical > td { background: #fdeceb; }
tr.row-warning > td { background: #fff6e0; }
tr.row-review > td { background: #eaf0fa; }
tr.row-info > td { background: #f4f5f7; }
.values { margin: 0; }
.values > div { padding: 2px 0; }
.values > div + div { border-top: 1px dotted #ccd3dc; }
.role { font-weight: 600; }
.val { font-family: Consolas, "Courier New", monospace; }
.src {
  margin-left: 6px;
  padding: 0 6px;
  border: 1px solid #8d97a4;
  border-radius: 2px;
  font-size: 12px;
  color: #414a55;
}
.src-fix { border-color: #17418a; color: #17418a; font-weight: 600; }
/* 裁决状态同样不靠颜色单独承载——文字标签本身已说清，颜色只是冗余。 */
.rv-open { color: #414a55; }
.rv-confirmed { color: #8a1c1c; }
.rv-accepted { color: #17418a; }
.rv-stale { color: #7a4b00; }
.rv-ignored, .rv-resolved { color: #2f6b3a; }
.stale { color: #7a4b00; font-weight: 600; }
.sub { display: block; color: #55606d; font-size: 12px; }
details { margin: 0; }
summary { cursor: pointer; font-weight: 600; padding: 2px 0; }
.ev { margin-top: 8px; font-size: 12px; }
.ev th { background: #f3f5f8; }
.raw {
  display: inline-block;
  font-family: Consolas, "Courier New", monospace;
  white-space: pre-wrap;
  word-break: break-all;
}
.empty { color: #55606d; }
footer { margin-top: 32px; color: #55606d; font-size: 12px; }
@media print {
  body { background: #ffffff; padding: 0; }
  .sheet { border: 0; max-width: none; }
  details[open] summary { list-style: none; }
}
</style>
</head>
<body>
<main class="sheet">

<h1>{{ view.project_name }}</h1>
<p class="meta">订单差异检查报告 · 生成时间 {{ view.generated_at }} · 项目 {{ view.project_id }}</p>

{% if view.show_coverage_banner %}
<div class="banner">
  <strong>{{ view.coverage_warning }}</strong>
  <div>本次只比较了 {{ view.documents | length }} 份单据。以下角色未参与比较，
  其内容<strong>完全没有被检查</strong>：</div>
  <ul>
  {% for absent in view.absent_roles %}
    <li>{{ absent.role_label }}——{{ absent.reason }}</li>
  {% endfor %}
  </ul>
</div>
{% endif %}

{% if not view.runnable %}
<div class="notice">
  参与比较的单据少于 2 份，<strong>本次未执行任何比较</strong>。
  报告中的「零差异」不代表单据一致。
</div>
{% endif %}

<p class="disclaimer">{{ view.disclaimer }}</p>

<h2>参与比较的单据</h2>
{% if view.documents %}
<table>
  <thead>
    <tr><th>角色</th><th>文件名</th><th>解析状态</th><th>行项目数</th></tr>
  </thead>
  <tbody>
  {% for doc in view.documents %}
    <tr>
      <td>{{ doc.role_label }}</td>
      <td>{{ doc.filename }}</td>
      <td>{{ doc.status_label }}</td>
      <td>{{ doc.line_count }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">没有任何单据进入比较集合。</p>
{% endif %}

<h2>差异概览</h2>
<ul class="counts">
{% for count in view.counts %}
  <li class="chip {{ count.css }}">
    <span class="num">{{ count.count }}</span>
    <span>{{ count.mark }} {{ count.label }}</span>
  </li>
{% endfor %}
</ul>
<p class="meta">共 {{ view.total }} 条差异。风险等级同时用<strong>文字标签</strong>与
<strong>形状记号</strong>区分，不依赖颜色辨识。</p>

<h2>差异明细</h2>
{% if view.differences %}
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>风险等级</th>
      <th>差异类型</th>
      <th>型号 / 主体</th>
      <th>字段</th>
      <th>各单据取值</th>
      <th>说明</th>
      {% if view.reviewed_count %}<th>人工裁决</th>{% endif %}
    </tr>
  </thead>
  <tbody>
  {% for diff in view.differences %}
    <tr class="{{ diff.row_css }}">
      <td>{{ diff.ordinal }}</td>
      <td>
        <span class="badge {{ diff.severity_css }}">{{ diff.severity_mark }}
        {{ diff.severity_label }}</span>
        <span class="sub">{{ diff.stage_label }}</span>
      </td>
      <td>{{ diff.type_label }}<span class="sub">{{ diff.scope_label }}</span></td>
      <td>{{ diff.subject_label }}</td>
      <td>{{ diff.field_label }}</td>
      <td>
        <div class="values">
        {% for value in diff.values %}
          <div>
            <span class="role">{{ value.role_label }}</span>
            <span class="val">{{ value.value }}</span>
            <span class="{{ value.source_css }}">{{ value.source_label }}</span>
            {% if value.is_correction and value.parser_value %}
            <span class="sub">机器原读数：{{ value.parser_value }}</span>
            {% endif %}
            {% if value.correction_reason %}
            <span class="sub">修正理由：{{ value.correction_reason }}</span>
            {% endif %}
            {% if value.warning %}
            <span class="sub">提取提示：{{ value.warning }}</span>
            {% endif %}
          </div>
        {% endfor %}
        </div>
      </td>
      <td>{{ diff.explanation }}</td>
      {% if view.reviewed_count %}
      <td>
        <span class="badge {{ diff.review_css }}">{{ diff.review_label }}</span>
        {% if diff.review_stale %}
        <span class="sub stale">本条裁决所依据的取值已变，请重新确认</span>
        {% endif %}
        {% if diff.review_note %}
        <span class="sub">备注：{{ diff.review_note }}</span>
        {% endif %}
      </td>
      {% endif %}
    </tr>
    <tr class="{{ diff.row_css }}">
      <td colspan="{% if view.reviewed_count %}8{% else %}7{% endif %}">
        <details>
          <summary>展开证据（{{ diff.evidence | length }} 条）</summary>
          <table class="ev">
            <thead>
              <tr>
                <th>角色</th>
                <th>文件名</th>
                <th>来源</th>
                <th>工作表</th>
                <th>单元格</th>
                <th>原文</th>
              </tr>
            </thead>
            <tbody>
            {% for ev in diff.evidence %}
              <tr>
                <td>{{ ev.role_label }}</td>
                <td>{{ ev.filename }}</td>
                <td>{{ ev.source_label }}</td>
                <td>{{ ev.sheet_name }}</td>
                <td>{{ ev.cell_reference }}</td>
                <td>
                  <span class="raw">{{ ev.raw_text }}</span>
                  {% if ev.derived_from %}
                  <span class="sub">依据：{{ ev.derived_from }}</span>
                  {% endif %}
                </td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        </details>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="empty">本次比较未发现差异。请注意：未参与比较的角色不产生任何差异。</p>
{% endif %}

<footer>
  <p>{{ view.disclaimer }}</p>
  <p>本报告为单文件静态页面，不含脚本、不请求任何外部资源，断网亦可打开。</p>
</footer>

</main>
</body>
</html>
"""

#: CSP 头由常量填入（见 CSP_CONTENT 的注释）。占位符必须真的被替换掉——
#: 拼错记号会让报告带着 `@@CSP_CONTENT@@` 发出去，这里 import 期就炸。
if _CSP_PLACEHOLDER not in _TEMPLATE_SOURCE_RAW:  # pragma: no cover - 源码级笔误
    raise RuntimeError(f"HTML 模板里找不到 CSP 占位符 {_CSP_PLACEHOLDER}")

_TEMPLATE_SOURCE = _TEMPLATE_SOURCE_RAW.replace(_CSP_PLACEHOLDER, CSP_CONTENT)

#: **`autoescape=True` 是这份模板唯一的 XSS 防线**（Jinja2 默认 False）。
#: `StrictUndefined` 让模板里的字段笔误在测试期就炸掉，而不是静默渲染成空白。
_ENV = Environment(
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

_TEMPLATE = _ENV.from_string(_TEMPLATE_SOURCE)


def render_report(
    result: ProjectResult,
    project_name: str,
    *,
    generated_at: str,
    reviews: Mapping[str, ReviewState] | None = None,
) -> str:
    """渲染自包含 HTML 报告。

    `generated_at` 由调用方传入（例如 API 层的当前时间字符串）——本模块不读时钟，
    因此同一 `ProjectResult` + 同一时间字符串 + 同一 `reviews` 两次渲染**逐字节一致**。
    """
    return _TEMPLATE.render(
        view=build_report_view(result, project_name, generated_at=generated_at, reviews=reviews)
    )


__all__ = [
    "COVERAGE_WARNING",
    "CSP_CONTENT",
    "DISCLAIMER",
    "EXPLANATION_TEMPLATES",
    "PARAM_LOCALIZERS",
    "ReportView",
    "build_report_view",
    "localize_buckets",
    "localize_group_key",
    "localize_params",
    "localize_prose",
    "localize_role_list",
    "localize_signature",
    "render_explanation",
    "render_report",
]
