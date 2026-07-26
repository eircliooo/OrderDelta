"""交期归一化。SPEC §4.1（修订自原第七节 delivery_date / delivery_window）。

原设计只能表达绝对日期，但真实报价单与 PI 上的交期九成写成：

    30 days after receipt of deposit
    收到定金及确认样后 45 天
    Within 60 days after order confirmation

只能存绝对日期的话，每份报价单都会产出一条 MISSING_VALUE 假警报；或把
「Ship by 2026-09-15」与「30 days after deposit」直接比字符串产出 CRITICAL 假警报。

处理方式与 payment_terms 完全一致（结构化优先、失败降级 raw_text + 待确认）。

**关键规则**：两侧表述不同类（一方相对条款、一方绝对日期）时 -> 不可比较，
调用方产出 REVIEW（需人工换算），**不得输出 VALUE_CONFLICT / CRITICAL**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.normalization.dates import parse_date
from app.normalization.text import collapse_ws, nfkc


class DeliveryTrigger(StrEnum):
    """相对交期的起算点。识别不到就返回 None，**不硬塞**。"""

    DEPOSIT = "DEPOSIT"
    ORDER = "ORDER"
    SAMPLE_APPROVAL = "SAMPLE_APPROVAL"
    LC = "LC"
    CONTRACT = "CONTRACT"


_TRIGGER_PATTERNS: tuple[tuple[re.Pattern[str], DeliveryTrigger], ...] = (
    (re.compile(r"(?i)deposit|down\s*payment|预付|定金|订金"), DeliveryTrigger.DEPOSIT),
    (
        re.compile(r"(?i)sample\s*(approval|confirm)|确认样|样品确认|封样"),
        DeliveryTrigger.SAMPLE_APPROVAL,
    ),
    (re.compile(r"(?i)\bl/?c\b|信用证"), DeliveryTrigger.LC),
    (re.compile(r"(?i)contract|合同"), DeliveryTrigger.CONTRACT),
    (
        re.compile(r"(?i)order\s*(confirmation|confirmed)?|下单|订单确认|接单"),
        DeliveryTrigger.ORDER,
    ),
)

#: "30 days" / "45 天" / "6 weeks" / "3 周"
_LEAD_TIME = re.compile(r"(?i)(\d{1,3})\s*(?:个)?\s*(days?|working\s*days?|weeks?|天|日|周|工作日)")

_ABSOLUTE_HINT = re.compile(
    r"(?i)\b(?:ship(?:ment)?\s*(?:by|on|before)|delivery\s*(?:by|on|before)|not\s*later\s*than|by)\b"
    r"|不迟于|之前|前发货|装运日期"
)


@dataclass(frozen=True)
class DeliveryTermsParse:
    raw: str | None
    lead_time_days: int | None = None
    delivery_trigger: DeliveryTrigger | None = None
    absolute_date: str | None = None
    date_ambiguous: bool = False
    structured: bool = False
    warning: str | None = None

    @property
    def is_relative(self) -> bool:
        return self.lead_time_days is not None

    @property
    def is_absolute(self) -> bool:
        return self.absolute_date is not None


def _to_days(amount: int, unit: str) -> int:
    lowered = unit.lower()
    if lowered.startswith(("week", "周")):
        return amount * 7
    return amount


def parse_delivery_terms(raw: object) -> DeliveryTermsParse:
    """结构化交期。无法可靠结构化时保留原文并标待确认。

    >>> parse_delivery_terms("30 days after receipt of deposit").lead_time_days
    30
    >>> parse_delivery_terms("2026-09-15").absolute_date
    '2026-09-15'
    """
    if raw is None:
        return DeliveryTermsParse(raw=None)

    original = str(raw)
    text = collapse_ws(nfkc(original))
    if not text:
        return DeliveryTermsParse(raw=original)

    lead_match = _LEAD_TIME.search(text)
    lead_days = _to_days(int(lead_match.group(1)), lead_match.group(2)) if lead_match else None

    trigger: DeliveryTrigger | None = None
    for pattern, candidate in _TRIGGER_PATTERNS:
        if pattern.search(text):
            trigger = candidate
            break

    if lead_days is not None:
        if trigger is None:
            return DeliveryTermsParse(
                raw=original,
                lead_time_days=lead_days,
                structured=False,
                warning="识别到交货天数但无法确定起算点（定金/下单/确认样？），需人工确认",
            )
        return DeliveryTermsParse(
            raw=original,
            lead_time_days=lead_days,
            delivery_trigger=trigger,
            structured=True,
        )

    parsed_date = parse_date(text)
    if parsed_date.iso is not None:
        return DeliveryTermsParse(raw=original, absolute_date=parsed_date.iso, structured=True)
    if parsed_date.ambiguous:
        return DeliveryTermsParse(
            raw=original,
            date_ambiguous=True,
            structured=False,
            warning=parsed_date.warning or "日期存在日月歧义，未擅自确定",
        )

    hint = "疑似绝对日期表述但无法解析出日期" if _ABSOLUTE_HINT.search(text) else "无法结构化"
    return DeliveryTermsParse(raw=original, structured=False, warning=f"{hint}，保留原文待确认")


def delivery_comparable(a: DeliveryTermsParse, b: DeliveryTermsParse) -> bool:
    """两侧是否可以直接比较。

    **不同类（一方相对条款、一方绝对日期）返回 False** —— 这是本模块存在的理由。
    把「30 days after deposit」和「2026-09-15」判成 VALUE_CONFLICT/CRITICAL
    会让每一份真实报价单都吃一条假警报。
    """
    if not a.structured or not b.structured:
        return False
    return a.is_relative == b.is_relative and a.is_absolute == b.is_absolute


def delivery_equal(a: DeliveryTermsParse, b: DeliveryTermsParse) -> bool:
    """在可比较的前提下判等。调用前必须先过 `delivery_comparable`。"""
    if a.is_relative and b.is_relative:
        return a.lead_time_days == b.lead_time_days and a.delivery_trigger == b.delivery_trigger
    return a.absolute_date == b.absolute_date
