"""日期归一化单测。SPEC §7（转 ISO 保留原文、日月歧义不得擅自确定）、§9.2。

本文件的核心断言不是「能解析多少格式」，而是**读不准时确实拒绝了**：
歧义日期不给 iso、两位年份不猜世纪、越界日期不回退成另一种书写约定、
Excel 序列号不擅自换算、垃圾输入不抛异常也不蒙一个值。

三条铁律各配一条机械断言，不靠 code review：
  - 无 float / 无二进制浮点        -> test_module_has_no_float
  - 无法判断时显式失败              -> test_every_result_obeys_the_invariants
  - 重复执行结果稳定                -> test_raw_never_contains_a_memory_address 等
"""

from __future__ import annotations

import ast
import datetime as dt
import doctest
from decimal import Decimal
from pathlib import Path

import pytest

from app.normalization import dates as dates_module
from app.normalization.dates import DateParse, dates_comparable, parse_date

# --------------------------------------------------------------- 无歧义格式


@pytest.mark.parametrize(
    "raw",
    [
        # 年在前，无歧义
        "2026-09-15",
        "2026/9/15",
        "2026.09.15",
        "2026 - 09 - 15",
        # 月份为英文名，无歧义
        "15-Sep-2026",
        "15 Sep 2026",
        "15 Sept. 2026",
        "Sep 15, 2026",
        "September 15, 2026",
        "15 September 2026",
        "SEP-15-2026",
        # 中文
        "2026年9月15日",
        "2026 年 09 月 15 号",
        "2026年9月15",  # 尾部「日/号」缺失，真实单据里常见
        # 日/月在前但只有一种合法解读（15 不可能是月份）
        "15/09/2026",
        "09/15/2026",
        "15-09-2026",
    ],
)
def test_unambiguous_formats_parse_to_iso(raw: str) -> None:
    parsed = parse_date(raw)
    assert parsed.iso == "2026-09-15"
    assert parsed.ambiguous is False
    assert parsed.candidates == ()
    assert parsed.ok is True
    assert parsed.raw == raw  # SPEC §7：转 ISO 但**保留原文**


# --------------------------------------------------------------- 脏输入：全角 / 空白 / 混排


@pytest.mark.parametrize(
    "raw",
    [
        "２０２６年９月１５日",  # 全角数字
        "２０２６－０９－１５",  # 全角数字 + 全角连字符
        "２０２６／０９／１５",  # 全角斜杠
        "2026．09．15",  # 全角句点
        "１５／０９／２０２６",  # 全角、日在前
        "１５ Ｓｅｐ ２０２６",  # 全角字母月份名
    ],
)
def test_fullwidth_input_is_normalized_by_nfkc(raw: str) -> None:
    """中文单据里全角数字/标点极常见，必须走 NFKC（SPEC §7）。"""
    parsed = parse_date(raw)
    assert parsed.iso == "2026-09-15"
    assert parsed.raw == raw  # 归一化不得覆盖原文


@pytest.mark.parametrize(
    "raw",
    [
        "  2026-09-15  ",  # 首尾空格
        "\t2026-09-15\n",  # 制表符 / 换行（复制粘贴产物）
        "2026-09-15　",  # 尾部全角空格
        " 2026-09-15",  # 不换行空格（网页/邮件粘贴产物）
        "15  Sep  2026",  # 中间多余空格
        "2026 年  09 月  15 日",
    ],
)
def test_excess_whitespace_is_tolerated(raw: str) -> None:
    parsed = parse_date(raw)
    assert parsed.iso == "2026-09-15"
    assert parsed.raw == raw


