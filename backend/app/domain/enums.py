"""冻结的枚举全集。

规格：docs/SPEC.md §2.1（枚举形状必须现在定死）、§5.3、§9.1、§9.2、§9.4、§12.3。

Gate-0 第 16 条要求「只产已声明枚举」：运行时产出的所有枚举值必须是本模块声明的
子集，由 tests/test_guards.py 的 `TestOnlyDeclaredEnums`（`-m enum_subset`）机械检查；
同一处还把 SPEC §2.1 冻结的词表逐字钉住，改词表必须是一次显式的规格修订。
因此本模块是唯一的枚举来源，任何地方不得使用裸字符串字面量代替这些枚举。
"""

from __future__ import annotations

from enum import StrEnum


class DocumentRole(StrEnum):
    """文档角色。SPEC §1.1。"""

    QUOTATION = "QUOTATION"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    PROFORMA_INVOICE = "PROFORMA_INVOICE"


#: 单据在贸易链条上的先后顺序。chain_stage 的方向由它决定（SPEC §9.4）。
ROLE_ORDER: dict[DocumentRole, int] = {
    DocumentRole.QUOTATION: 0,
    DocumentRole.PURCHASE_ORDER: 1,
    DocumentRole.PROFORMA_INVOICE: 2,
}

#: role_signature（`Q1:P2:I0`）的角色缩写与固定顺序。SPEC §3.2。
#:
#: 生产方（`MatchGroupDraft.role_signature`）与消费方（报告 / 前端的中文化）
#: 必须读同一份定义：形状是「每个角色恰好一段、顺序固定」，
#: 少一段就不是本系统产出的签名，翻译它等于替用户断言「这个角色不参与」。
SIGNATURE_TAGS: tuple[tuple[str, DocumentRole], ...] = (
    ("Q", DocumentRole.QUOTATION),
    ("P", DocumentRole.PURCHASE_ORDER),
    ("I", DocumentRole.PROFORMA_INVOICE),
)


class ParseStatus(StrEnum):
    """SPEC §5.3 冻结词表。"""

    PENDING = "PENDING"
    OK = "OK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ParseReasonCode(StrEnum):
    """结构化拒绝/告警原因。

    必须是字段而非拼在中文文案里，否则 golden test 只能靠字符串匹配（SPEC §5.3）。
    """

    UNSUPPORTED_EXT = "UNSUPPORTED_EXT"
    ENCRYPTED = "ENCRYPTED"
    CORRUPT = "CORRUPT"
    INSUFFICIENT_TEXT = "INSUFFICIENT_TEXT"
    UNSUPPORTED_TEXT_LAYER = "UNSUPPORTED_TEXT_LAYER"
    ROW_LIMIT = "ROW_LIMIT"
    SHEET_LIMIT = "SHEET_LIMIT"
    NO_TABLE_FOUND = "NO_TABLE_FOUND"
    FORMULA_WITHOUT_CACHE = "FORMULA_WITHOUT_CACHE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"


class Scope(StrEnum):
    """差异作用域。SPEC §9。"""

    DOCUMENT = "DOCUMENT"
    LINE_ITEM = "LINE_ITEM"
    CALCULATION = "CALCULATION"


class SubjectKind(StrEnum):
    """difference_key 中 subject 的类别。SPEC §3.3。"""

    DOCUMENT_ROLE = "DOCUMENT_ROLE"
    MATCH_GROUP = "MATCH_GROUP"


class DifferenceType(StrEnum):
    """SPEC §9.1 全集（原计划 7 个 + INCOMPARABLE）。"""

    VALUE_CONFLICT = "VALUE_CONFLICT"
    MISSING_VALUE = "MISSING_VALUE"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    UNMATCHED_LINE_ITEM = "UNMATCHED_LINE_ITEM"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    SEMANTIC_DIFFERENCE = "SEMANTIC_DIFFERENCE"
    EXTRACTION_UNCERTAIN = "EXTRACTION_UNCERTAIN"
    #: 结构上无法比较（币种混合、单位换算未知、计价基准不同）。
    #: 绝不能坍缩成 VALUE_CONFLICT（假警报）或不产出（危险的沉默）。SPEC §9.1。
    INCOMPARABLE = "INCOMPARABLE"


class Severity(StrEnum):
    """风险等级。INFO 是本次修订新增（SPEC §9.4）。

    没有 INFO，「买方砍价」这类正常业务只能落在 REVIEW 及以上，仍会污染总览。
    """

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    REVIEW = "REVIEW"
    INFO = "INFO"


#: 排序用（数字越小越严重）。对外输出的稳定排序依赖它。
SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.REVIEW: 2,
    Severity.INFO: 3,
}


class ChainStage(StrEnum):
    """比较发生在贸易链条的哪一段。SPEC §9.4。

    严重度按链路阶段查表，不按字段——这是本次修订在领域层面最重要的一条。
    """

    OFFER_TO_ORDER = "OFFER_TO_ORDER"  # Q  -> PO
    ORDER_TO_CONFIRMATION = "ORDER_TO_CONFIRMATION"  # PO -> PI
    OFFER_TO_CONFIRMATION = "OFFER_TO_CONFIRMATION"  # Q  -> PI
    WITHIN_DOCUMENT = "WITHIN_DOCUMENT"


