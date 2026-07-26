"""Incoterm 归一化。SPEC §7（保留原第十节）。

拆 `term` / `named_place` / `version` 三段。**比较时不能只比 term。**

理由：`FOB Shanghai` 与 `FOB Ningbo` 的 term 逐字相同，装运港却差着一整段内陆运费
与风险转移点。只比 term 会把这类差异静默吞掉——SPEC §4.1 因此把
`incoterm` / `incoterm_named_place` / `incoterm_version` 拆成三个独立字段，
§9.4 也给它们各自的严重度（PO→PI 为 CRITICAL）。

**无法判断时显式失败**：认不出 term 就返回 `term=None` + warning，
**绝不把整串塞进 named_place**——那等于用一个看起来有值的字段掩盖一次识别失败。

同一条原则的另外三处兑现（每一处都有对应单测）：

- 一处写了两套条款（"FOB/CIF Shanghai"）-> `term=None`，不取第一个。
- 声明了两个不同修订年 -> `version=None`，不取第一个。
- 裸四位数**只在末尾**才当版本；"FOB Shanghai, PO 2020-001" 里的 2020 属单号，
  挖走它会拼出一个原单据上不存在的 named_place。

`named_place` 只能是原文的**一段连续片段**（忽略空白），不得由若干碎片拼接而成——
它会出现在报告「引用原文」的位置，凭空拼出的字符串比留下噪声危险得多。

历史术语（DAT / DAF / DES / DEQ / DDU）仍然识别，否则真实老单据会退化成
「认不出」并连地点一起丢掉；但必须在 warning 里点明已废止。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.normalization.text import collapse_ws, nfkc

# --------------------------------------------------------------- 术语表

#: Incoterms® 2020 的十一个术语（大写）。
INCOTERMS_2020: frozenset[str] = frozenset(
    {"EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"}
)

#: 已废止但真实单据上仍在用的历史术语 -> 废止说明（进 warning）。
OBSOLETE_INCOTERMS: dict[str, str] = {
    "DAT": "DAT 是 Incoterms 2010 术语，2020 版已废止，由 DPU 取代",
    "DAF": "DAF 是 Incoterms 2000 及更早版本术语，2010 版起已废止",
    "DES": "DES 是 Incoterms 2000 及更早版本术语，2010 版起已废止",
    "DEQ": "DEQ 是 Incoterms 2000 及更早版本术语，2010 版起已废止",
    "DDU": "DDU 是 Incoterms 2000 及更早版本术语，2010 版起已废止，由 DAP 取代",
}

#: 识别集合 = 现行 + 历史。识别 ≠ 认可，历史术语一律带 warning。
KNOWN_INCOTERMS: frozenset[str] = INCOTERMS_2020 | frozenset(OBSOLETE_INCOTERMS)

#: 真实存在过的 Incoterms 修订年份。不在表内的年份**不猜**，标 warning 并置 None。
KNOWN_VERSIONS: frozenset[str] = frozenset({"1990", "2000", "2010", "2020"})

#: 拼写全称。真实单据上 "EX WORKS Shanghai"、"FREE ON BOARD Ningbo" 并不罕见。
#: 只收标准全称，不收自造缩写——这里多收一条就是多一条猜测。
_SPELLED_OUT: dict[str, str] = {
    "ex works": "EXW",
    "free carrier": "FCA",
    "free alongside ship": "FAS",
    "free on board": "FOB",
    "cost and freight": "CFR",
    "cost insurance and freight": "CIF",
    "carriage paid to": "CPT",
    "carriage and insurance paid to": "CIP",
    "delivered at place unloaded": "DPU",
    "delivered at place": "DAP",
    "delivered duty paid": "DDP",
    "delivered at terminal": "DAT",
    "delivered duty unpaid": "DDU",
}

#: 长的在前：`delivered at place unloaded` 必须先于 `delivered at place` 尝试。
#: 次键用别名本身，把顺序**完全钉死**：只按 `-len` 排序时等长别名之间靠 dict 插入序
#: 决胜，那是隐式依赖；Gate-0 第 15 条要求连跑 3 次逐字节一致，排序键必须是全序。
_SPELLED_OUT_SORTED: tuple[tuple[str, str], ...] = tuple(
    sorted(_SPELLED_OUT.items(), key=lambda item: (-len(item[0]), item[0]))
)

# --------------------------------------------------------------- 正则

#: 带 "Incoterms" 字样的版本子串："Incoterms 2020" / "(Incoterms® 2010)" / "INCOTERMS(R),2000"
#: 前置 `(?<![A-Za-z])` 保证 "incoterms" 是完整词，不在更长的单词内部命中。
_VERSION_LABELLED = re.compile(
    r"[(（\[【]?\s*(?<![A-Za-z])incoterms?\s*(?:®|™|©|\(r\))?\s*[,，;；:：]?\s*(\d{4})\s*[)）\]】]?",
    re.IGNORECASE,
)

#: 摘掉年份后可能残留的孤立 "Incoterms" 字样（"FOB Shanghai Incoterms"）。
_VERSION_WORD = re.compile(r"(?<!\w)incoterms?\s*(?:®|™|©|\(r\))?(?!\w)", re.IGNORECASE)

#: 不带 "Incoterms" 字样的裸年份。**只认真实修订年**，免得把地名/单号里的数字当版本。
_VERSION_BARE = re.compile(r"(?<!\w)(1990|2000|2010|2020)(?!\w)")

#: 裸年份**只在文本末尾**才算版本声明（可带一层括号）。
#: 中间的四位数几乎总是单号/门牌/合同号的一部分（"FOB Shanghai, PO 2020-001"），
#: 从中间挖走会拼出一个原单据上不存在的 named_place，比不识别版本危险得多。
_VERSION_BARE_TRAILING = re.compile(r"[(（\[【]?\s*(1990|2000|2010|2020)\s*[)）\]】]?\s*$")

#: 由分隔符引出的第二个三字母术语："EXW/FCA Shenzhen"、"FOB or CIF Shanghai"。
#: **只认分隔符引出的**：仅靠空格分隔会把 "DAP Des Moines"（DES 是废止术语）
#: 这类正常地名误判为多术语，那是用假的「无法识别」换真的可用性。
_ALTERNATIVE_TERM = re.compile(r"(?:[/|&]|或|\bor\b)\s*([A-Za-z]{3})(?![A-Za-z0-9])", re.IGNORECASE)

#: 开头的三字母术语，允许 "C.I.F." 这种带点写法与字母间空格。
#: 尾部 `(?![A-Za-z0-9])` 保证是完整词——"FOBB" / "EXWORKS" 不得被截成 "FOB" / "EXW"。
_TERM_AT_START = re.compile(
    r"^([A-Za-z])\s*\.?\s*([A-Za-z])\s*\.?\s*([A-Za-z])\s*\.?(?![A-Za-z0-9])"
)

#: 术语前可能的包裹符与前导噪声。
_LEADING_NOISE = " \t(（[【<《-–—:：,，*"

#: named_place 两端要剥掉的标点（**不含括号**，括号另有配对逻辑）。
_EDGE_PUNCT = " \t,，、;；:：.。!！?？/\\|-–—_~*+=\"'`"

#: 比较键用：去掉全部标点，只留字母数字/CJK 与空格。
_PLACE_NOISE = re.compile(r"[^\w\s]+")

_BRACKET_PAIRS: dict[str, str] = {
    "(": ")",
    "（": "）",
    "[": "]",
    "【": "】",
    "{": "}",
    "<": ">",
    "《": "》",
}
_BRACKET_CLOSERS: dict[str, str] = {close: opening for opening, close in _BRACKET_PAIRS.items()}


# --------------------------------------------------------------- 结果类型


@dataclass(frozen=True)
class IncotermParse:
    """Incoterm 三段解析结果（SPEC §7）。

    `named_place` **保留原始大小写与中文**：地名不是枚举，`upper()` 会让报告里
    「引用原文」的位置显示出一个原单据上不存在的写法。大小写差异在
    `incoterm_equivalent` 的比较键里才被忽略，不在存储层被抹掉。
    """

    raw: str | None
    term: str | None
    named_place: str | None
    version: str | None
    warning: str | None = None

    @property
    def ok(self) -> bool:
        """term 是否识别成功。未识别应走 INCOMPARABLE，不是 DIFFERENT（SPEC §9.1）。"""
        return self.term is not None


# --------------------------------------------------------------- 解析


def parse_incoterm(raw: object) -> IncotermParse:
    """把单元格文本拆成 term / named_place / version 三段。

    >>> parse_incoterm("FOB Shanghai").term
    'FOB'
    >>> parse_incoterm("FOB Shanghai").named_place
    'Shanghai'
    >>> parse_incoterm("FOB 宁波").named_place
    '宁波'
    >>> parse_incoterm("C.I.F. Rotterdam").term
    'CIF'
    >>> parse_incoterm("CIF Hamburg, Incoterms 2020").version
    '2020'
    >>> parse_incoterm("FOB Ningbo (Incoterms® 2010)").named_place
    'Ningbo'
    >>> parse_incoterm("EXW").named_place is None
    True
    >>> parse_incoterm("到岸价").term is None
    True
    >>> parse_incoterm("到岸价").named_place is None
    True
    >>> parse_incoterm("EX WORKS上海").named_place
    '上海'
    >>> parse_incoterm("FOB/CIF Shanghai").term is None
    True
    >>> parse_incoterm("FOB Shanghai, PO 2020-001").version is None
    True
    >>> parse_incoterm("FOB Shanghai, PO 2020-001").named_place
    'Shanghai, PO 2020-001'
    """
    if raw is None:
        return IncotermParse(raw=None, term=None, named_place=None, version=None)

    original = str(raw)
    working = collapse_ws(nfkc(original))
    if not working:
        return IncotermParse(raw=original, term=None, named_place=None, version=None)

    warnings: list[str] = []
    working, version = _extract_version(working, warnings)

    candidate, remainder = _split_term(working)
    if candidate is None:
        warnings.append(f"无法识别 Incoterm 术语：{original!r}")
        return IncotermParse(
            raw=original,
            term=None,
            named_place=None,
            version=version,
            warning=_join(warnings),
        )
    if candidate not in KNOWN_INCOTERMS:
        warnings.append(f"未知 Incoterm 术语 {candidate}，不猜测，也不把原文当作交货地点")
        return IncotermParse(
            raw=original,
            term=None,
            named_place=None,
            version=version,
            warning=_join(warnings),
        )

    alternative = _alternative_term(remainder)
    if alternative is not None:
        warnings.append(
            f"同一处同时给出 {candidate} 与 {alternative} 两套条款，不擅自选其一，交人工判定"
        )
        return IncotermParse(
            raw=original,
            term=None,
            named_place=None,
            version=version,
            warning=_join(warnings),
        )

    if candidate in OBSOLETE_INCOTERMS:
        warnings.append(OBSOLETE_INCOTERMS[candidate])

    return IncotermParse(
        raw=original,
        term=candidate,
        named_place=_clean_place(remainder),
        version=version,
        warning=_join(warnings),
    )


def _cut_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """删掉若干区间并折叠空白。区间必须按起点升序且互不重叠（`finditer` 天然满足）。"""
    kept: list[str] = []
    cursor = 0
    for start, end in spans:
        kept.append(text[cursor:start])
        cursor = end
    kept.append(text[cursor:])
    return collapse_ws(" ".join(kept))


def _extract_version(text: str, warnings: list[str]) -> tuple[str, str | None]:
    """摘出版本年份并把版本子串从文本里剔除，返回 (剩余文本, 版本)。

    三条规则，每条都是「无法判断时显式失败」的直接兑现：

    1. 带 "Incoterms" 字样 = 显式版本声明，**全部**摘走（不能只摘第一个，否则
       第二处 "Incoterms 2020" 会以字面量形式漏进 named_place）。
    2. 声明了多个**互不相同**的年份 -> 版本置 None + warning。取第一个就是替用户
       在两份条款之间选边。
    3. 不带字样的裸年份**只在末尾**才算版本；中间的四位数留在原地不动
       （见 `_VERSION_BARE_TRAILING` 注释）。
    """
    labelled = list(_VERSION_LABELLED.finditer(text))
    if labelled:
        rest = _cut_spans(text, [(m.start(), m.end()) for m in labelled])
        years = sorted({m.group(1) for m in labelled})
        if len(years) > 1:
            warnings.append(
                f"文本内出现多个 Incoterms 版本年份 {'/'.join(years)}，无法判断以哪个为准，未采纳"
            )
            return rest, None
        year = years[0]
        if year in KNOWN_VERSIONS:
            return rest, year
        warnings.append(f"Incoterms 版本年份 {year} 不是已知修订版，未采纳")
        return rest, None

    text = collapse_ws(_VERSION_WORD.sub(" ", text))

    trailing = _VERSION_BARE_TRAILING.search(text)
    if trailing is None:
        # 年份不在末尾：可能是单号/合同号/门牌的一部分。不当版本，**更不从中间挖走**。
        return text, None

    year = trailing.group(1)
    others = {m.group(1) for m in _VERSION_BARE.finditer(text[: trailing.start()])}
    if others - {year}:
        listed = "/".join(sorted(others | {year}))
        warnings.append(f"文本内出现多个修订年份 {listed}，无法判断以哪个为准，未采纳")
        return text, None
    return collapse_ws(text[: trailing.start()]), year


def _is_ascii_alnum(char: str) -> bool:
    """术语的右边界判据。

    **必须与 `_TERM_AT_START` 的 `(?![A-Za-z0-9])` 用同一套判据。**
    `str.isalnum()` 对 CJK 返回 True，用它会让 "EX WORKS上海" 认不出，而同一份单据上
    "EXW上海" 却认得出——同一个产品对同一种写法给两种答案是最难排查的那类不一致。
    """
    return char.isascii() and char.isalnum()


def _split_term(text: str) -> tuple[str | None, str]:
    """返回 (候选术语大写, 剩余文本)。候选**未必**在已知集合里，由调用方判定。"""
    head = text.lstrip(_LEADING_NOISE)

    match = _TERM_AT_START.match(head)
    if match is not None:
        return "".join(match.groups()).upper(), head[match.end() :]

    for alias, term in _SPELLED_OUT_SORTED:
        size = len(alias)
        if head[:size].lower() == alias and (len(head) == size or not _is_ascii_alnum(head[size])):
            return term, head[size:]

    return None, head


def _alternative_term(remainder: str) -> str | None:
    """剩余文本里由分隔符引出的第二个已知术语，没有则 None。

    "FOB/CIF Shanghai" 不是「FOB，交货地点是 CIF Shanghai」，而是一份**还没定条款**的
    报价。取第一个就是替企业选边（SPEC §20：不得自动判断哪份文件正确）。

    已知边界：只靠空格分隔的 "DAP Hamburg DPU Bremen" 不会被识别为多术语。放宽到
    「任意位置的三字母词」会把 "DAP Des Moines" 判成 DAP/DES 冲突——用假的无法识别
    换真的可用性是坏交易，宁可在这一步漏，也不误伤正常地名。
    """
    for match in _ALTERNATIVE_TERM.finditer(remainder):
        code = match.group(1).upper()
        if code in KNOWN_INCOTERMS:
            return code
    return None


def _clean_place(text: str) -> str | None:
    """清理 named_place：剥两端标点与不成对括号、折叠空白，**保留大小写与中文**。"""
    place = collapse_ws(text)
    while place:
        trimmed = place.strip(_EDGE_PUNCT)
        if not trimmed:
            return None
        trimmed = collapse_ws(_strip_unpaired_brackets(trimmed))
        if trimmed == place:
            return place
        place = trimmed
    return None


def _strip_unpaired_brackets(place: str) -> str:
    """剥掉整体包裹的成对括号，以及版本子串被摘走后剩下的**单只**括号。

    中间成对出现的括号原样保留——"Shanghai (China)" 是地名的一部分，不是包装。
    """
    first, last = place[0], place[-1]
    if len(place) >= 2 and _BRACKET_PAIRS.get(first) == last:
        return place[1:-1]
    if first in _BRACKET_PAIRS and _BRACKET_PAIRS[first] not in place:
        return place[1:]
    if first in _BRACKET_CLOSERS and _BRACKET_CLOSERS[first] not in place:
        return place[1:]
    if last in _BRACKET_CLOSERS and _BRACKET_CLOSERS[last] not in place:
        return place[:-1]
    if last in _BRACKET_PAIRS:
        return place[:-1]
    return place


def _join(warnings: list[str]) -> str | None:
    return "；".join(warnings) if warnings else None


# --------------------------------------------------------------- 比较


def _place_key(place: str | None) -> str | None:
    """比较用的地点键：忽略大小写与标点，**不做任何同义地名映射**。

    「上海 == Shanghai」需要一张地名词典，那是猜测，不在 MVP-0 内。
    两边写法不同就如实产出差异，交人工看。
    """
    if place is None:
        return None
    return collapse_ws(_PLACE_NOISE.sub(" ", nfkc(place))).casefold() or None


def incoterm_equivalent(a: object, b: object) -> bool:
    """**三段全等**才算相等（SPEC §7：比较时不能只比 term）。

    参数可以是原始文本，也可以是已解析的 `IncotermParse`。

    判定规则：

    - term 不同 -> False。
    - term 相同但 named_place 不同 -> **False**。`FOB Shanghai` 与 `FOB Ningbo`
      只比 term 会得出「相等」，实际差着一整段内陆运费与风险转移点——这是外贸真实翻车点。
    - named_place 一边有一边无 -> False。缺地点的 Incoterm 本身就是不完整条款，
      如实报差异比静默判等安全。
    - version **一边有一边无 -> 忽略 version**，只比 term 与 named_place。
      理由：单据上写不写 "Incoterms 2020" 属排版习惯，一方没写不构成版本冲突；
      但两边都写了且不同（2010 与 2020 之间 DAT 被 DPU 取代）就是真差异。
    - 任一方 term 未识别 -> False。调用方应先查 `.ok`：未识别要走 INCOMPARABLE
      而不是 DIFFERENT（SPEC §9.1，把「无法比较」说成「不一致」是假警报）。

    >>> incoterm_equivalent("FOB Shanghai", "fob  shanghai")
    True
    >>> incoterm_equivalent("FOB Shanghai", "FOB Ningbo")
    False
    >>> incoterm_equivalent("CIF Hamburg, Incoterms 2020", "CIF Hamburg")
    True
    >>> incoterm_equivalent("CIF Hamburg, Incoterms 2020", "CIF Hamburg (Incoterms 2010)")
    False
    >>> incoterm_equivalent("到岸价", "到岸价")
    False
    """
    left = a if isinstance(a, IncotermParse) else parse_incoterm(a)
    right = b if isinstance(b, IncotermParse) else parse_incoterm(b)

    if left.term is None or right.term is None:
        return False
    if left.term != right.term:
        return False
    if _place_key(left.named_place) != _place_key(right.named_place):
        return False
    if left.version is not None and right.version is not None:
        return left.version == right.version
    return True