@pytest.mark.parametrize(
    "raw",
    [
        "交货日期：2026-09-15",
        "Delivery 2026-09-15",
        "2026-09-15 交货",
        "2026年Sep15日",  # 中英混排，无法确定读法
        "2026-09-15 (星期二)",
        "15/09/2026 或 09/15/2026",  # 单元格里塞了两个日期
        "2026-09-15~2026-09-30",  # 区间不是单个日期
        "2026-09-15T00:00:00",  # 带时间的字符串（不是 datetime 对象）
    ],
)
def test_mixed_language_or_decorated_text_fails_explicitly(raw: str) -> None:
    """中英混排 / 带前后缀 / 区间：读不准就显式失败，**不做子串抠日期**。

    从一句话里抠出第一个像日期的片段，等于替用户决定这句话在说什么
    （SPEC §20「无法判断时显式失败」）。
    """
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.warning is not None
    assert parsed.raw == raw


# --------------------------------------------------------------- 日月歧义


@pytest.mark.parametrize("raw", ["08/09/2026", "08-09-2026", "08.09.2026", "０８／０９／２０２６"])
def test_ambiguous_day_month_is_refused(raw: str) -> None:
    """两个数都 <=12 -> 不得擅自确定，只能给出两个排序候选。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ok is False
    assert parsed.ambiguous is True
    assert parsed.candidates == ("2026-08-09", "2026-09-08")
    assert parsed.warning is not None
    assert "日月顺序歧义，未擅自确定" in parsed.warning
    assert parsed.raw == raw


def test_candidates_are_sorted_ascending() -> None:
    """candidates 必须排序，否则重复执行结果不稳定（Gate-0 第 15 条）。"""
    parsed = parse_date("11/02/2026")
    assert parsed.candidates == ("2026-02-11", "2026-11-02")
    assert list(parsed.candidates) == sorted(parsed.candidates)


def test_parse_is_deterministic() -> None:
    assert parse_date("08/09/2026") == parse_date("08/09/2026")


def test_same_number_both_sides_is_not_ambiguous() -> None:
    """09/09/2026 两种解读重合，结果唯一，不该报歧义。"""
    parsed = parse_date("09/09/2026")
    assert parsed.iso == "2026-09-09"
    assert parsed.ambiguous is False
    assert parsed.candidates == ()


def test_day_gt_12_on_left_resolves() -> None:
    parsed = parse_date("31/12/2026")
    assert (parsed.iso, parsed.ambiguous) == ("2026-12-31", False)


def test_ambiguous_result_can_never_carry_an_iso() -> None:
    """结构性保证：DateParse 自身拒绝「又报歧义又给值」。"""
    with pytest.raises(ValueError, match="iso 必须为 None"):
        DateParse(raw="08/09/2026", iso="2026-08-09", ambiguous=True, candidates=("2026-08-09",))
    with pytest.raises(ValueError, match="至少两个候选"):
        DateParse(raw="08/09/2026", iso=None, ambiguous=True, candidates=("2026-08-09",))
    with pytest.raises(ValueError, match="升序排序"):
        DateParse(
            raw="08/09/2026",
            iso=None,
            ambiguous=True,
            candidates=("2026-09-08", "2026-08-09"),
        )
    with pytest.raises(ValueError, match="不得携带 candidates"):
        DateParse(raw="x", iso="2026-08-09", candidates=("2026-08-09",))


# --------------------------------------------------------------- 越界不得回退 / 不得夹取


@pytest.mark.parametrize(
    "raw",
    [
        "2026-15-09",  # 月份位越界
        "2026-13-05",  # 月份位越界，且 年-日-月 恰好合法
        "2026/13/5",
        "2026-02-30",  # 闰月越界，不得夹取成 02-28
        "2026-04-31",
        "2026-00-15",  # 月份为 0
        "2026-09-00",  # 日为 0
        "2026-13-45",  # 两种解读都越界
    ],
)
def test_year_first_out_of_range_fails_explicitly(raw: str) -> None:
    """年在前但月/日越界 -> 显式失败，**不回退到 年-日-月，不夹取到月末**。

    回退看着像「唯一合法解读」，实则是为救坏数据发明一种真实单据里不存在的
    书写约定（SPEC §20「无法判断时显式失败」）。
    """
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ok is False
    assert parsed.ambiguous is False
    assert parsed.candidates == ()
    assert parsed.warning is not None
    assert parsed.raw == raw


def test_corrupt_year_first_date_never_silently_equals_a_clean_one() -> None:
    """回归：`2026-13-05` 曾被读成 2026-05-13，与干净的 `2026-05-13` 判等。

    那是**危险的沉默**——两份文件交期不同却一条差异都不报（SPEC §9.1：
    INCOMPARABLE 绝不能坍缩成 EQUAL）。此处锁死：坏数据只能进 REVIEW 通道。
    """
    corrupt = parse_date("2026-13-05")
    clean = parse_date("2026-05-13")
    assert clean.iso == "2026-05-13"
    assert corrupt.iso is None
    assert dates_comparable(corrupt, clean) is False


@pytest.mark.parametrize("raw", ["31/02/2026", "30/02/2026", "31/04/2026", "29/02/2025"])
def test_day_month_out_of_range_is_not_clamped(raw: str) -> None:
    """两种读法都越界 -> 失败。不得夹取成月末，也不得挑一个「接近的」。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.warning is not None