def chain_stage_for(baseline: DocumentRole, target: DocumentRole) -> ChainStage:
    """由一对角色推出 chain_stage。参数顺序无关（内部按 ROLE_ORDER 定向）。"""
    lo, hi = sorted((baseline, target), key=lambda r: ROLE_ORDER[r])
    if lo is DocumentRole.QUOTATION and hi is DocumentRole.PURCHASE_ORDER:
        return ChainStage.OFFER_TO_ORDER
    if lo is DocumentRole.PURCHASE_ORDER and hi is DocumentRole.PROFORMA_INVOICE:
        return ChainStage.ORDER_TO_CONFIRMATION
    if lo is DocumentRole.QUOTATION and hi is DocumentRole.PROFORMA_INVOICE:
        return ChainStage.OFFER_TO_CONFIRMATION
    raise ValueError(f"同角色不构成链路阶段: {baseline} / {target}")


class Verdict(StrEnum):
    """四态比较器（+MISSING）。SPEC §9.2。"""

    EQUAL = "EQUAL"
    DIFFERENT = "DIFFERENT"
    INCOMPARABLE = "INCOMPARABLE"
    UNCERTAIN = "UNCERTAIN"
    MISSING = "MISSING"


class MatchMethod(StrEnum):
    """SPEC §3.2 match_group.match_method。"""

    SKU_EXACT = "SKU_EXACT"
    CUSTOMER_PART_MAP = "CUSTOMER_PART_MAP"  # MVP-1
    FUZZY_CANDIDATE = "FUZZY_CANDIDATE"  # MVP-1
    UNMATCHED = "UNMATCHED"
    USER_MANUAL = "USER_MANUAL"  # MVP-1


class MultiplicityState(StrEnum):
    """组内每个角色是否唯一。MULTI 阻断字段比较（SPEC §9.5）。"""

    UNIQUE_PER_ROLE = "UNIQUE_PER_ROLE"
    MULTI_PER_ROLE = "MULTI_PER_ROLE"


class CoverageState(StrEnum):
    """组覆盖了几个角色。缺口不阻断比较（SPEC §9.5）。"""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    ISOLATED = "ISOLATED"


class SelectionState(StrEnum):
    AUTO_SELECTED = "AUTO_SELECTED"
    CANDIDATE = "CANDIDATE"  # MVP-1
    USER_SELECTED = "USER_SELECTED"  # MVP-1
    USER_REJECTED = "USER_REJECTED"  # MVP-1


class MatchGroupStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"


class ReviewStatus(StrEnum):
    """SPEC §12.3 冻结词表。"""

    OPEN = "OPEN"
    CONFIRMED_DIFFERENCE = "CONFIRMED_DIFFERENCE"
    ACCEPTED_DIFFERENCE = "ACCEPTED_DIFFERENCE"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    IGNORED = "IGNORED"
    RESOLVED = "RESOLVED"


class IdentityStrength(StrEnum):
    """WEAK 的差异重跑后不继承审核状态——宁可可见地丢，不可错挂。SPEC §3.3。"""

    STRONG = "STRONG"
    WEAK = "WEAK"


class ExtractionMethod(StrEnum):
    """SPEC §6.3 优先级：user_confirmed > alias > layout。

    白名单同时是「LLM 没有偷偷变成必需依赖」的机器证明（SPEC §14）。
    """

    ALIAS = "alias"
    LAYOUT = "layout"
    USER_CONFIRMED = "user_confirmed"


#: Gate-0 断言用：MVP-0 允许出现的提取方法。出现白名单外的值即失败。
ALLOWED_EXTRACTION_METHODS: frozenset[str] = frozenset(
    {ExtractionMethod.ALIAS, ExtractionMethod.LAYOUT, ExtractionMethod.USER_CONFIRMED}
)


class ValueKind(StrEnum):
    """FieldSpec.value_kind。SPEC §4。"""

    TEXT = "TEXT"
    DECIMAL = "DECIMAL"
    MONEY = "MONEY"
    CURRENCY = "CURRENCY"
    DATE = "DATE"
    ENUM = "ENUM"
    STRUCTURED = "STRUCTURED"


class ValueSource(StrEnum):
    """values_by_document 里每个值的来源。SPEC §9.6。

    报告要发给老板和客户，「这个数是机器读的还是人填的」必须精确到字段。
    """

    PARSER = "PARSER"
    USER_CORRECTION = "USER_CORRECTION"


class CorrectionKind(StrEnum):
    OVERRIDE = "OVERRIDE"
    CONFIRM = "CONFIRM"


class EvidenceSourceType(StrEnum):
    XLSX_CELL = "XLSX_CELL"
    XLSX_RANGE = "XLSX_RANGE"
    PDF_TEXT = "PDF_TEXT"  # MVP-1
    DERIVED = "DERIVED"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    COMPARED = "COMPARED"
