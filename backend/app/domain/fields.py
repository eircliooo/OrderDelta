"""FieldSpec 注册表。SPEC §4、§6.2、§9.4。

**不落库**，本模块是字段规格的唯一来源。`docs/comparison-rules.md` 由它生成
（硬约束 #12：禁止手写），因此规格永不与代码漂移。

三件事在这里定死：
  1. 中英文别名表 —— 归一化后**精确字典查找**，禁止子串包含（SPEC §6.2）
  2. 严重度按 chain_stage 查表 —— **不按字段**（SPEC §9.4，本次修订最重要的一条）
  3. 比较器与容差基准

别名索引**按 scope 分开建**：`total amount` 在表头里是行金额、在表尾是总金额，
同名不同义，混在一个索引里会互相污染。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import ChainStage, DocumentRole, Severity, ValueKind
from app.normalization.text import normalize_header


class ColumnClass(StrEnum):
    """表头列类。表头打分器的硬门槛依赖它（SPEC §6.1）：

    至少命中 2 个不同列类，且必须包含「数量类」与「单价或金额类」各一。
    """

    SKU = "SKU"
    DESCRIPTION = "DESCRIPTION"
    QUANTITY = "QUANTITY"
    PRICE_OR_AMOUNT = "PRICE_OR_AMOUNT"
    UNIT = "UNIT"
    PACKAGING = "PACKAGING"
    OTHER = "OTHER"


#: 表头必须同时命中的两个列类，否则不认为找到了订单表格。
REQUIRED_COLUMN_CLASSES: frozenset[ColumnClass] = frozenset(
    {ColumnClass.QUANTITY, ColumnClass.PRICE_OR_AMOUNT}
)

MIN_DISTINCT_COLUMN_CLASSES = 2


class Comparator(StrEnum):
    TEXT_EXACT = "text_exact"
    TEXT_SEMANTIC = "text_semantic"
    DECIMAL_QUANTIZED = "decimal_quantized"
    MONEY_QUANTIZED = "money_quantized"
    CURRENCY_CODE = "currency_code"
    DATE_ISO = "date_iso"
    INCOTERM_TRIPLE = "incoterm_triple"
    PAYMENT_STRUCTURED = "payment_structured"
    DELIVERY_TERMS = "delivery_terms"
    QUANTITY_WITH_UNIT = "quantity_with_unit"


class FieldScope(StrEnum):
    DOCUMENT = "DOCUMENT"
    LINE_ITEM = "LINE_ITEM"


class MissingPolicy(StrEnum):
    """某份文档没有这个字段时是否产出 MISSING_VALUE。

    真实单据里「只有 PI 写了运输方式」「只有 PO 写了备注」是常态，一律报缺失
    会制造大量噪音，把真正的缺失（缺币种、缺付款条件）淹掉。
    """

    REPORT = "REPORT"
    IGNORE = "IGNORE"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_zh: str
    scope: FieldScope
    value_kind: ValueKind
    comparator: Comparator
    severity_by_stage: Mapping[ChainStage, Severity]
    aliases: tuple[str, ...] = ()
    column_class: ColumnClass = ColumnClass.OTHER
    tolerance_note: str = "精确相等"
    ambiguity_policy: str = "无"
    example: str = ""
    missing_policy: MissingPolicy = MissingPolicy.REPORT


def _sev(q2po: Severity, po2pi: Severity, q2pi: Severity) -> dict[ChainStage, Severity]:
    return {
        ChainStage.OFFER_TO_ORDER: q2po,
        ChainStage.ORDER_TO_CONFIRMATION: po2pi,
        ChainStage.OFFER_TO_CONFIRMATION: q2pi,
    }


# --------------------------------------------------------------------------------
# 行项目字段
# --------------------------------------------------------------------------------

_LINE_ITEM_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="internal_sku",
        label_zh="内部型号",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        column_class=ColumnClass.SKU,
        severity_by_stage=_sev(Severity.INFO, Severity.CRITICAL, Severity.REVIEW),
        aliases=(
            "sku",
            "item no",
            "item no.",
            "item code",
            "item#",
            "model",
            "model no",
            "model no.",
            "art no",
            "art. no",
            "article no",
            "product code",
            "product no",
            "part no",
            "ref no",
            "型号",
            "货号",
            "产品编号",
            "编号",
            "我司型号",
            "内部型号",
            "产品型号",
        ),
        tolerance_note="归一化后精确相等（大小写/全角半角统一，不删前导零）",
        example="AB-100 与 ab-100 视为同一 SKU；0012 与 12 不是同一 SKU",
    ),
    FieldSpec(
        key="customer_part_number",
        label_zh="客户料号",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        column_class=ColumnClass.SKU,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=(
            "customer item",
            "customer item no",
            "client sku",
            "buyer code",
            "customer part no",
            "customer part no.",
            "customer code",
            "buyer item no",
            "客户料号",
            "客户型号",
            "客户编号",
            "客户货号",
        ),
        ambiguity_policy="无法映射到内部 SKU 时产出 WARNING，不强行匹配",
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="description",
        label_zh="产品描述",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        column_class=ColumnClass.DESCRIPTION,
        severity_by_stage=_sev(Severity.REVIEW, Severity.REVIEW, Severity.REVIEW),
        aliases=(
            "description",
            "product description",
            "item description",
            "commodity",
            "product name",
            "goods",
            "品名",
            "产品名称",
            "商品名称",
            "货物描述",
            "描述",
        ),
        tolerance_note="折叠空白后比较；不等时产出 REVIEW（疑似同义），不产出 CRITICAL",
        ambiguity_policy="描述相近但不确定是否同一产品 -> REVIEW，绝不自动判定等价",
    ),
    FieldSpec(
        key="specification",
        label_zh="规格",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        column_class=ColumnClass.DESCRIPTION,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "specification",
            "spec",
            "specs",
            "size",
            "dimension",
            "dimensions",
            "规格",
            "尺寸",
            "规格型号",
        ),
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="color",
        label_zh="颜色",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        column_class=ColumnClass.DESCRIPTION,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.WARNING),
        aliases=("color", "colour", "颜色", "色号", "色别"),
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="quantity",
        label_zh="数量",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.DECIMAL,
        comparator=Comparator.QUANTITY_WITH_UNIT,
        column_class=ColumnClass.QUANTITY,
        severity_by_stage=_sev(Severity.INFO, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "qty",
            "qty.",
            "quantity",
            "order qty",
            "ordered quantity",
            "q'ty",
            "qnty",
            "数量",
            "订购数量",
            "订货数量",
            "订单数量",
        ),
        tolerance_note="Decimal 精确相等（数量不设容差）",
        ambiguity_policy="单位不同 -> INCOMPARABLE，绝不跨单位换算",
        example="Q→PO 数量变化是买方下单行为（INFO）；PO→PI 数量不同是发货风险（CRITICAL）",
    ),
    FieldSpec(
        key="unit",
        label_zh="单位",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        column_class=ColumnClass.UNIT,
        severity_by_stage=_sev(Severity.REVIEW, Severity.REVIEW, Severity.REVIEW),
        aliases=("unit", "units", "uom", "u/m", "单位", "计量单位"),
        tolerance_note="同义单位归一后相等（PCS=PC=PIECE）",
        ambiguity_policy="SETS 与 PCS、CTNS 与 PCS 不能自动换算 -> INCOMPARABLE",
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="unit_price",
        label_zh="单价",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.MONEY,
        comparator=Comparator.MONEY_QUANTIZED,
        column_class=ColumnClass.PRICE_OR_AMOUNT,
        severity_by_stage=_sev(Severity.INFO, Severity.CRITICAL, Severity.WARNING),
        # 注意：裸别名 "price" 已按 SPEC §6.2 删除——它会命中 "Total Price"
        aliases=(
            "unit price",
            "u/p",
            "price per pc",
            "price/pc",
            "price each",
            "单价",
            "单位价格",
            "单件价格",
        ),
        tolerance_note="按币种精度量化后精确相等（跨文档先量化再分桶）",
        example="USD 保留 2 位；JPY 保留 0 位。3333×0.1275=424.9575 量化为 424.96",
    ),
    FieldSpec(
        key="currency",
        label_zh="币种",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.CURRENCY,
        comparator=Comparator.CURRENCY_CODE,
        column_class=ColumnClass.PRICE_OR_AMOUNT,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.CRITICAL),
        aliases=("currency", "curr", "币种", "货币"),
        ambiguity_policy="单独的 $ 标记歧义，不擅自认定为 USD -> REVIEW",
    ),
    FieldSpec(
        key="line_total",
        label_zh="行金额",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.MONEY,
        comparator=Comparator.MONEY_QUANTIZED,
        column_class=ColumnClass.PRICE_OR_AMOUNT,
        severity_by_stage=_sev(Severity.INFO, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "amount",
            "total",
            "total price",
            "total amount",
            "line total",
            "extended price",
            "sub total",
            "subtotal",
            "金额",
            "总额",
            "小计",
            "总价",
            "合计金额",
        ),
        tolerance_note="文档内 quantity×unit_price 校验允许 1 个币种最小单位容差",
    ),
    FieldSpec(
        key="packaging_quantity",
        label_zh="装箱数量",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.DECIMAL,
        comparator=Comparator.DECIMAL_QUANTIZED,
        column_class=ColumnClass.PACKAGING,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=(
            "packaging quantity",
            "packing qty",
            "qty/ctn",
            "pcs/ctn",
            "pc/ctn",
            "装箱数量",
            "每箱数量",
            "装箱量",
            "每箱只数",
        ),
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="carton_count",
        label_zh="箱数",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.DECIMAL,
        comparator=Comparator.DECIMAL_QUANTIZED,
        column_class=ColumnClass.PACKAGING,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=(
            "ctns",
            "ctn",
            "cartons",
            "carton",
            "carton qty",
            "no of cartons",
            "箱数",
            "纸箱数",
            "总箱数",
        ),
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="remarks",
        label_zh="备注",
        scope=FieldScope.LINE_ITEM,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        column_class=ColumnClass.OTHER,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=("remark", "remarks", "note", "notes", "备注", "说明"),
        missing_policy=MissingPolicy.IGNORE,
    ),
)


# --------------------------------------------------------------------------------
# 文档级字段
# --------------------------------------------------------------------------------

_DOCUMENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="document_number",
        label_zh="单据号",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        severity_by_stage=_sev(Severity.INFO, Severity.INFO, Severity.INFO),
        aliases=(
            "quotation no",
            "quotation no.",
            "po no",
            "po no.",
            "pi no",
            "invoice no",
            "order no",
            "contract no",
            "ref no.",
            "报价单号",
            "订单号",
            "合同号",
            "发票号",
            "单号",
        ),
        tolerance_note="三份单据的编号本就不同，默认 INFO，仅作展示",
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="document_date",
        label_zh="单据日期",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.DATE,
        comparator=Comparator.DATE_ISO,
        severity_by_stage=_sev(Severity.REVIEW, Severity.REVIEW, Severity.REVIEW),
        aliases=("date", "issue date", "日期", "开单日期", "报价日期", "订单日期"),
        ambiguity_policy="08/09/2026 类日月歧义 -> REVIEW，不擅自确定",
    ),
    FieldSpec(
        key="buyer_name",
        label_zh="买方",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        severity_by_stage=_sev(Severity.REVIEW, Severity.WARNING, Severity.REVIEW),
        aliases=("buyer", "customer", "messrs", "买方", "客户", "客户名称", "购货单位"),
    ),
    FieldSpec(
        key="seller_name",
        label_zh="卖方",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        severity_by_stage=_sev(Severity.REVIEW, Severity.WARNING, Severity.REVIEW),
        aliases=("seller", "supplier", "vendor", "卖方", "供应商", "供货单位"),
    ),
    FieldSpec(
        key="currency",
        label_zh="币种",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.CURRENCY,
        comparator=Comparator.CURRENCY_CODE,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.CRITICAL),
        aliases=("currency", "币种", "货币", "结算币种"),
        ambiguity_policy="单独的 $ 标记歧义 -> REVIEW",
    ),
    FieldSpec(
        key="incoterm",
        label_zh="贸易术语",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.ENUM,
        comparator=Comparator.INCOTERM_TRIPLE,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "incoterm",
            "incoterms",
            "trade term",
            "trade terms",
            "price term",
            "price terms",
            "价格条款",
            "贸易条款",
            "成交方式",
            "贸易术语",
        ),
        tolerance_note="**三段全等才算相等**（term + named_place + version）",
        example="FOB Shanghai 与 FOB Ningbo 必须判为不等",
    ),
    FieldSpec(
        key="incoterm_named_place",
        label_zh="贸易术语地点",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.WARNING),
        tolerance_note="由 incoterm 拆分得到，不单独设别名",
        ambiguity_policy="与 destination 语义互斥，**禁止跨字段比较**",
    ),
    FieldSpec(
        key="incoterm_version",
        label_zh="贸易术语版本",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_EXACT,
        severity_by_stage=_sev(Severity.INFO, Severity.REVIEW, Severity.INFO),
        tolerance_note="由 incoterm 拆分得到；一方缺失时不参与比较",
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="payment_terms",
        label_zh="付款条件",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.STRUCTURED,
        comparator=Comparator.PAYMENT_STRUCTURED,
        severity_by_stage=_sev(Severity.WARNING, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "payment",
            "payment term",
            "payment terms",
            "terms of payment",
            "payment condition",
            "付款方式",
            "付款条件",
            "支付方式",
            "结算方式",
        ),
        tolerance_note="结构化成功时比 deposit/balance 比例与账期；失败则保留原文",
        ambiguity_policy="任一方无法结构化 -> REVIEW（待确认），**不产出 CRITICAL**",
    ),
    FieldSpec(
        key="delivery_terms",
        label_zh="交期",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.STRUCTURED,
        comparator=Comparator.DELIVERY_TERMS,
        severity_by_stage=_sev(Severity.REVIEW, Severity.WARNING, Severity.REVIEW),
        aliases=(
            "delivery",
            "delivery time",
            "delivery date",
            "lead time",
            "shipment date",
            "time of shipment",
            "delivery term",
            "delivery terms",
            "交货期",
            "交期",
            "装运期",
            "交货时间",
            "交货日期",
        ),
        tolerance_note="结构化 lead_time_days + trigger；失败则保留原文并标待确认",
        ambiguity_policy=(
            "**两侧表述不同类（一方相对条款、一方绝对日期）-> REVIEW，"
            "不得输出 VALUE_CONFLICT / CRITICAL**"
        ),
        example="PO 写 Ship by 2026-09-15、PI 写 30 days after deposit -> REVIEW",
    ),
    FieldSpec(
        key="destination",
        label_zh="目的地",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=("destination", "目的地", "收货地", "交货地点", "目的国"),
        ambiguity_policy="定义为最终收货地/交货地，与 incoterm_named_place 语义互斥",
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="shipping_method",
        label_zh="运输方式",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=(
            "shipping method",
            "shipment",
            "mode of transport",
            "transport",
            "运输方式",
            "装运方式",
            "运输",
        ),
        missing_policy=MissingPolicy.IGNORE,
    ),
    FieldSpec(
        key="grand_total",
        label_zh="总金额",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.MONEY,
        comparator=Comparator.MONEY_QUANTIZED,
        severity_by_stage=_sev(Severity.INFO, Severity.CRITICAL, Severity.WARNING),
        aliases=(
            "grand total",
            "total amount",
            "total value",
            "total",
            "合计",
            "总计",
            "总金额",
            "价格总计",
            "总价",
        ),
        tolerance_note="与 sum(line_total) 的校验允许 1 个币种最小单位容差",
        ambiguity_policy=(
            "存在未解释差额时产出 CALCULATION_ERROR/**REVIEW**（可能来自运费/折扣/税费），"
            "不判 CRITICAL"
        ),
    ),
    FieldSpec(
        key="remarks",
        label_zh="备注",
        scope=FieldScope.DOCUMENT,
        value_kind=ValueKind.TEXT,
        comparator=Comparator.TEXT_SEMANTIC,
        severity_by_stage=_sev(Severity.INFO, Severity.WARNING, Severity.INFO),
        aliases=("remark", "remarks", "note", "notes", "备注", "说明", "其他要求"),
        missing_policy=MissingPolicy.IGNORE,
    ),
)


ALL_FIELDS: tuple[FieldSpec, ...] = _LINE_ITEM_FIELDS + _DOCUMENT_FIELDS


def _build_alias_index(specs: tuple[FieldSpec, ...]) -> dict[str, str]:
    """归一化别名 -> 字段 key。

    同一 scope 内两个字段抢同一个归一化别名是**规格错误**，import 期直接抛。
    宁可启动失败，也不要在运行期静默把 Total Price 认成 unit_price。
    """
    index: dict[str, str] = {}
    for spec in specs:
        for alias in spec.aliases:
            norm = normalize_header(alias)
            if not norm:
                raise ValueError(f"别名归一化后为空：{spec.key} <- {alias!r}")
            existing = index.get(norm)
            if existing is not None and existing != spec.key:
                raise ValueError(
                    f"别名冲突：{norm!r} 同时被 {existing} 与 {spec.key} 声明。"
                    "同一 scope 内别名必须唯一（SPEC §6.2）。"
                )
            index[norm] = spec.key
    return index


BY_KEY: dict[tuple[FieldScope, str], FieldSpec] = {
    (spec.scope, spec.key): spec for spec in ALL_FIELDS
}

LINE_ITEM_ALIAS_INDEX: dict[str, str] = _build_alias_index(_LINE_ITEM_FIELDS)
DOCUMENT_ALIAS_INDEX: dict[str, str] = _build_alias_index(_DOCUMENT_FIELDS)

LINE_ITEM_FIELD_KEYS: tuple[str, ...] = tuple(spec.key for spec in _LINE_ITEM_FIELDS)
DOCUMENT_FIELD_KEYS: tuple[str, ...] = tuple(spec.key for spec in _DOCUMENT_FIELDS)

COLUMN_CLASS_BY_FIELD: dict[str, ColumnClass] = {
    spec.key: spec.column_class for spec in _LINE_ITEM_FIELDS
}


def line_item_spec(key: str) -> FieldSpec:
    return BY_KEY[(FieldScope.LINE_ITEM, key)]


def document_spec(key: str) -> FieldSpec:
    return BY_KEY[(FieldScope.DOCUMENT, key)]


def match_header(text: str, scope: FieldScope) -> str | None:
    """表头 -> 字段 key。归一化后**精确字典查找**，无匹配返回 None。

    禁止子串包含、禁止模糊匹配（SPEC §6.2）。
    """
    index = LINE_ITEM_ALIAS_INDEX if scope is FieldScope.LINE_ITEM else DOCUMENT_ALIAS_INDEX
    return index.get(normalize_header(text))


def severity_for(key: str, scope: FieldScope, stage: ChainStage) -> Severity:
    """按 chain_stage 查严重度。**不按字段**（SPEC §9.4）。"""
    spec = BY_KEY[(scope, key)]
    if stage is ChainStage.WITHIN_DOCUMENT:
        # 文档内恒等式（行金额、总金额）出错是硬错误
        return Severity.CRITICAL
    return spec.severity_by_stage.get(stage, Severity.REVIEW)


# --------------------------------------------------------------------------------
# SKU 存在性有向表（SPEC §9.4）
# --------------------------------------------------------------------------------

_Q = DocumentRole.QUOTATION
_P = DocumentRole.PURCHASE_ORDER
_I = DocumentRole.PROFORMA_INVOICE

#: 出现该 SKU 的角色集合 -> 严重度。键为 frozenset。
SKU_PRESENCE_SEVERITY: dict[frozenset[DocumentRole], Severity] = {
    frozenset({_Q}): Severity.INFO,  # 报价项未被采纳，正常业务
    frozenset({_P}): Severity.CRITICAL,  # 客户订了，我方两份都没有
    frozenset({_I}): Severity.CRITICAL,  # PI 上凭空多出一项
    frozenset({_Q, _P}): Severity.CRITICAL,  # 已下单但 PI 漏货
    frozenset({_P, _I}): Severity.REVIEW,  # 未经报价的成交项
    frozenset({_Q, _I}): Severity.CRITICAL,  # 客户没订却出现在 PI
}


def sku_presence_severity(
    present: frozenset[DocumentRole], compared: frozenset[DocumentRole]
) -> Severity:
    """SKU 只出现在部分文档时的严重度。

    `compared` 是本次实际参与比较的角色集合——只有两份文件时，
    「Q 里没有」不是缺失，是根本没参与（SPEC §1.3）。
    """
    effective = present & compared
    if effective == compared:
        return Severity.INFO
    return SKU_PRESENCE_SEVERITY.get(effective, Severity.REVIEW)


# --------------------------------------------------------------------------------
# docs/comparison-rules.md 生成（硬约束 #12：禁止手写）
# --------------------------------------------------------------------------------

_STAGE_LABEL: dict[ChainStage, str] = {
    ChainStage.OFFER_TO_ORDER: "Q→PO",
    ChainStage.ORDER_TO_CONFIRMATION: "PO→PI",
    ChainStage.OFFER_TO_CONFIRMATION: "Q→PI",
}


def _render_field(spec: FieldSpec) -> str:
    stages = " / ".join(
        f"{label}={spec.severity_by_stage.get(stage, Severity.REVIEW).value}"
        for stage, label in _STAGE_LABEL.items()
    )
    aliases = "、".join(spec.aliases) if spec.aliases else "（不通过别名提取）"
    return "\n".join(
        (
            f"### `{spec.key}` — {spec.label_zh}",
            "",
            f"- **归一化**：{spec.value_kind.value}",
            f"- **比较方式**：`{spec.comparator.value}`",
            f"- **默认风险等级**：{stages}",
            f"- **容差**：{spec.tolerance_note}",
            f"- **不确定情况**：{spec.ambiguity_policy}",
            f"- **示例**：{spec.example or '—'}",
            f"- **别名**：{aliases}",
            "",
        )
    )


def render_comparison_rules_md() -> str:
    """生成 docs/comparison-rules.md 全文。

    由 scripts/gen_docs.py 调用。生成后 `git diff` 必须为空，否则说明有人手写过。
    """
    lines: list[str] = [
        "# 字段比较规则",
        "",
        "> **本文件由 `app/domain/fields.py` 的 FieldSpec 注册表自动生成"
        "（硬约束 #12）。禁止手写。**",
        "> 重新生成（在 `backend/` 下）：`python -m tools.gen_docs`",
        "",
        "严重度按**链路阶段**查表，不按字段（SPEC §9.4）：",
        "",
        "| 阶段 | 含义 |",
        "|---|---|",
        "| Q→PO | 报价单 → 客户采购订单：买方下单行为，砍价/改量是正常业务 |",
        "| PO→PI | 客户采购订单 → 形式发票：**卖方确认环节，错了直接损失** |",
        "| Q→PI | 报价单 → 形式发票：跳过 PO 的旁证，参考价值中等 |",
        "| 文档内 | 单份文档内部的恒等式校验（数量×单价=金额、Σ行金额=总金额） |",
        "",
        "---",
        "",
        "## 行项目字段",
        "",
    ]
    lines.extend(_render_field(spec) for spec in _LINE_ITEM_FIELDS)
    lines.extend(("---", "", "## 文档级字段", ""))
    lines.extend(_render_field(spec) for spec in _DOCUMENT_FIELDS)
    lines.extend(
        (
            "---",
            "",
            "## SKU 存在性有向表",
            "",
            "| 出现于 | 严重度 | 业务含义 |",
            "|---|---|---|",
            "| 仅 Q | INFO | 报价项未被采纳，正常 |",
            "| 仅 PO | CRITICAL | 客户下单了，我方两份单据都没有 |",
            "| 仅 PI | CRITICAL | PI 上凭空多出一项 |",
            "| Q + PO | CRITICAL | 已下单但 PI 漏货 |",
            "| PO + PI | REVIEW | 未经报价的成交项 |",
            "| Q + PI | CRITICAL | 客户没订却出现在 PI |",
            "",
        )
    )
    return "\n".join(lines) + "\n"


#: 参与 `rules_digest()` 的 FieldSpec 字段 —— **只列真正影响判定的**。
#:
#: 刻意排除 `label_zh` / `tolerance_note` / `ambiguity_policy` / `example`：
#: 这四个是纯文案，除了渲染进 `comparison-rules.md` 之外不参与任何判定
#: （全仓可 grep 验证）。把它们算进指纹会导致——给某个字段的「示例」补一句中文说明，
#: 就让全部**弱身份**裁决在 `resolve_review()` 里走 `not same_run` 分支被丢弃；
#: 而那条分支返回的是 `(OPEN, note=None)`，**不是** NEEDS_CONFIRMATION：
#: 用户的备注在界面上直接消失，既没有提示也没有旧前提可看。
#: 一次纯文案改动换来一批无声丢失的人工判断，代价和收益完全不成比例。
_RULE_DIGEST_FIELDS: tuple[str, ...] = (
    "key",
    "scope",
    "value_kind",
    "comparator",
    "severity_by_stage",
    "aliases",
    "column_class",
    "missing_policy",
)


def rules_digest() -> str:
    """比较规则整体的版本指纹。SPEC §3.2 要求 `comparison_input_fingerprint` 含 `rules:` 段。

    由 FieldSpec 注册表里**影响判定的那些字段**规范化序列化后哈希，
    而不是另外手写一份「规则版本号」：手写版本号必然会被忘记递增，
    而忘记的后果恰好是「规则改了但系统当作没改」——也就是这个指纹存在的唯一目的失守。

    排除纯文案字段的理由见 `_RULE_DIGEST_FIELDS`。
    """
    parts: list[str] = []
    for spec in (*_LINE_ITEM_FIELDS, *_DOCUMENT_FIELDS):
        for name in _RULE_DIGEST_FIELDS:
            value = getattr(spec, name)
            if name == "severity_by_stage":
                rendered = ",".join(
                    f"{stage.value}={value[stage].value}" for stage in sorted(value, key=str)
                )
            elif isinstance(value, tuple):
                rendered = ",".join(value)
            else:
                rendered = getattr(value, "value", str(value))
            parts.append(f"{spec.key}.{name}={rendered}")
    # SKU 存在性有向表也决定严重度，同样必须进指纹。
    parts += [
        f"sku_presence.{'+'.join(sorted(r.value for r in roles))}={sev.value}"
        for roles, sev in sorted(
            SKU_PRESENCE_SEVERITY.items(), key=lambda kv: sorted(r.value for r in kv[0])
        )
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
