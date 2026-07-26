"""付款条件归一化单测。SPEC §7、§9.4。

重点不是「能解析多少」，而是「读不懂时有没有老实降级」——
`structured=False` 的每一条都是产品核心原则的回归测试。

三条机械约束在本文件里各有一条测试兜底：

  - **禁止二进制浮点**（SPEC §7、硬约束 #7）-> `test_module_ast_has_no_float`
  - **重复执行结果稳定**（Gate-0 第 15 条）-> `test_output_stable_across_hash_seeds`
  - **无法判断时显式失败**（SPEC §20）-> `test_every_unstructured_result_carries_warning`
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.normalization import payment as payment_module
from app.normalization.payment import (
    PaymentTermsParse,
    PaymentTrigger,
    parse_payment_terms,
    payment_comparable,
    payment_fields_equal,
)

# --------------------------------------------------------------- 真实写法


@pytest.mark.parametrize(
    ("raw", "deposit", "balance", "deposit_trigger", "balance_trigger", "due_days"),
    [
        (
            "30% T/T in advance, 70% before shipment",
            Decimal("0.3"),
            Decimal("0.7"),
            PaymentTrigger.ORDER,
            PaymentTrigger.SHIPMENT,
            None,
        ),
        (
            "30% deposit, balance against B/L copy",
            Decimal("0.3"),
            Decimal("0.7"),
            PaymentTrigger.ORDER,
            PaymentTrigger.BL_COPY,
            None,
        ),
        (
            "T/T 30% 定金，70% 见提单副本付款",
            Decimal("0.3"),
            Decimal("0.7"),
            PaymentTrigger.ORDER,
            PaymentTrigger.BL_COPY,
            None,
        ),
        (
            "100% T/T before shipment",
            Decimal("1"),
            Decimal("0"),
            PaymentTrigger.SHIPMENT,
            None,
            None,
        ),
        ("L/C at sight", Decimal("1"), Decimal("0"), PaymentTrigger.SIGHT, None, 0),
        (
            "T/T 30 days after B/L date",
            Decimal("1"),
            Decimal("0"),
            PaymentTrigger.BL_DATE,
            None,
            30,
        ),
        (
            "30% 预付，余款发货前付清",
            Decimal("0.3"),
            Decimal("0.7"),
            PaymentTrigger.ORDER,
            PaymentTrigger.SHIPMENT,
            None,
        ),
    ],
)
def test_real_world_terms_are_structured(
    raw: str,
    deposit: Decimal,
    balance: Decimal,
    deposit_trigger: PaymentTrigger,
    balance_trigger: PaymentTrigger | None,
    due_days: int | None,
) -> None:
    """任务给定的七条真实写法必须全部结构化成功。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is True
    assert parsed.raw == raw
    assert parsed.deposit_percent == deposit
    assert parsed.balance_percent == balance
    assert parsed.deposit_trigger is deposit_trigger
    assert parsed.balance_trigger is balance_trigger
    assert parsed.due_days == due_days


def test_percent_is_decimal_never_float() -> None:
    """比例内部统一存小数，且必须是 Decimal（禁止二进制浮点）。"""
    parsed = parse_payment_terms("30% deposit, 70% before shipment")
    assert isinstance(parsed.deposit_percent, Decimal)
    assert isinstance(parsed.balance_percent, Decimal)
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.total_percent == Decimal(1)


def test_trigger_is_str_at_runtime() -> None:
    """PaymentTrigger 是 StrEnum：落库/JSON 直接当字符串用。"""
    parsed = parse_payment_terms("100% T/T before shipment")
    assert parsed.deposit_trigger == "SHIPMENT"
    assert isinstance(parsed.deposit_trigger, str)


def test_sight_lc_sets_due_days_zero() -> None:
    """即期信用证：due_days = 0 是定义，不是猜测。"""
    parsed = parse_payment_terms("L/C at sight")
    assert parsed.structured is True
    assert parsed.due_days == 0
    assert parsed.deposit_trigger is PaymentTrigger.SIGHT


def test_no_punctuation_between_installments() -> None:
    """没有逗号分隔时按百分比切分，第二个节点不得污染第一笔。"""
    parsed = parse_payment_terms("30% T/T in advance 70% before shipment")
    assert parsed.structured is True
    assert parsed.deposit_trigger is PaymentTrigger.ORDER
    assert parsed.balance_trigger is PaymentTrigger.SHIPMENT


