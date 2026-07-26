"""日期归一化。SPEC §7（保留原第十节）、§9.2 四态 Verdict。

规格原文：**「转 ISO 但保留原文；`08/09/2026` 类日月歧义不得擅自确定」**。

因此本模块只有三种结局，**没有第四种「猜一个」**：

  1. 唯一确定  -> iso 有值、ambiguous=False
  2. 日月歧义  -> iso=None、ambiguous=True、candidates 给出全部候选 + warning
  3. 无法解析  -> iso=None、warning 说明原因（**不抛异常**，调用方不该被日期噎住）

调用方拿到 2/3 两种结局时应产出 REVIEW（Verdict.UNCERTAIN），**绝不能**产出
VALUE_CONFLICT——把「读不准」说成「不一致」是假警报，说成「一致」是危险的沉默
（SPEC §9.1、§9.2）。判据统一走 `dates_comparable()`。

`candidates` **必须排序**：不排序则重复执行的差异集合逐字节不一致，直接违反
Gate-0 第 15 条「连跑 3 次结果一致」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from app.normalization.text import collapse_ws, nfkc

# --------------------------------------------------------------- 词表与模式

#: 英文月份名（含常见缩写）。月份为英文名时日月顺序天然无歧义。
_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

#: 分隔符用反向引用 `\2` 锁死两侧一致。`2026-09/15` 这类混用是坏数据，
#: 按「无法判断时显式失败」处理，不做纠错。
_YMD = re.compile(r"^(\d{4})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{4})$")
_SHORT_YEAR = re.compile(r"^(\d{1,2})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{1,2})$")

_CJK = re.compile(r"^(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?$")

_D_MON_Y = re.compile(r"^(\d{1,2})\s*[-/.\s]\s*([A-Za-z]{3,9})\.?\s*[-/,.\s]\s*(\d{2,4})$")
_MON_D_Y = re.compile(r"^([A-Za-z]{3,9})\.?\s*[-/.\s]\s*(\d{1,2})\s*[-/,.\s]\s*(\d{2,4})$")

#: 两位年份一律拒绝。补世纪就是替用户猜一个可能差 100 年的交期。
_SHORT_YEAR_WARNING = "年份不足四位，无法确定世纪，未擅自补全"

_AMBIGUOUS_WARNING = "日月顺序歧义，未擅自确定"


@dataclass(frozen=True)
class DateParse:
    """日期解析结果（SPEC §7 四元组中的 raw / normalized 部分）。

    `iso` 为 None 时**必定**给出原因：`ambiguous=True` 表示日月顺序歧义
    （`candidates` 列出全部候选，已按 ISO 升序排序），否则 `warning` 说明为何读不出。
    """

    raw: str | None
    iso: str | None
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()
    warning: str | None = None

    def __post_init__(self) -> None:
        """结构性禁止「既给了 iso 又说歧义」这类自相矛盾的结果。

        这是「不得擅自确定」的机器保证：任何未来的分支只要想一边报歧义一边塞值，
        构造期就炸，不会静默流到比较引擎。只在编程错误时触发，用户输入触发不了。
        """
        if self.ambiguous:
            if self.iso is not None:
                raise ValueError("ambiguous=True 时 iso 必须为 None（不得擅自确定）")
            if len(self.candidates) < 2:
                raise ValueError("ambiguous=True 时 candidates 必须列出至少两个候选")
        elif self.candidates:
            raise ValueError("非歧义结果不得携带 candidates")
        if tuple(sorted(self.candidates)) != self.candidates:
            raise ValueError("candidates 必须升序排序（重复执行结果稳定）")

    @property
    def ok(self) -> bool:
        """是否得到唯一确定的 ISO 日期。"""
        return self.iso is not None


def _fail(raw: str | None, warning: str) -> DateParse:
    return DateParse(raw=raw, iso=None, warning=warning)


def _iso(year: int, month: int, day: int) -> str | None:
    """构造 ISO 日期串；月/日越界返回 None（**不修正、不夹取**）。"""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _four_digit_year(text: str) -> int | None:
    """只接受四位年份。两位年份不猜世纪，返回 None 交调用方显式失败。"""
    return int(text) if len(text) == 4 else None


def _from_numeric_dmy(raw: str, year: int, first: int, second: int) -> DateParse:
    """两个纯数字 + 四位年：列出**全部合法**解读，多于一种即判歧义。

    这条路径同时覆盖三种情况，不需要额外分支：
      15/09/2026 -> 15 不可能是月份，只剩一种合法解读 -> 2026-09-15
      09/15/2026 -> 同上 -> 2026-09-15
      08/09/2026 -> 两种都合法 -> 歧义，iso=None
    """
    seen: set[str] = set()
    for month, day in ((first, second), (second, first)):
        iso = _iso(year, month, day)
        if iso is not None:
            seen.add(iso)
    candidates = tuple(sorted(seen))

    if not candidates:
        return _fail(raw, f"月/日越界，无法解析为日期：{raw!r}")
    if len(candidates) == 1:
        # 含 08/08/2026 这类两种解读重合的情况：结果唯一，不算歧义。
        return DateParse(raw=raw, iso=candidates[0])
    return DateParse(
        raw=raw,
        iso=None,
        ambiguous=True,
        candidates=candidates,
        warning=f"{_AMBIGUOUS_WARNING}：{raw!r} 可能是 {candidates[0]} 或 {candidates[1]}",
    )


def _from_numeric_ymd(raw: str, year: int, month: int, day: int) -> DateParse:
    """年在前 -> 只按 ISO 的 年-月-日 读（SPEC §7）。

    月/日越界一律显式失败，**绝不回退到 年-日-月**。那个回退看着像「唯一合法解读」，
    实际是为了救坏数据而发明一种真实单据里不存在的书写约定，代价是**危险的沉默**：
    `2026-13-05` 会被读成 2026-05-13，与另一份文件里干净的 `2026-05-13` 判等，
    于是「两份文件交期一致」——一条差异都不报。误报只是吵，漏报是错
    （SPEC §9.1「绝不能坍缩成 EQUAL（危险的沉默）」、§20「无法判断时显式失败」）。

    中文「年月日」路径本来就不做这种回退，这里与它保持一致。
    """
    iso = _iso(year, month, day)
    if iso is None:
        return _fail(raw, f"年在前但月/日越界（月={month} 日={day}），无法解析为日期：{raw!r}")
    return DateParse(raw=raw, iso=iso)


def _from_month_name(raw: str, month_text: str, day_text: str, year_text: str) -> DateParse:
    """月份为英文名 -> 日月顺序不存在歧义。"""
    month = _MONTH_NAMES.get(month_text.lower())
    if month is None:
        return _fail(raw, f"无法识别的月份名 {month_text!r}：{raw!r}")
    year = _four_digit_year(year_text)
    if year is None:
        return _fail(raw, f"{_SHORT_YEAR_WARNING}：{raw!r}")
    iso = _iso(year, month, int(day_text))
    if iso is None:
        return _fail(raw, f"月/日越界，无法解析为日期：{raw!r}")
    return DateParse(raw=raw, iso=iso)


def parse_date(raw: object) -> DateParse:
    """把单元格原始值解析成 ISO 日期，**原文一并保留**（SPEC §7）。

    支持：openpyxl 的 datetime/date 对象、年在前的数字格式、英文月份名格式、
    中文「年月日」格式、日月在前的数字格式（可能歧义）。

    >>> parse_date("2026-09-15").iso
    '2026-09-15'
    >>> parse_date("2026年9月15日").iso
    '2026-09-15'
    >>> parse_date("15 September 2026").iso
    '2026-09-15'
    >>> parse_date("15/09/2026").iso
    '2026-09-15'
    >>> parse_date("09/15/2026").iso
    '2026-09-15'
    >>> parse_date("08/09/2026").iso is None
    True
    >>> parse_date("08/09/2026").candidates
    ('2026-08-09', '2026-09-08')
    >>> parse_date("08/09/26").iso is None
    True
    >>> parse_date("2026-13-05").iso is None   # 越界不回退成 年-日-月
    True
    >>> parse_date(46281).iso is None          # Excel 序列号不擅自换算
    True
    """
    if raw is None:
        return DateParse(raw=None, iso=None)

    if isinstance(raw, bool):
        return _fail(str(raw), "布尔值不是日期")

    # openpyxl 的日期单元格直接给 datetime/date：已经是确定的日历日，无歧义可言。
    if isinstance(raw, datetime):
        warning = "原值含时间部分，已按日期部分取用" if raw.time() != time(0, 0) else None
        return DateParse(raw=raw.isoformat(), iso=raw.date().isoformat(), warning=warning)
    if isinstance(raw, date):
        return DateParse(raw=raw.isoformat(), iso=raw.isoformat())

    # 数字单元格。openpyxl 只在单元格带日期格式时才给 datetime，否则给的是 Excel
    # 序列号（int/float）。**不换算**：1900 与 1904 两套 epoch 相差约 4 年，用哪套
    # 记在工作簿设置里、本函数看不到，换算就是替用户猜一个可能差 4 年的交期。
    if isinstance(raw, int | float | Decimal):
        text = repr(raw) if isinstance(raw, float) else str(raw)
        return _fail(text, f"数字型单元格不是日期，未按 Excel 序列号擅自换算：{text}")

    if not isinstance(raw, str):
        # 不对未知对象调用 str()：CPython 默认 __repr__ 带内存地址，写进 raw_value /
        # values_digest 会让连跑三次的指纹逐字节不一致（Gate-0 第 15 条）；而且用户
        # 自定义的 __str__ 可能抛异常，会击穿本函数「任何输入都不抛异常」的契约。
        return _fail(None, f"不支持的单元格类型 {type(raw).__name__}")

    original = raw
    text = collapse_ws(nfkc(raw))
    if not text:
        # 空单元格不是错误，交由 MISSING_VALUE 语义处理（SPEC §9.8）。
        return DateParse(raw=original, iso=None)

    cjk = _CJK.match(text)
    if cjk is not None:
        year = _four_digit_year(cjk.group(1))
        if year is None:
            return _fail(original, f"{_SHORT_YEAR_WARNING}：{original!r}")
        iso = _iso(year, int(cjk.group(2)), int(cjk.group(3)))
        if iso is None:
            return _fail(original, f"月/日越界，无法解析为日期：{original!r}")
        return DateParse(raw=original, iso=iso)

    ymd = _YMD.match(text)
    if ymd is not None:
        return _from_numeric_ymd(original, int(ymd.group(1)), int(ymd.group(3)), int(ymd.group(4)))

    dmy = _DMY.match(text)
    if dmy is not None:
        return _from_numeric_dmy(original, int(dmy.group(4)), int(dmy.group(1)), int(dmy.group(3)))

    if _SHORT_YEAR.match(text) is not None:
        return _fail(original, f"{_SHORT_YEAR_WARNING}：{original!r}")

    d_mon_y = _D_MON_Y.match(text)
    if d_mon_y is not None:
        return _from_month_name(original, d_mon_y.group(2), d_mon_y.group(1), d_mon_y.group(3))

    mon_d_y = _MON_D_Y.match(text)
    if mon_d_y is not None:
        return _from_month_name(original, mon_d_y.group(1), mon_d_y.group(2), mon_d_y.group(3))

    return _fail(original, f"无法解析为日期：{original!r}")


def dates_comparable(a: DateParse, b: DateParse) -> bool:
    """两个日期能否直接判等。

    任一侧歧义或未解析出唯一 ISO -> False。调用方据此产出 REVIEW（Verdict.UNCERTAIN），
    **不得**产出 VALUE_CONFLICT（SPEC §9.2）。

    >>> dates_comparable(parse_date("2026-09-15"), parse_date("15-Sep-2026"))
    True
    >>> dates_comparable(parse_date("2026-09-15"), parse_date("08/09/2026"))
    False
    """
    if a.ambiguous or b.ambiguous:
        return False
    return a.iso is not None and b.iso is not None
