"""付款条件归一化。SPEC §7（保留原第十节）、§4.1、§9.4。

SPEC §7 原文：

> 付款条件：优先结构化 `deposit_percent` / `balance_percent` / `deposit_trigger` /
> `balance_trigger` / `due_days` / `raw_text`；**无法可靠结构化时保留原文并标记待确认。**

因此本模块的默认动作是**降级**，不是猜测：

  - 付款节点识别不到  -> 该 trigger 字段为 None，**绝不硬塞一个最像的值**
  - 比例合计不等于 100% -> 保留已解析的部分，`structured=False` + warning
  - 多于两笔付款 / 多个不同天数 -> 两个槽位表达不了，`structured=False` + warning
  - 完全认不出        -> 全部结构字段 None，`raw` 原样保留 + warning

「一笔付清 = 100%」这个兜底**只在没有任何未换算的比例线索时**才允许，否则就是
把读不懂说成读懂了。以下四类输入曾经静默产出「100% 发货前付清」，现在一律降级：

  - `1/3 deposit, 2/3 before shipment`      —— 分数写法，未换算成比例
  - `USD 3000 deposit, balance before shipment` —— 定额定金，余款比例不可知
  - `余款发货前付清`（只有「余款」一笔）    —— 另一笔没读到，余款不等于 100%
  - `0% deposit`（比例全为 0）              —— 有比例表述但没有一笔真付款

`30 percent` / `30 pct` 这类拼写出来的百分比现在照常识别（原先落进上面的兜底）。

`structured=False` 由比较层翻译成 **REVIEW**（人工确认），**不是** CRITICAL——
把「机器读不懂」说成「两份文件不一致」是假警报（SPEC §9.4 payment_terms 行）。
判断可不可比走 `payment_comparable()`，判断相不相等走 `payment_fields_equal()`。

**全程 Decimal，禁止二进制浮点**（CLAUDE.md 架构边界，有 AST 扫描测试）。
比例内部一律存小数：30% -> Decimal("0.3")。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from app.normalization.numbers import parse_decimal
from app.normalization.text import collapse_ws, nfkc

# --------------------------------------------------------------- 受控词表


class PaymentTrigger(StrEnum):
    """付款节点受控词表（SPEC §7 的 deposit_trigger / balance_trigger）。

    **识别不到就是 None**，不得退化成「挑一个最像的」——付款节点写错的代价是
    钱在错误的时点出门，比多一条待确认严重得多。
    """

    ORDER = "ORDER"  # 下单 / 合同签订 / 预付 / 定金（付款时点＝下单）
    DEPOSIT = "DEPOSIT"  # 收到定金**之后**（定金本身是事件）
    SHIPMENT = "SHIPMENT"  # 发货前 / 装运前
    BL_COPY = "BL_COPY"  # 凭提单副本
    BL_DATE = "BL_DATE"  # 提单日期之后（通常配合 due_days）
    DELIVERY = "DELIVERY"  # 到货 / 收货之后
    SAMPLE_APPROVAL = "SAMPLE_APPROVAL"  # 确认样之后
    SIGHT = "SIGHT"  # 即期 / 见单即付（due_days = 0）


#: 节点识别的正则来源。**顺序即优先级**，具体的排在笼统的前面：
#: `提单日期` 必须先于 `提单副本` 判定，`收到定金后` 必须先于裸 `定金`。
_TRIGGER_SOURCES: tuple[tuple[PaymentTrigger, tuple[str, ...]], ...] = (
    (
        PaymentTrigger.BL_DATE,
        (r"b/?l\s*date", r"bill of lading date", r"date of (?:the )?b/?l", r"提单日"),
    ),
    (
        PaymentTrigger.BL_COPY,
        (r"b/?l\s*cop", r"cop(?:y|ies) of (?:the )?b/?l", r"提单副本", r"副本提单"),
    ),
    (
        PaymentTrigger.SIGHT,
        (
            r"at sight",
            r"sight l/?c",
            r"after sight",
            r"\d+\s*days?\s+sight",
            r"即期",
            r"见单",
            r"见票",
        ),
    ),
    (
        PaymentTrigger.SAMPLE_APPROVAL,
        (
            r"sample approval",
            r"approval of (?:the )?samples?",
            r"approved samples?",
            r"confirmed samples?",
            r"确认样",
            r"样品确认",
            r"签样",
            r"封样",
        ),
    ),
    (
        PaymentTrigger.SHIPMENT,
        (
            r"before shipment",
            r"prior to shipment",
            r"before loading",
            r"发货前",
            r"装运前",
            r"装船前",
            r"出货前",
            r"发货之前",
        ),
    ),
    (
        PaymentTrigger.DELIVERY,
        (
            r"after delivery",
            r"upon delivery",
            r"on arrival",
            r"after arrival",
            r"receipt of (?:the )?goods",
            r"到货",
            r"货到",
            r"收货后",
            r"验收后",
        ),
    ),
    (
        PaymentTrigger.DEPOSIT,
        (
            r"(?:after|upon|against|from)\s+(?:the\s+)?(?:receipt of\s+)?deposit",
            r"receipt of (?:the )?deposit",
            r"after down payment",
            r"收到定金",
            r"收到订金",
            r"定金后",
            r"定金到账",
        ),
    ),
    (
        PaymentTrigger.ORDER,
        (
            r"in advance",
            r"advance payment",
            r"prepaid",
            r"prepayment",
            r"deposit",
            r"down payment",
            r"(?:after|upon|with|against|on)\s+(?:the\s+)?order",
            r"order confirmation",
            r"(?:upon|after|on)\s+signing",
            r"contract signing",
            r"signing of (?:the )?contract",
            r"定金",
            r"订金",
            r"预付",
            r"下单",
            r"合同签订",
            r"签订合同",
            r"订单确认",
            r"签约",
        ),
    ),
)

_TRIGGER_PATTERNS: tuple[tuple[re.Pattern[str], PaymentTrigger], ...] = tuple(
    (re.compile("|".join(sources)), trigger) for trigger, sources in _TRIGGER_SOURCES
)

# --------------------------------------------------------------- 词法

#: 子句分隔符。中英文标点都要收，真实单据里两种混用。
_SEG_SPLIT = re.compile(r"[,，;；。、\n]+|\+|\band\b")

#: 换行**必须先换成子句分隔符**再走 collapse_ws。
#: Excel 里付款条件几乎总是分行写，折叠成空格后「30% 定金 / 余款发货前付清」
#: 会粘成一个子句，第二行的付款节点直接污染第一笔（deposit_trigger 变成 SHIPMENT）。
_LINE_BREAK = re.compile(r"[\r\n\u2028\u2029]+")

#: 百分比 token。数字**必须**带百分号或拼写出来的 percent/pct——
#: 「30 days」绝不能被读成 30%。group(1) 是数字本身。
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per\s*cent\b|pct\b)")

#: 未换算成比例的付款金额/分数线索。出现它就说明原文的分账是用金额或分数表达的，
#: 我们没把它换算成比例，此时**不得**走「没写比例 = 一笔付清 100%」的兜底
#: （那是猜测，不是解析）。
#: 分数写法：`1/3`、`30/70`；定额写法：`USD 3,000`、`$500`、`3000USD`、`3000元`。
#: `b/l`、`t/t`、`d/p` 两侧无数字，不会命中。
#: 已知代价：`货款 3000 元发货前付清`（金额其实是总价）也会降级成待确认。
#: 这是刻意选的方向——多一条 REVIEW 可见可消，编一个「100% 发货前」不可见。
_UNCONVERTED_SPLIT = re.compile(
    r"\d+\s*/\s*\d+"
    r"|(?:[$€£¥￥]|usd|eur|gbp|jpy|cny|rmb|hkd|aud|cad)\s*\.?\s*\d"
    r"|\d\s*(?:usd|eur|gbp|jpy|cny|rmb|hkd|aud|cad|元|美元|美金|人民币)"
)

#: 「余款」类关键词：比例 = 100% - 其余已知比例。
_REMAINDER = re.compile(r"balance|remaining|remainder|余款|尾款|余额|余下|其余|剩余")

#: 账期天数。`日` 必须带 `内`/`后`，否则会把日期「5月30日」读成 30 天。
_DUE_DAYS = re.compile(r"(\d{1,3})\s*(?:days?|天|日内|日后)")

_MAX_INSTALLMENTS = 2

# --------------------------------------------------------------- 结果类型


@dataclass(frozen=True)
class PaymentTermsParse:
    """付款条件结构化结果（SPEC §7 六元组）。

    字段语义（**两个槽位，按原文出现顺序**）：

      - `deposit_percent` / `deposit_trigger`：第一笔（定金 / 预付）
      - `balance_percent` / `balance_trigger`：第二笔（尾款）
      - 单笔付清（`100% before shipment`、`L/C at sight`）记为
        `deposit_percent=1`、`balance_percent=0`、`balance_trigger=None`
      - `due_days`：账期天数；即期（SIGHT）为 0；识别不到为 None
      - `structured=False` 时**已解析的部分照样保留**，供人工确认时参考

    比例一律是小数（30% -> Decimal("0.3")），不是 30。

    >>> parse_payment_terms("30% deposit, balance against B/L copy").balance_percent
    Decimal('0.7')
    >>> parse_payment_terms("L/C at sight").due_days
    0
    >>> parse_payment_terms("Payment to be discussed").structured
    False
    """

    raw: str | None
    deposit_percent: Decimal | None = None
    balance_percent: Decimal | None = None
    #: PaymentTrigger 是 StrEnum，运行时就是 str（`x == "SHIPMENT"` 为真），
    #: 标成枚举是为了让 mypy 守住受控词表。
    deposit_trigger: PaymentTrigger | None = None
    balance_trigger: PaymentTrigger | None = None
    due_days: int | None = None
    structured: bool = False
    warning: str | None = None

    @property
    def total_percent(self) -> Decimal | None:
        """已解析比例的合计。两个槽位都为 None 时返回 None。"""
        parts = [p for p in (self.deposit_percent, self.balance_percent) if p is not None]
        if not parts:
            return None
        return sum(parts, Decimal(0))


@dataclass(frozen=True)
class _Installment:
    """一笔付款。`percent is None` 表示原文写的是「余款 / balance」，待补齐。"""

    percent: Decimal | None
    trigger: PaymentTrigger | None
    due_days: int | None


# --------------------------------------------------------------- 内部工具


def _fmt_percent(value: Decimal) -> str:
    """把小数比例格式化成人看的百分数：Decimal("0.3") -> '30'。"""
    return format((value * Decimal(100)).normalize(), "f")


def _detect_trigger(text: str) -> PaymentTrigger | None:
    """按优先级查受控词表。查不到返回 None——**不猜**。"""
    for pattern, trigger in _TRIGGER_PATTERNS:
        if pattern.search(text):
            return trigger
    return None


def _detect_due_days(text: str, trigger: PaymentTrigger | None) -> tuple[int | None, str | None]:
    """账期天数，返回 `(天数, 错误)`。即期（SIGHT）没写天数时是 0，这是定义不是猜测。

    同一子句里出现**两个不同的天数**（"45 days after B/L date or 30 days after
    delivery"）时返回错误。取第一个 = 替企业在两套账期之间选边，而账期选错就是
    钱在错误的时点出门。
    """
    values = {int(match.group(1)) for match in _DUE_DAYS.finditer(text)}
    if len(values) > 1:
        listed = "/".join(str(v) for v in sorted(values))
        return None, f"同一子句里出现多个账期天数（{listed} 天），无法确定以哪个为准"
    if values:
        return values.pop(), None
    if trigger is PaymentTrigger.SIGHT:
        return 0, None
    return None, None


def _split_on_percents(part: str) -> list[str]:
    """一个子句里出现多个百分比时按百分比切开。

    覆盖「30% T/T in advance 70% before shipment」这种**没有标点**的写法；
    不切开的话第二个节点会污染第一笔付款。
    """
    matches = list(_PERCENT.finditer(part))
    if len(matches) <= 1:
        return [part]
    pieces: list[str] = []
    for index, match in enumerate(matches):
        start = 0 if index == 0 else match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(part)
        pieces.append(part[start:end])
    return pieces


def _segments(text: str) -> list[str]:
    """先按标点切，再按百分比切。"""
    out: list[str] = []
    parts: list[str] = [p for p in _SEG_SPLIT.split(text) if p]
    for part in parts:
        stripped = part.strip()
        if stripped:
            out.extend(_split_on_percents(stripped))
    return out


def _read_segment(seg: str) -> tuple[_Installment | None, str | None]:
    """把一个子句读成一笔付款。

    返回 `(付款, 错误)`：两者都为 None 表示该子句不承载付款（例如「T/T」本身，
    或一笔 `0%` —— 0% 不是一笔付款，丢掉它是算术不是解释）。
    """
    trigger = _detect_trigger(seg)
    due_days, error = _detect_due_days(seg, trigger)
    if error is not None:
        return None, error

    match = _PERCENT.search(seg)
    if match is not None:
        # 显式补回 "%" 再走 numbers.parse_decimal：比例的十进制解析全项目一个入口，
        # 且**不能**走 parse_percent —— 那条路对 "0.5%" 与裸 "0.5" 无法区分。
        parsed = parse_decimal(f"{match.group(1)}%")
        value = parsed.value
        if value is None or value < 0 or value > 1:
            return None, f"付款比例 {match.group(0)!r} 不在 0–100% 之间，无法解析"
        if value == 0:
            return None, None
        return _Installment(percent=value, trigger=trigger, due_days=due_days), None

    if _REMAINDER.search(seg):
        return _Installment(percent=None, trigger=trigger, due_days=due_days), None

    return None, None


def _degraded(raw: str | None, installments: list[_Installment], warning: str) -> PaymentTermsParse:
    """降级但**保留已解析的部分**（SPEC §7：保留原文并标记待确认）。"""
    first = installments[0] if installments else None
    second = installments[1] if len(installments) > 1 else None
    days = {i.due_days for i in installments if i.due_days is not None}
    return PaymentTermsParse(
        raw=raw,
        deposit_percent=first.percent if first is not None else None,
        balance_percent=second.percent if second is not None else None,
        deposit_trigger=first.trigger if first is not None else None,
        balance_trigger=second.trigger if second is not None else None,
        due_days=days.pop() if len(days) == 1 else None,
        structured=False,
        warning=warning,
    )


# --------------------------------------------------------------- 入口


def parse_payment_terms(raw: object) -> PaymentTermsParse:
    """解析付款条件。**无法可靠结构化时返回 structured=False 并保留原文。**

    >>> p = parse_payment_terms("30% T/T in advance, 70% before shipment")
    >>> p.structured, p.deposit_percent, p.balance_trigger
    (True, Decimal('0.3'), <PaymentTrigger.SHIPMENT: 'SHIPMENT'>)
    >>> parse_payment_terms("T/T 30 days after B/L date").due_days
    30
    >>> parse_payment_terms("30% deposit, 60% before shipment").structured
    False
    """
    if raw is None:
        return PaymentTermsParse(raw=None)

    original = raw if isinstance(raw, str) else str(raw)
    # 换行先变成子句分隔符，再折叠空白（见 _LINE_BREAK 注释）。
    text = collapse_ws(nfkc(_LINE_BREAK.sub(";", original))).lower()
    if not text:
        # 空 = 未提取到，由比较层判 MISSING_VALUE，不是解析失败，不给 warning。
        return PaymentTermsParse(raw=original)

    installments: list[_Installment] = []
    for seg in _segments(text):
        installment, error = _read_segment(seg)
        if error is not None:
            # 出错也**保留已读到的部分**，与其余降级路径一致。
            return _degraded(original, installments, error)
        if installment is not None:
            installments.append(installment)

    if not installments:
        if _PERCENT.search(text) is not None:
            # 原文明明写了比例，却一笔有效付款都没读出来（例如全是 0%）。
            # 此时兜底成「100% 一笔付清」是彻头彻尾的编造。
            return PaymentTermsParse(
                raw=original, warning="原文写有付款比例，但未能解析出任何一笔有效付款"
            )
        if _UNCONVERTED_SPLIT.search(text) is not None:
            # 分数（1/3）或定额（USD 3000）分账：读得到「有几笔」，读不到「各几成」。
            return PaymentTermsParse(
                raw=original,
                warning="识别到未换算成比例的金额或分数表述，无法确定各笔付款比例",
            )
        # 没写比例 = 一笔付清（100%）。但必须至少认出节点或天数，
        # 否则就是彻底认不出，绝不假装 100%。
        trigger = _detect_trigger(text)
        due_days, error = _detect_due_days(text, trigger)
        if error is not None:
            return PaymentTermsParse(raw=original, warning=error)
        if trigger is None and due_days is None:
            return PaymentTermsParse(
                raw=original, warning="无法结构化付款条件，已保留原文待人工确认"
            )
        installments = [_Installment(percent=Decimal(1), trigger=trigger, due_days=due_days)]

    if len(installments) > _MAX_INSTALLMENTS:
        return _degraded(
            original,
            installments,
            f"识别到 {len(installments)} 笔付款，"
            f"MVP-0 只能结构化两笔（定金 + 尾款），无法可靠结构化",
        )

    # 「余款」按 100% - 已知比例补齐。两处「余款」则无从分配。
    blanks = [index for index, i in enumerate(installments) if i.percent is None]
    if len(blanks) > 1:
        return _degraded(original, installments, "识别到多处「余款」，无法确定各自比例")
    if blanks and len(installments) == 1:
        # 全文只读到「余款 / balance」一笔：另一笔（定额定金、分数、写在别处的比例）
        # 没读到。把余款当成 100% 是猜测——「余款」这个词本身就意味着还有别的付款。
        return _degraded(
            original,
            [],
            "只识别到「余款」一笔，另一笔付款未能识别，无法确定余款比例",
        )
    if blanks:
        known = sum((i.percent for i in installments if i.percent is not None), Decimal(0))
        remainder = Decimal(1) - known
        if remainder <= 0:
            return _degraded(
                original,
                installments,
                f"已知比例合计 {_fmt_percent(known)}%，「余款」比例无法确定",
            )
        installments[blanks[0]] = replace(installments[blanks[0]], percent=remainder)

    percents = [i.percent for i in installments if i.percent is not None]
    total = sum(percents, Decimal(0))
    if total != Decimal(1):
        return _degraded(original, installments, f"比例合计 {_fmt_percent(total)}%，无法可靠结构化")

    # 单笔付清：第二个槽位是明确的 0，不是「不知道」。
    deposit = installments[0]
    balance = installments[1] if len(installments) > 1 else None
    balance_percent = balance.percent if balance is not None else Decimal(0)
    balance_trigger = balance.trigger if balance is not None else None

    days = {i.due_days for i in installments if i.due_days is not None}
    if len(days) > 1:
        return _degraded(
            original,
            installments,
            "识别到多个付款天数（" + "/".join(str(d) for d in sorted(days)) + "），"
            "单个 due_days 字段无法表达",
        )

    warning: str | None = None
    if deposit.trigger is None or (balance is not None and balance.trigger is None):
        # 比例可比（SPEC §9.4 payment_terms 比的就是比例），但节点没读全，
        # 明确告知而不是沉默。
        warning = "比例已结构化，但未能识别全部付款节点，需人工确认"

    return PaymentTermsParse(
        raw=original,
        deposit_percent=deposit.percent,
        balance_percent=balance_percent,
        deposit_trigger=deposit.trigger,
        balance_trigger=balance_trigger,
        due_days=days.pop() if days else None,
        structured=True,
        warning=warning,
    )


# --------------------------------------------------------------- 比较


def payment_comparable(a: PaymentTermsParse, b: PaymentTermsParse) -> bool:
    """两份付款条件能不能按结构化字段比较。

    任一侧 `structured=False` -> False，调用方据此产出 **REVIEW**（人工确认），
    **不是 CRITICAL**。

    本函数只回答「能不能比」，**不回答「相不相等」**——两者合并成一个返回值，
    调用方就再也分不出 REVIEW 与 CRITICAL 了。相等判定见 `payment_fields_equal()`。

    >>> payment_comparable(parse_payment_terms("N/A"), parse_payment_terms("L/C at sight"))
    False
    """
    return a.structured and b.structured


def _triggers_conflict(left: PaymentTrigger | None, right: PaymentTrigger | None) -> bool:
    """两个付款节点是否**确定**不同。

    一边有一边无 -> 不算冲突（「没读出来」不是「不一样」，判成不一样就是假警报）；
    两边都读出来且不同 -> 是真差异。判据与 `incoterm_equivalent` 处理 version
    的那条规则同源。
    """
    return left is not None and right is not None and left is not right


def payment_fields_equal(a: PaymentTermsParse, b: PaymentTermsParse) -> bool:
    """比较 deposit_percent / balance_percent / due_days **与两个付款节点**。

    **调用前必须先过 `payment_comparable()`**：不可比时本函数无意义。

    - 一侧有 due_days 另一侧没有视为不等——「见提单副本付款」与「提单日后 30 天付款」
      比例相同但不是同一个条款，说成相等是危险的沉默。
    - 节点两边都识别出来且不同（`70% before shipment` 与 `70% after delivery`）
      同样视为不等：比例一致但钱出门的时点不同，这正是 PO↔PI 必须报出来的那类差异。
      只比比例会把它静默吞掉。
    - 节点只有一侧识别出来时**忽略节点**，避免把「机器没读懂」报成「两份文件不一致」
      （这一侧的 `warning` 已经写明未识别全部节点，由调用方按 REVIEW 呈现）。

    已知边界：两个槽位**按原文出现顺序**填充，因此 `70% 发货前, 30% 定金` 与
    `30% 定金, 70% 发货前` 会判为不等（误报，可人工消解），而不是靠重排猜测原意。

    >>> a = parse_payment_terms("30% deposit, balance against B/L copy")
    >>> b = parse_payment_terms("T/T 30% 定金，70% 见提单副本付款")
    >>> payment_comparable(a, b) and payment_fields_equal(a, b)
    True
    >>> x = parse_payment_terms("30% deposit, 70% before shipment")
    >>> y = parse_payment_terms("30% deposit, 70% after delivery")
    >>> payment_fields_equal(x, y)
    False
    """
    if not payment_comparable(a, b):
        return False
    if _triggers_conflict(a.deposit_trigger, b.deposit_trigger):
        return False
    if _triggers_conflict(a.balance_trigger, b.balance_trigger):
        return False
    return (
        a.deposit_percent == b.deposit_percent
        and a.balance_percent == b.balance_percent
        and a.due_days == b.due_days
    )