def test_full_width_percent_and_comma() -> None:
    """全角标点（中文单据极常见）走 NFKC 后与半角等价。"""
    parsed = parse_payment_terms("３０％定金，７０％发货前")
    assert parsed.structured is True
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.balance_percent == Decimal("0.7")


# --------------------------------------------------------------- 中英文混合


def test_mixed_chinese_english_with_due_days() -> None:
    parsed = parse_payment_terms("T/T 30% deposit，余款见提单副本后 7 天内付清")
    assert parsed.structured is True
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.deposit_trigger is PaymentTrigger.ORDER
    assert parsed.balance_percent == Decimal("0.7")
    assert parsed.balance_trigger is PaymentTrigger.BL_COPY
    assert parsed.due_days == 7


def test_mixed_chinese_english_sample_approval() -> None:
    parsed = parse_payment_terms("50% 确认样后支付, 50% before shipment")
    assert parsed.structured is True
    assert parsed.deposit_trigger is PaymentTrigger.SAMPLE_APPROVAL
    assert parsed.balance_trigger is PaymentTrigger.SHIPMENT
    assert parsed.deposit_percent == Decimal("0.5")


def test_deposit_event_trigger_differs_from_order() -> None:
    """「收到定金后 30 天」的节点是 DEPOSIT，不是 ORDER。"""
    parsed = parse_payment_terms("30% 预付，70% 收到定金后 30 天内付清")
    assert parsed.structured is True
    assert parsed.deposit_trigger is PaymentTrigger.ORDER
    assert parsed.balance_trigger is PaymentTrigger.DEPOSIT
    assert parsed.due_days == 30


def test_delivery_trigger() -> None:
    parsed = parse_payment_terms("30% deposit, 70% after delivery")
    assert parsed.balance_trigger is PaymentTrigger.DELIVERY


# --------------------------------------------------------------- 降级：比例不足 100%


@pytest.mark.parametrize(
    ("raw", "expected_in_warning"),
    [
        ("30% T/T in advance, 60% before shipment", "90"),
        ("30% deposit", "30"),
        ("50% deposit, 60% before shipment", "110"),
    ],
)
def test_percent_sum_not_100_degrades(raw: str, expected_in_warning: str) -> None:
    """比例合计不等于 100%：不报错、不猜，降级为待确认并保留已解析部分。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is False
    assert parsed.warning is not None
    assert "无法可靠结构化" in parsed.warning
    assert f"比例合计 {expected_in_warning}%" in parsed.warning
    assert parsed.raw == raw
    # 已解析的部分必须保留，供人工确认时参考
    assert parsed.deposit_percent is not None


def test_partial_result_kept_on_degrade() -> None:
    parsed = parse_payment_terms("30% T/T in advance, 60% before shipment")
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.balance_percent == Decimal("0.6")
    assert parsed.deposit_trigger is PaymentTrigger.ORDER
    assert parsed.balance_trigger is PaymentTrigger.SHIPMENT


def test_three_installments_degrade() -> None:
    """三笔付款：两个槽位表达不了，降级而不是丢掉第三笔。"""
    parsed = parse_payment_terms("30% deposit, 40% before shipment, 30% after delivery")
    assert parsed.structured is False
    assert parsed.warning is not None
    assert "3 笔付款" in parsed.warning
    assert parsed.deposit_percent == Decimal("0.3")


def test_conflicting_due_days_degrade() -> None:
    """两笔各有不同天数，单个 due_days 字段表达不了 -> 降级。"""
    parsed = parse_payment_terms("30% 30 days after order, 70% 60 days after B/L date")
    assert parsed.structured is False
    assert parsed.warning is not None
    assert "多个付款天数" in parsed.warning
    assert parsed.due_days is None


def test_remainder_without_room_degrades() -> None:
    """已知比例已达 100%，再出现「余款」= 读错了，降级。"""
    parsed = parse_payment_terms("100% T/T in advance, balance before shipment")
    assert parsed.structured is False
    assert parsed.warning is not None
    assert "余款" in parsed.warning


# --------------------------------------------------------------- 降级：完全无法解析


@pytest.mark.parametrize(
    "raw",
    [
        "Payment terms to be discussed",
        "TBD",
        "按双方另行约定执行",
        "T/T",
    ],
)
def test_unparseable_degrades_with_all_fields_none(raw: str) -> None:
    """完全认不出：全部结构字段 None，raw 原样保留，warning 说明。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is False
    assert parsed.raw == raw
    assert parsed.deposit_percent is None
    assert parsed.balance_percent is None
    assert parsed.deposit_trigger is None
    assert parsed.balance_trigger is None
    assert parsed.due_days is None
    assert parsed.warning == "无法结构化付款条件，已保留原文待人工确认"