# --------------------------------------------------------------- 分隔符混用


@pytest.mark.parametrize("raw", ["2026-09/15", "2026.09-15", "15/09-2026", "15-09.2026"])
def test_mixed_separators_are_bad_data(raw: str) -> None:
    """分隔符两侧不一致 = 坏数据，不做纠错（正则反向引用锁死）。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.warning is not None


# --------------------------------------------------------------- 两位年份


@pytest.mark.parametrize(
    "raw",
    [
        "08/09/26",
        "15-09-26",
        "1-2-26",
        "15-Sep-26",
        "Sep 15, 26",
        "26年9月15日",
        "２６年９月１５日",
    ],
)
def test_two_digit_year_is_refused(raw: str) -> None:
    """两位年份一律拒绝，**不猜世纪**（差一位就是差 100 年的交期）。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.candidates == ()
    assert parsed.warning is not None
    assert "世纪" in parsed.warning


# --------------------------------------------------------------- datetime 直连


def test_datetime_object_is_unambiguous() -> None:
    # openpyxl 的日期单元格给的就是朴素 datetime（无时区）。
    parsed = parse_date(dt.datetime(2026, 9, 15, 0, 0, 0))
    assert parsed.iso == "2026-09-15"
    assert parsed.ambiguous is False
    assert parsed.warning is None
    assert parsed.raw == "2026-09-15T00:00:00"


def test_datetime_with_time_component_warns_but_resolves() -> None:
    parsed = parse_date(dt.datetime(2026, 9, 15, 13, 30, 0))
    assert parsed.iso == "2026-09-15"
    assert parsed.warning is not None


def test_tz_aware_datetime_is_stable() -> None:
    """带时区的 datetime 也必须给确定结果，raw 里不得出现随环境变化的内容。"""
    parsed = parse_date(dt.datetime(2026, 9, 15, tzinfo=dt.UTC))
    assert parsed.iso == "2026-09-15"
    assert parsed.raw == "2026-09-15T00:00:00+00:00"


def test_date_object_is_unambiguous() -> None:
    parsed = parse_date(dt.date(2026, 9, 15))
    assert parsed.iso == "2026-09-15"
    assert parsed.ambiguous is False
    assert parsed.raw == "2026-09-15"


# --------------------------------------------------------------- 数字型单元格


@pytest.mark.parametrize("raw", [46281, 46281.0, Decimal("46281"), 0, -1, 123])
def test_numeric_cell_is_refused_without_serial_conversion(raw: object) -> None:
    """数字型单元格（未按日期格式化的 Excel 序列号）**不擅自换算**。

    46281 在 1900 系统里正好是 2026-09-15，在 1904 系统里差约 4 年；
    工作簿用哪套 epoch 本函数看不到，换算就是猜。
    """
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.warning is not None
    assert parsed.raw is not None  # 原文仍要保留（SPEC §7 四元组）