def test_empty_is_missing_not_failure() -> None:
    """空值属于「未提取到」（MISSING_VALUE），不是解析失败，不给 warning。"""
    for raw in (None, "", "   "):
        parsed = parse_payment_terms(raw)
        assert parsed.structured is False
        assert parsed.warning is None
    assert parse_payment_terms(None).raw is None


def test_trigger_not_recognized_returns_none_not_a_guess() -> None:
    """节点识别不到就是 None，绝不硬塞最像的一个。"""
    parsed = parse_payment_terms("30% by cheque, 70% by cheque")
    assert parsed.deposit_trigger is None
    assert parsed.balance_trigger is None
    assert parsed.structured is True  # 比例可比
    assert parsed.warning is not None
    assert "未能识别全部付款节点" in parsed.warning


def test_non_string_input_is_preserved() -> None:
    """非字符串单元格（例如数字）不得炸，raw 转成字符串保留。"""
    parsed = parse_payment_terms(30)
    assert parsed.structured is False
    assert parsed.raw == "30"


# --------------------------------------------------------------- payment_comparable


def test_comparable_false_when_either_unstructured() -> None:
    """**任一 structured=False 即不可比** —— 调用方据此产出 REVIEW 而非 CRITICAL。"""
    ok = parse_payment_terms("30% deposit, 70% before shipment")
    bad = parse_payment_terms("Payment terms to be discussed")
    assert ok.structured is True
    assert bad.structured is False
    assert payment_comparable(ok, bad) is False
    assert payment_comparable(bad, ok) is False
    assert payment_comparable(bad, bad) is False


def test_comparable_false_for_partial_degrade() -> None:
    """降级但留有部分字段的一侧同样不可比，不得拿半截结果去判 CRITICAL。"""
    ok = parse_payment_terms("30% deposit, 70% before shipment")
    half = parse_payment_terms("30% deposit, 60% before shipment")
    assert half.deposit_percent == Decimal("0.3")
    assert payment_comparable(ok, half) is False
    assert payment_fields_equal(ok, half) is False


def test_comparable_true_when_both_structured() -> None:
    a = parse_payment_terms("30% T/T in advance, 70% before shipment")
    b = parse_payment_terms("30% 预付，余款发货前付清")
    assert payment_comparable(a, b) is True


def test_fields_equal_across_languages() -> None:
    """中英文写法不同、结构相同 -> 相等（不产生假差异）。"""
    a = parse_payment_terms("30% deposit, balance against B/L copy")
    b = parse_payment_terms("T/T 30% 定金，70% 见提单副本付款")
    assert payment_fields_equal(a, b) is True


def test_fields_not_equal_on_percent_difference() -> None:
    a = parse_payment_terms("30% deposit, 70% before shipment")
    b = parse_payment_terms("40% deposit, 60% before shipment")
    assert payment_comparable(a, b) is True
    assert payment_fields_equal(a, b) is False


def test_fields_not_equal_when_due_days_missing_on_one_side() -> None:
    """比例相同但一侧有账期：不是同一个条款，不得判相等（危险的沉默）。"""
    a = parse_payment_terms("100% T/T before shipment")
    b = parse_payment_terms("T/T 30 days after B/L date")
    assert a.deposit_percent == b.deposit_percent
    assert payment_comparable(a, b) is True
    assert payment_fields_equal(a, b) is False


def test_fields_equal_ignores_decimal_exponent() -> None:
    """Decimal("0.30") 与 Decimal("0.3") 是同一个比例。"""
    a = PaymentTermsParse(
        raw="a",
        deposit_percent=Decimal("0.30"),
        balance_percent=Decimal("0.70"),
        structured=True,
    )
    b = PaymentTermsParse(
        raw="b",
        deposit_percent=Decimal("0.3"),
        balance_percent=Decimal("0.7"),
        structured=True,
    )
    assert payment_fields_equal(a, b) is True


def test_fields_not_equal_when_triggers_differ() -> None:
    """比例一致但付款节点不同 = 钱在不同时点出门，**不得判等**（危险的沉默）。"""
    a = parse_payment_terms("30% deposit, 70% before shipment")
    b = parse_payment_terms("30% deposit, 70% after delivery")
    assert a.balance_trigger is PaymentTrigger.SHIPMENT
    assert b.balance_trigger is PaymentTrigger.DELIVERY
    assert payment_comparable(a, b) is True
    assert payment_fields_equal(a, b) is False


def test_fields_equal_when_trigger_unknown_on_one_side() -> None:
    """节点只有一侧识别出来时忽略节点：「没读出来」不是「不一样」，不得报 CRITICAL。"""
    a = parse_payment_terms("30% deposit, 70% before shipment")
    b = parse_payment_terms("30% deposit, 70% by cheque")
    assert b.balance_trigger is None
    assert b.warning is not None  # 未识别节点这件事必须可见
    assert payment_fields_equal(a, b) is True


# --------------------------------------------------------------- 脏输入：真实单据里的写法


def test_spelled_out_percent_is_recognized() -> None:
    """`30 percent` / `30 pct` / `30 per cent`：英文单据里与 `30%` 同样常见。

    识别不了会掉进「没写比例 = 一笔付清 100%」的兜底，把 30/70 说成 100%——
    比不识别危险得多。
    """
    for raw in (
        "30 percent deposit, 70 percent before shipment",
        "30 pct deposit, 70 pct before shipment",
        "30 per cent deposit, 70 per cent before shipment",
    ):
        parsed = parse_payment_terms(raw)
        assert parsed.structured is True, raw
        assert parsed.deposit_percent == Decimal("0.3"), raw
        assert parsed.balance_percent == Decimal("0.7"), raw
        assert parsed.balance_trigger is PaymentTrigger.SHIPMENT, raw


@pytest.mark.parametrize(
    "raw",
    [
        "30%　定金，70%　发货前",  # 全角空格
        "  30%\t T/T  in   advance ,\t 70%  before   shipment ",  # 多余空格与制表符
        "30% DEPOSIT, 70% BEFORE SHIPMENT",  # 全大写
        "30%deposit,70%before shipment",  # 无空格
        "付款方式：30%定金，70%发货前付清",  # 带中文标签前缀
        "30% deposit\n70% before shipment",  # 多行单元格
        "30% T/T in advance and 70% before shipment",  # and 连接
    ],
)
def test_dirty_but_readable_inputs_still_structure(raw: str) -> None:
    """脏排版不改变语义：全角、制表符、大小写、多行、标签前缀都必须照常结构化。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is True
    assert parsed.raw == raw  # 原文逐字保留（SPEC §7 四元组）
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.balance_percent == Decimal("0.7")


def test_multiline_cell_does_not_leak_trigger_across_lines() -> None:
    """多行单元格：第二行的节点**不得**污染第一笔付款。

    换行如果只被折叠成空格，「30% 定金 / 余款发货前付清」会粘成一句，
    deposit_trigger 被读成 SHIPMENT，余款还会整笔丢掉。
    """
    parsed = parse_payment_terms("30% 定金\r\n余款发货前付清")
    assert parsed.structured is True
    assert parsed.deposit_percent == Decimal("0.3")
    assert parsed.deposit_trigger is PaymentTrigger.ORDER
    assert parsed.balance_percent == Decimal("0.7")
    assert parsed.balance_trigger is PaymentTrigger.SHIPMENT


@pytest.mark.parametrize("raw", [30, 30.0, Decimal("0.3"), True])
def test_numeric_cells_do_not_crash_and_do_not_invent_terms(raw: object) -> None:
    """数字型 / 布尔型单元格：不得抛异常，也不得凭空造出一个付款条件。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is False
    assert parsed.raw == str(raw)
    assert parsed.deposit_percent is None
    assert parsed.warning is not None