def test_excel_serial_number_does_not_become_a_date() -> None:
    """点名锁死最危险的一种「聪明」：把 46281 变成 2026-09-15。"""
    assert parse_date(46281).iso is None
    assert dates_comparable(parse_date(46281), parse_date("2026-09-15")) is False


# --------------------------------------------------------------- 拒绝与容错


@pytest.mark.parametrize(
    "raw",
    [
        "n/a",
        "N/A",
        "TBD",
        "见合同",
        "待定",
        "ASAP after deposit",
        "30 days after deposit",  # 相对交期条款，走 delivery_terms 不走日期
        "32/13/2026",
        "Foo 15, 2026",  # 月份名不认识
        "20260915",  # 紧凑格式，无分隔符，不猜
        "2026",  # 只有年
        "9月15日",  # 缺年份
        "-",
        "--",
        True,
        False,
        object(),
        dt.time(9, 30),
        b"2026-09-15",  # bytes 单元格
        ["2026-09-15"],
    ],
)
def test_unparseable_input_fails_explicitly(raw: object) -> None:
    """无法解析 -> iso=None + warning，**不抛异常、不猜值**。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.candidates == ()
    assert parsed.warning is not None


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n", "　", " "])
def test_empty_cell_is_not_an_error(raw: object) -> None:
    """空单元格不是解析错误（缺失走 MISSING_VALUE 语义，SPEC §9.8）。"""
    parsed = parse_date(raw)
    assert parsed.iso is None
    assert parsed.ambiguous is False
    assert parsed.warning is None


class _ExplodingCell:
    """__str__ / __repr__ 都抛异常的对象。第三方库的懒加载单元格可能长这样。"""

    def __str__(self) -> str:  # pragma: no cover - 被调用即说明实现有问题
        raise RuntimeError("boom")

    __repr__ = __str__


def test_object_with_exploding_str_does_not_break_parsing() -> None:
    """契约是「任何输入都不抛异常」。对未知对象调用 str() 会把这条契约击穿。"""
    parsed = parse_date(_ExplodingCell())
    assert parsed.iso is None
    assert parsed.warning is not None
    assert "_ExplodingCell" in parsed.warning


# --------------------------------------------------------------- 确定性（Gate-0 第 15 条）

#: 覆盖全部结局分支的脏输入语料。属性断言对每一条都成立。
_DIRTY_CORPUS: tuple[object, ...] = (
    None,
    "",
    "   ",
    "　",
    "2026-09-15",
    "  2026-09-15  ",
    "２０２６年９月１５日",
    "15 Sept. 2026",
    "Sep 15, 2026",
    "15/09/2026",
    "09/15/2026",
    "08/09/2026",
    "11/02/2026",
    "09/09/2026",
    "2026-13-05",
    "2026-02-30",
    "2026-09/15",
    "08/09/26",
    "15-Sep-26",
    "26年9月15日",
    "交货日期：2026-09-15",
    "TBD",
    "见合同",
    "20260915",
    46281,
    46281.0,
    Decimal("46281"),
    True,
    dt.date(2026, 9, 15),
    dt.datetime(2026, 9, 15, 13, 30),
    dt.time(9, 30),
    object(),
    b"2026-09-15",
)


def test_every_result_obeys_the_invariants() -> None:
    """一条断言守住全部铁律，新增分支想违反也绕不过去。"""
    for index, raw in enumerate(_DIRTY_CORPUS):
        parsed = parse_date(raw)
        where = f"corpus[{index}]"

        # 显式失败：没给出 iso 就必须说明原因（空单元格除外，那是缺失不是错误）
        is_empty = raw is None or (isinstance(raw, str) and not raw.strip())
        if parsed.iso is None and not is_empty:
            assert parsed.warning is not None, where

        # 歧义与确定互斥，候选必须排序且至少两个
        if parsed.ambiguous:
            assert parsed.iso is None, where
            assert len(parsed.candidates) >= 2, where
            assert list(parsed.candidates) == sorted(parsed.candidates), where
        else:
            assert parsed.candidates == (), where

        # ok 恒等于「拿到唯一 ISO」
        assert parsed.ok is (parsed.iso is not None), where

        # 保留原文：字符串输入原样回传，一个字符都不许改（SPEC §7）
        if isinstance(raw, str):
            assert parsed.raw == raw, where

        # 重复执行逐字节一致
        assert parse_date(raw) == parsed, where


def test_raw_never_contains_a_memory_address() -> None:
    """回归：未知对象曾走 str(raw)，raw 里带 `<object object at 0x...>`。

    该值会流进 extracted_field.raw_value 与 values_digest，连跑三次指纹不一致，
    直接违反 Gate-0 第 15 条。
    """
    for raw in _DIRTY_CORPUS:
        parsed = parse_date(raw)
        for text in (parsed.raw, parsed.warning):
            assert text is None or "0x" not in text
            assert text is None or " at 0" not in text

    assert parse_date(object()).raw is None
    assert parse_date(object()) == parse_date(object())


# --------------------------------------------------------------- dates_comparable

_OK = parse_date("2026-09-15")
_OK2 = parse_date("15-Sep-2026")
_AMBIGUOUS = parse_date("08/09/2026")
_UNPARSED = parse_date("TBD")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (_OK, _OK2, True),  # 两侧确定 -> 可比较
        (_OK, _AMBIGUOUS, False),  # 一侧歧义
        (_AMBIGUOUS, _OK, False),  # 歧义在另一侧，对称
        (_OK, _UNPARSED, False),  # 一侧未解析
        (_UNPARSED, _OK, False),
        (_AMBIGUOUS, _UNPARSED, False),  # 两侧都不可用
        (_UNPARSED, _UNPARSED, False),
        (_AMBIGUOUS, _AMBIGUOUS, False),
    ],
)
def test_dates_comparable(left: DateParse, right: DateParse, expected: bool) -> None:
    assert dates_comparable(left, right) is expected


def test_dates_comparable_is_symmetric_over_the_corpus() -> None:
    """不对称会让差异集合依赖角色迭代顺序（SPEC §9.3 的同类陷阱）。"""
    parsed = [parse_date(raw) for raw in _DIRTY_CORPUS]
    for left in parsed:
        for right in parsed:
            assert dates_comparable(left, right) is dates_comparable(right, left)


def test_ambiguous_pair_with_identical_raw_is_still_incomparable() -> None:
    """两份文件写的都是 08/09/2026 也不能判等——谁知道双方指的是不是同一天。

    调用方据此产出 REVIEW，而不是 EQUAL（危险的沉默）或 VALUE_CONFLICT（假警报）。
    """
    assert dates_comparable(parse_date("08/09/2026"), parse_date("08/09/2026")) is False


def test_candidate_overlap_does_not_make_dates_comparable() -> None:
    """`08/09/2026` 的候选之一等于 `2026-08-09`，仍然不可比较。

    「候选里有一个对得上就算一致」是最诱人的一步之遥，等于替用户选读法。
    """
    ambiguous = parse_date("08/09/2026")
    assert "2026-08-09" in ambiguous.candidates
    assert dates_comparable(ambiguous, parse_date("2026-08-09")) is False


# --------------------------------------------------------------- 铁律：无二进制浮点


def test_module_has_no_float() -> None:
    """SPEC §7 / CLAUDE.md：域内模块禁止 `float(` 与浮点字面量，AST 扫描强制。"""
    source = Path(dates_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", f"第 {node.lineno} 行调用了 float()"
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), f"第 {node.lineno} 行有浮点字面量"


# --------------------------------------------------------------- 文档示例不腐化


def test_module_doctests_pass() -> None:
    result = doctest.testmod(dates_module, verbose=False)
    assert result.failed == 0
    assert result.attempted > 0