def test_date_like_number_is_not_read_as_due_days() -> None:
    """「5月30日前」里的 30 是日期，不是账期天数（`日` 必须带 `内`/`后`）。"""
    parsed = parse_payment_terms("货款于5月30日前付清")
    assert parsed.due_days is None
    assert parsed.structured is False


def test_usance_lc_after_sight() -> None:
    """远期信用证 `90 days after sight`：节点 SIGHT + 账期 90 天。"""
    parsed = parse_payment_terms("L/C 90 days after sight")
    assert parsed.structured is True
    assert parsed.deposit_trigger is PaymentTrigger.SIGHT
    assert parsed.due_days == 90


# --------------------------------------------------------------- 降级：读不出比例就不许兜底


def test_fraction_split_degrades_instead_of_assuming_100_percent() -> None:
    """`1/3 deposit, 2/3 before shipment`：分数没换算成比例 -> 显式失败。

    回归：曾经静默产出「100% 发货前付清」，且 warning 为 None。
    """
    parsed = parse_payment_terms("1/3 deposit, 2/3 before shipment")
    assert parsed.structured is False
    assert parsed.deposit_percent is None
    assert parsed.warning is not None
    assert "无法确定" in parsed.warning


@pytest.mark.parametrize(
    "raw",
    [
        "USD 3000 deposit, balance before shipment",
        "3000USD deposit before shipment",
        "预付3000元，余款发货前付清",
        "$500 deposit before shipment",
    ],
)
def test_fixed_amount_deposit_degrades_instead_of_assuming_100_percent(raw: str) -> None:
    """定额定金：读得到「有几笔」，读不到「各几成」-> 显式失败。

    回归：曾经静默产出「100% 发货前付清」且 warning 为 None。
    """
    parsed = parse_payment_terms(raw)
    assert parsed.structured is False
    assert parsed.deposit_percent is None
    assert parsed.balance_percent is None
    assert parsed.warning is not None


@pytest.mark.parametrize("raw", ["余款发货前付清", "balance before shipment"])
def test_lone_remainder_degrades(raw: str) -> None:
    """全文只有「余款」一笔：这个词本身就意味着还有另一笔没读到，不得当成 100%。"""
    parsed = parse_payment_terms(raw)
    assert parsed.structured is False
    assert parsed.deposit_percent is None
    assert parsed.warning is not None
    assert "余款" in parsed.warning


def test_percent_over_100_is_explicit_failure() -> None:
    """`150%` 必须显式失败。

    回归：曾经被 `parse_percent` 二次除以 100，静默变成 1.5%，
    再以「比例合计 1.5%」的降级理由呈现给用户——那句 warning 本身就是假的。
    """
    parsed = parse_payment_terms("150% in advance")
    assert parsed.structured is False
    assert parsed.deposit_percent is None
    assert parsed.warning is not None
    assert "150%" in parsed.warning


def test_zero_percent_installment_is_not_a_payment() -> None:
    """`0% deposit, 100% before shipment` = 一笔 100% 发货前付清（0 不是一笔付款）。

    与 `100% T/T before shipment` 必须判等，否则同一条款换个写法就报 CRITICAL。
    """
    parsed = parse_payment_terms("0% deposit, 100% before shipment")
    assert parsed.structured is True
    assert parsed.deposit_percent == Decimal(1)
    assert parsed.deposit_trigger is PaymentTrigger.SHIPMENT
    assert payment_fields_equal(parsed, parse_payment_terms("100% T/T before shipment")) is True


def test_all_zero_percent_degrades() -> None:
    """原文写了比例却没有一笔有效付款：兜底成 100% 就是编造。"""
    parsed = parse_payment_terms("0% deposit")
    assert parsed.structured is False
    assert parsed.deposit_percent is None
    assert parsed.warning is not None


def test_two_due_days_in_one_clause_degrades() -> None:
    """一个子句里两个账期（`45 days ... or 30 days ...`）：取第一个就是替企业选边。"""
    parsed = parse_payment_terms("T/T 45 days after B/L date or 30 days after delivery")
    assert parsed.structured is False
    assert parsed.due_days is None
    assert parsed.warning is not None
    assert "多个账期天数" in parsed.warning


# --------------------------------------------------------------- 机械约束

#: 覆盖全部分支的脏输入语料。下面三条不变量在**每一条**上都必须成立。
DIRTY_CORPUS: tuple[object, ...] = (
    "30% T/T in advance, 70% before shipment",
    "30% deposit, balance against B/L copy",
    "T/T 30% 定金，70% 见提单副本付款",
    "100% T/T before shipment",
    "L/C at sight",
    "T/T 30 days after B/L date",
    "30% 预付，余款发货前付清",
    "30%　定金，70%　发货前",
    "  30%\t T/T  in   advance ,\t 70%  before   shipment ",
    "30% deposit\n70% before shipment",
    "30% 定金\r\n余款发货前付清",
    "30 percent deposit, 70 percent before shipment",
    "1/3 deposit, 2/3 before shipment",
    "USD 3000 deposit, balance before shipment",
    "3000USD deposit before shipment",
    "预付3000元，余款发货前付清",
    "余款发货前付清",
    "150% in advance",
    "0% deposit",
    "0% deposit, 100% before shipment",
    "T/T 45 days after B/L date or 30 days after delivery",
    "20% deposit, 30% before shipment, 50% after delivery",
    "30% 30 days after order, 70% 60 days after B/L date",
    "100% T/T in advance, balance before shipment",
    "Payment terms to be discussed",
    "按双方另行约定执行",
    "T/T",
    "货款于5月30日前付清",
    "L/C 90 days after sight",
    "第一笔 30%，第二笔 70%",
    30,
    30.0,
    Decimal("0.3"),
)


@pytest.mark.parametrize("raw", DIRTY_CORPUS)
def test_raw_is_always_preserved_verbatim(raw: object) -> None:
    """SPEC §7 四元组：原文逐字保留，任何分支都不许改写它。"""
    assert parse_payment_terms(raw).raw == (raw if isinstance(raw, str) else str(raw))


@pytest.mark.parametrize("raw", DIRTY_CORPUS)
def test_every_unstructured_result_carries_warning(raw: object) -> None:
    """**无法判断时显式失败**：非空输入只要没结构化成功，就必须留下人看得懂的理由。

    唯一允许 `structured=False` 且 `warning=None` 的情况是空值（MISSING_VALUE，
    由 `test_empty_is_missing_not_failure` 覆盖）。
    """
    parsed = parse_payment_terms(raw)
    if not parsed.structured:
        assert parsed.warning is not None


@pytest.mark.parametrize("raw", DIRTY_CORPUS)
def test_structured_result_always_totals_100_percent(raw: object) -> None:
    """结构化成功 = 两个槽位比例合计恒等于 100%，且都是 Decimal。"""
    parsed = parse_payment_terms(raw)
    if parsed.structured:
        assert isinstance(parsed.deposit_percent, Decimal)
        assert isinstance(parsed.balance_percent, Decimal)
        assert parsed.total_percent == Decimal(1)


def test_module_ast_has_no_float() -> None:
    """硬约束 #7：域内模块不得出现 `float(`，也不得出现二进制浮点字面量。

    AST 扫描而不是字符串查找——注释里的 `float(` 不算违规，
    `x = 0.3` 这种没写 `float(` 的浮点字面量才是最容易漏掉的那种。
    """
    source = Path(payment_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    float_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    ]
    float_literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert float_calls == []
    assert float_literals == []


_DETERMINISM_SCRIPT = """\
import sys

sys.stdout.reconfigure(encoding="utf-8")
from decimal import Decimal

from tests.test_normalization_payment import DIRTY_CORPUS
from app.normalization.payment import parse_payment_terms

for raw in DIRTY_CORPUS:
    print(repr(parse_payment_terms(raw)))
"""


def test_output_stable_across_hash_seeds(tmp_path: Path) -> None:
    """Gate-0 第 15 条：重复执行逐字节一致，且不依赖任何集合/字典迭代顺序。

    同进程内跑两遍证明不了这一点（哈希随机化在进程启动时固定），
    因此换三个 `PYTHONHASHSEED` 各起一个子进程比对输出。
    """
    script = tmp_path / "determinism.py"
    script.write_text(_DETERMINISM_SCRIPT, encoding="utf-8")
    root = Path(payment_module.__file__).resolve().parents[2]

    outputs: list[str] = []
    for seed in ("0", "1", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(root)}
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            encoding="utf-8",
            cwd=str(root),
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1] == outputs[2]
    assert "PaymentTermsParse" in outputs[0]
