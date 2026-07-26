"""Incoterm 归一化单测。SPEC §7：拆 term / named_place / version，**比较时不能只比 term**。"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import doctest
import hashlib
import inspect
import os
import pathlib
import subprocess
import sys
from decimal import Decimal

import pytest

from app.normalization import incoterm as incoterm_module
from app.normalization.incoterm import (
    INCOTERMS_2020,
    KNOWN_INCOTERMS,
    KNOWN_VERSIONS,
    OBSOLETE_INCOTERMS,
    IncotermParse,
    incoterm_equivalent,
    parse_incoterm,
)
from app.normalization.text import collapse_ws, nfkc

#: 真实外贸单据 incoterm 单元格里出现过的脏输入。**所有不变量测试共用这一份语料**，
#: 加一条脏样本即同时被「不崩」「不凭空造 place」「重复执行稳定」三条断言覆盖。
DIRTY_CORPUS: tuple[object, ...] = (
    # 空与非字符串单元格（openpyxl 会直接给 int / float / Decimal / datetime / bool）
    None,
    "",
    "   ",
    "　",
    "\n\t ",
    0,
    2020,
    12.5,
    Decimal("2020"),
    True,
    dt.date(2020, 1, 1),
    ["FOB", "Shanghai"],
    {"term": "FOB"},
    b"FOB Shanghai",
    # 全角 / 中英混排 / 多余空白 / 换行
    "ＦＯＢ　上海",
    "ＣＩＦ，汉堡　Ｉｎｃｏｔｅｒｍｓ　２０２０",
    "  FOB   Shanghai  ",
    "FOB\nShanghai",
    "FOB\tShanghai",
    "FOB 宁波",
    "EXW上海",
    "EX WORKS上海",
    "FREE ON BOARD宁波",
    "delivered at place unloaded 汉堡",
    "FOB价",
    "FOB价 上海",
    # 标点 / 括号 / 包裹符噪声
    "(FOB) Shanghai",
    "FOB (Shanghai)",
    "FOB: Shanghai",
    "FOB - Shanghai Port",
    "CIF, Rotterdam.",
    "C.I.F. Rotterdam",
    "F O B Shanghai",
    "CIF Shanghai (China)",
    "《FOB》Shanghai",
    "FOB {Shanghai}",
    "FOB Shanghai ( )",
    "\x00FOB Shanghai",
    "FOB " + "Shanghai " * 200,
    # 版本写法
    "CIF Hamburg, Incoterms 2020",
    "FOB Ningbo (Incoterms® 2010)",
    "DDP Los Angeles, INCOTERMS(R) 2000",
    "CIF Hamburg (2020)",
    "FCA Shenzhen 2020",
    "CIF Hamburg, Incoterms 2015",
    "CIF Hamburg (Incoterms 2010) Incoterms 2020",
    "FOB Shanghai 2020 2010",
    "FOB Shanghai, PO 2020-001",
    "CIF Hamburg (Contract No. 2020-08)",
    "Incoterms 2020",
    # 多术语 / 认不出
    "FOB/CIF Shanghai",
    "FOB or CIF Shanghai",
    "DAP Des Moines",
    "CFR Dakar",
    "FOB Cifuentes",
    "到岸价",
    "TBD",
    "N/A",
    "-",
    "FOBB Shanghai",
    "EXWORKS Ningbo",
    # 废止术语
    "DAT Hamburg",
    "DDU Moscow",
)

# --------------------------------------------------------------- 三段拆分


@pytest.mark.parametrize(
    ("raw", "term", "place", "version"),
    [
        # 任务书逐条列出的真实写法
        ("FOB Shanghai", "FOB", "Shanghai", None),
        ("CIF NEW YORK", "CIF", "NEW YORK", None),
        ("FOB 宁波", "FOB", "宁波", None),
        ("CIF Hamburg, Incoterms 2020", "CIF", "Hamburg", "2020"),
        ("FOB Ningbo (Incoterms® 2010)", "FOB", "Ningbo", "2010"),
        ("EXW", "EXW", None, None),
        ("C.I.F. Rotterdam", "CIF", "Rotterdam", None),
        # 其他真实变体
        ("fob shanghai", "FOB", "shanghai", None),
        ("  FOB   Shanghai  ", "FOB", "Shanghai", None),
        ("FOB: Shanghai", "FOB", "Shanghai", None),
        ("FOB - Shanghai Port", "FOB", "Shanghai Port", None),
        ("(FOB) Shanghai", "FOB", "Shanghai", None),
        ("FOB (Shanghai)", "FOB", "Shanghai", None),
        ("CIF Shanghai (China)", "CIF", "Shanghai (China)", None),
        ("ＦＯＢ　上海", "FOB", "上海", None),  # 全角字母 + 全角空格，NFKC 归一
        ("DDP Los Angeles, INCOTERMS(R) 2000", "DDP", "Los Angeles", "2000"),
        ("CIP Chicago Incoterms 1990", "CIP", "Chicago", "1990"),
        ("FCA Shenzhen 2020", "FCA", "Shenzhen", "2020"),
        ("FOB Shanghai Incoterms", "FOB", "Shanghai", None),
        ("EX WORKS Ningbo", "EXW", "Ningbo", None),
        ("Free On Board Qingdao", "FOB", "Qingdao", None),
        ("DELIVERED AT PLACE UNLOADED Hamburg", "DPU", "Hamburg", None),
        ("DELIVERED AT PLACE Hamburg", "DAP", "Hamburg", None),
        ("C.I.F.", "CIF", None, None),
        ("CIF, Rotterdam.", "CIF", "Rotterdam", None),
    ],
)
def test_parse_three_segments(
    raw: str, term: str | None, place: str | None, version: str | None
) -> None:
    parsed = parse_incoterm(raw)
    assert (parsed.term, parsed.named_place, parsed.version) == (term, place, version)
    assert parsed.raw == raw


def test_named_place_keeps_original_case_and_chinese() -> None:
    """地名不是枚举，不得 upper()——报告要能逐字引用原文。"""
    assert parse_incoterm("CIF New York").named_place == "New York"
    assert parse_incoterm("CIF NEW YORK").named_place == "NEW YORK"
    assert parse_incoterm("FOB 宁波").named_place == "宁波"
    # term 被大写归一，place 不跟着走
    mixed = parse_incoterm("cif Ho Chi Minh City")
    assert (mixed.term, mixed.named_place) == ("CIF", "Ho Chi Minh City")


def test_version_is_four_digit_year_string() -> None:
    for raw, expected in [
        ("CIF Hamburg, Incoterms 2020", "2020"),
        ("FOB Ningbo (Incoterms® 2010)", "2010"),
        ("DDP Berlin Incoterms 2000", "2000"),
    ]:
        parsed = parse_incoterm(raw)
        assert parsed.version == expected
        assert isinstance(parsed.version, str)


def test_version_absent_is_none_not_guessed() -> None:
    """没写版本就是没写。不得默认补 2020——那是替用户签条款。"""
    assert parse_incoterm("FOB Shanghai").version is None
    assert parse_incoterm("EXW").version is None


def test_unknown_revision_year_is_rejected_with_warning() -> None:
    """2015 不是真实修订版：置 None + warning，不四舍五入到最近的修订版。"""
    parsed = parse_incoterm("CIF Hamburg, Incoterms 2015")
    assert parsed.term == "CIF"
    assert parsed.named_place == "Hamburg"
    assert parsed.version is None
    assert parsed.warning is not None
    assert "2015" in parsed.warning


# --------------------------------------------------------------- 废止术语


@pytest.mark.parametrize("term", sorted(OBSOLETE_INCOTERMS))
def test_obsolete_terms_are_recognized_but_warned(term: str) -> None:
    """历史术语仍要识别（否则连地点一起丢），但必须在 warning 里标注已废止。"""
    parsed = parse_incoterm(f"{term} Shanghai")
    assert parsed.term == term
    assert parsed.named_place == "Shanghai"
    assert parsed.warning is not None
    assert "废止" in parsed.warning


def test_current_terms_carry_no_warning() -> None:
    for term in sorted(INCOTERMS_2020):
        parsed = parse_incoterm(f"{term} Shanghai")
        assert parsed.term == term
        assert parsed.warning is None


def test_obsolete_and_current_sets_are_disjoint() -> None:
    assert not INCOTERMS_2020 & frozenset(OBSOLETE_INCOTERMS)
    assert INCOTERMS_2020 | frozenset(OBSOLETE_INCOTERMS) == KNOWN_INCOTERMS
    assert len(INCOTERMS_2020) == 11


# --------------------------------------------------------------- 显式失败


@pytest.mark.parametrize(
    "raw",
    [
        "到岸价",
        "船上交货",
        "ABC Shanghai",
        "XYZ",
        "FOBB Shanghai",  # 四字母，不得被截成 FOB
        "EXWORKS Ningbo",  # 连写，不得被截成 EXW
        "TBD",
        "见合同",
        "123",
        "-",
    ],
)
def test_unrecognized_term_fails_explicitly(raw: str) -> None:
    """认不出就返回 None + warning，**绝不把整串当作 place**（本产品核心原则）。"""
    parsed = parse_incoterm(raw)
    assert parsed.term is None
    assert parsed.named_place is None
    assert parsed.warning is not None
    assert parsed.raw == raw
    assert not parsed.ok


def test_empty_and_none_inputs_are_silent() -> None:
    """空单元格不是错误，不产 warning（否则每份单据都刷一条假告警）。"""
    for raw in (None, "", "   ", "　"):
        parsed = parse_incoterm(raw)
        assert parsed.term is None
        assert parsed.named_place is None
        assert parsed.version is None
        assert parsed.warning is None
        assert not parsed.ok


def test_version_only_text_has_no_term() -> None:
    parsed = parse_incoterm("Incoterms 2020")
    assert parsed.term is None
    assert parsed.named_place is None
    assert parsed.version == "2020"
    assert parsed.warning is not None


def test_non_string_input_is_stringified_not_crashed() -> None:
    parsed = parse_incoterm(2020)
    assert parsed.term is None
    assert parsed.warning is not None


def test_ok_property_tracks_term() -> None:
    assert parse_incoterm("FOB Shanghai").ok
    assert not parse_incoterm("到岸价").ok


def test_result_is_frozen() -> None:
    parsed = parse_incoterm("FOB Shanghai")
    assert isinstance(parsed, IncotermParse)
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.term = "CIF"  # type: ignore[misc]


# --------------------------------------------------------------- 三段比较


def test_same_term_different_place_is_not_equivalent() -> None:
    """外贸真实翻车点：只比 term 会把装运港差异静默吞掉。"""
    assert not incoterm_equivalent("FOB Shanghai", "FOB Ningbo")
    assert not incoterm_equivalent("CIF New York", "CIF Los Angeles")
    assert not incoterm_equivalent("FOB 宁波", "FOB 上海")
    # 对照：确实是同一条款时必须判等，否则天天假警报
    assert incoterm_equivalent("FOB Shanghai", "FOB Shanghai")


def test_equivalence_ignores_case_punctuation_and_spacing() -> None:
    assert incoterm_equivalent("FOB Shanghai", "fob  shanghai")
    assert incoterm_equivalent("CIF NEW YORK", "C.I.F. New York")
    assert incoterm_equivalent("CIF Rotterdam", "CIF Rotterdam.")


def test_different_term_same_place_is_not_equivalent() -> None:
    assert not incoterm_equivalent("FOB Shanghai", "CIF Shanghai")
    assert not incoterm_equivalent("DAP Hamburg", "DPU Hamburg")


def test_version_missing_on_one_side_is_ignored() -> None:
    """一方没写版本属排版习惯，不构成版本冲突。"""
    assert incoterm_equivalent("CIF Hamburg, Incoterms 2020", "CIF Hamburg")
    assert incoterm_equivalent("CIF Hamburg", "CIF Hamburg (Incoterms® 2010)")
    assert incoterm_equivalent("EXW", "EXW Incoterms 2020")


def test_version_present_on_both_sides_must_match() -> None:
    assert incoterm_equivalent("CIF Hamburg Incoterms 2020", "CIF Hamburg (Incoterms® 2020)")
    assert not incoterm_equivalent("CIF Hamburg Incoterms 2020", "CIF Hamburg (Incoterms® 2010)")


def test_place_missing_on_one_side_is_a_difference() -> None:
    """缺地点的 Incoterm 本身就是不完整条款，如实报差异比静默判等安全。"""
    assert not incoterm_equivalent("FOB Shanghai", "FOB")
    assert not incoterm_equivalent("FOB", "FOB Shanghai")
    assert incoterm_equivalent("FOB", "FOB")


def test_unrecognized_side_is_never_equivalent() -> None:
    """未识别一方应由调用方走 INCOMPARABLE，这里绝不返回 True。"""
    assert not incoterm_equivalent("到岸价", "到岸价")
    assert not incoterm_equivalent("FOB Shanghai", "到岸价")
    assert not incoterm_equivalent(None, None)
    assert not incoterm_equivalent("", "")


def test_equivalent_accepts_parsed_results() -> None:
    left = parse_incoterm("FOB Shanghai")
    right = parse_incoterm("fob shanghai")
    assert incoterm_equivalent(left, right)
    assert not incoterm_equivalent(left, parse_incoterm("FOB Ningbo"))


def test_no_synonym_mapping_between_languages() -> None:
    """「上海 == Shanghai」需要地名词典，那是猜测，MVP-0 不做。"""
    assert not incoterm_equivalent("FOB 上海", "FOB Shanghai")


# --------------------------------------------------------------- 版本：不猜、不挖


@pytest.mark.parametrize(
    ("raw", "place"),
    [
        ("FOB Shanghai, PO 2020-001", "Shanghai, PO 2020-001"),
        ("CIF Hamburg (Contract No. 2020-08)", "Hamburg (Contract No. 2020-08)"),
        ("DAP Berth 2010 East", "Berth 2010 East"),
    ],
)
def test_bare_year_inside_text_is_not_a_version(raw: str, place: str) -> None:
    """裸四位数只在末尾才可能是版本声明。

    从中间挖走它会拼出一个原单据上不存在的 named_place（"Shanghai, PO -001"），
    而这个字段会出现在报告「引用原文」的位置，并按 SPEC §9.4 在 PO→PI 判 CRITICAL。
    宁可不认版本，也不能伪造地点。
    """
    parsed = parse_incoterm(raw)
    assert parsed.version is None
    assert parsed.named_place == place


def test_trailing_bare_year_is_taken_as_version_brackets_included() -> None:
    for raw in ("FCA Shenzhen 2020", "CIF Hamburg (2020)", "CIF Hamburg（2020）"):
        parsed = parse_incoterm(raw)
        assert parsed.version == "2020", raw
        assert parsed.named_place in {"Shenzhen", "Hamburg"}, raw


@pytest.mark.parametrize(
    "raw",
    [
        "CIF Hamburg (Incoterms 2010) Incoterms 2020",
        "CIF Hamburg Incoterms 2020, Incoterms 2010",
        "FOB Shanghai 2020 2010",
    ],
)
def test_conflicting_version_years_fail_explicitly(raw: str) -> None:
    """两个不同修订年 = 无法判断。取第一个就是替用户在两份条款之间选边。"""
    parsed = parse_incoterm(raw)
    assert parsed.version is None
    assert parsed.warning is not None
    assert "2010" in parsed.warning and "2020" in parsed.warning


def test_all_version_labels_are_removed_from_named_place() -> None:
    """摘版本必须摘干净：漏掉第二处会让 "Incoterms 2020" 以字面量漏进地点字段。"""
    parsed = parse_incoterm("CIF Hamburg (Incoterms 2010) Incoterms 2020")
    assert parsed.named_place == "Hamburg"


def test_known_versions_are_the_real_revisions_only() -> None:
    assert set(KNOWN_VERSIONS) == {"1990", "2000", "2010", "2020"}


# --------------------------------------------------------------- 一处两套条款


@pytest.mark.parametrize(
    "raw",
    [
        "FOB/CIF Shanghai",
        "FOB or CIF Shanghai",
        "EXW/FCA Shenzhen",
        "FOB Shanghai / CIF Ningbo",
        "CIF Hamburg 或 CIP Chicago",
        "FOB Shanghai / FOB Ningbo",  # 同术语两个地点，同样无法判断
    ],
)
def test_two_terms_in_one_cell_fail_explicitly(raw: str) -> None:
    """ "FOB/CIF Shanghai" 不是「FOB，地点为 CIF Shanghai」，是一份还没定条款的报价。

    取第一个 = 替企业选边（SPEC §20「不得自动判断哪份文件正确」）。
    """
    parsed = parse_incoterm(raw)
    assert parsed.term is None
    assert parsed.named_place is None
    assert parsed.warning is not None
    assert not parsed.ok


@pytest.mark.parametrize(
    ("raw", "term", "place"),
    [
        ("DAP Des Moines", "DAP", "Des Moines"),  # DES 是废止术语，但这里是地名
        ("CFR Dakar", "CFR", "Dakar"),
        ("FOB Cifuentes", "FOB", "Cifuentes"),
        ("CIF Shanghai (China)", "CIF", "Shanghai (China)"),
        ("FOB Port Said", "FOB", "Port Said"),
    ],
)
def test_place_containing_term_like_words_is_not_multi_term(
    raw: str, term: str, place: str
) -> None:
    """负例保护：多术语判据只认分隔符引出的术语。

    放宽到「任意位置的三字母词」会把 "DAP Des Moines" 判成冲突——用假的「无法识别」
    换真的可用性是坏交易。
    """
    parsed = parse_incoterm(raw)
    assert (parsed.term, parsed.named_place) == (term, place)
    assert parsed.warning is None


def test_multi_term_cell_is_never_equivalent_to_a_single_term_cell() -> None:
    assert not incoterm_equivalent("FOB/CIF Shanghai", "FOB Shanghai")
    assert not incoterm_equivalent("FOB/CIF Shanghai", "FOB/CIF Shanghai")


# --------------------------------------------------------------- 拼写全称的边界


@pytest.mark.parametrize(
    ("raw", "term", "place"),
    [
        ("EX WORKS上海", "EXW", "上海"),
        ("FREE ON BOARD宁波", "FOB", "宁波"),
        ("delivered at place unloaded 汉堡", "DPU", "汉堡"),
        ("EXW上海", "EXW", "上海"),
        ("FOB上海", "FOB", "上海"),
    ],
)
def test_spelled_out_term_followed_by_cjk_place(raw: str, term: str, place: str) -> None:
    """中文单据「英文术语 + 中文港口」常常不留空格。

    `str.isalnum()` 对 CJK 返回 True，用它做右边界会让 "EX WORKS上海" 认不出而
    "EXW上海" 认得出——同一产品对同一写法给两种答案，是最难排查的那类不一致。
    """
    parsed = parse_incoterm(raw)
    assert (parsed.term, parsed.named_place) == (term, place)


@pytest.mark.parametrize("raw", ["EX WORKSHOP Ningbo", "FREE ON BOARDING Qingdao"])
def test_spelled_out_term_must_be_a_whole_phrase(raw: str) -> None:
    """右边界放宽后仍不得把更长的英文词截断成术语。"""
    parsed = parse_incoterm(raw)
    assert parsed.term is None
    assert parsed.named_place is None


# --------------------------------------------------------------- 脏输入不变量


@pytest.mark.parametrize("raw", DIRTY_CORPUS, ids=lambda value: repr(value)[:40])
def test_dirty_cell_never_raises_and_holds_invariants(raw: object) -> None:
    """对任意单元格值都必须有三条不变量成立，否则解析层会把脏数据洗成看似干净的值。"""
    parsed = parse_incoterm(raw)

    # 1. 原文一定留档（SPEC §7 四元组）。
    assert parsed.raw == (None if raw is None else str(raw))

    # 2. term 认不出 -> named_place 必须一并为 None，绝不把整串当地点。
    if parsed.term is None:
        assert parsed.named_place is None

    # 3. 非空输入但没识别出 term -> 必须有 warning（显式失败，不静默）；
    #    空单元格不是错误，不得刷假告警。
    blank = raw is None or not collapse_ws(nfkc(str(raw)))
    if blank:
        assert parsed.warning is None
        assert parsed.version is None
    elif parsed.term is None:
        assert parsed.warning is not None


@pytest.mark.parametrize("raw", DIRTY_CORPUS, ids=lambda value: repr(value)[:40])
def test_named_place_is_a_contiguous_fragment_of_the_raw_text(raw: object) -> None:
    """named_place 必须是原文的一段连续片段（忽略空白），**不得由碎片拼接而成**。

    这条不变量机械地封死「把中间的年份/单号挖掉再把两头接起来」这类修补：
    报告在「引用原文」的位置显示一个原单据上不存在的字符串，比留下噪声危险得多。
    带 "Incoterms" 字样的显式版本声明是**自证的标签**，摘走它才是正确行为，故排除。
    """
    parsed = parse_incoterm(raw)
    if parsed.named_place is None:
        return
    source = nfkc(str(raw))
    if "incoterm" in source.lower():
        return
    squeezed_source = "".join(source.split())
    squeezed_place = "".join(parsed.named_place.split())
    assert squeezed_place in squeezed_source


def test_named_place_never_swallows_an_unrecognized_whole_string() -> None:
    """反向确认最贵的那条失效模式：认不出时地点字段必须是 None，而不是原文。"""
    for raw in ("到岸价", "船上交货", "见合同", "TO BE ADVISED"):
        parsed = parse_incoterm(raw)
        assert parsed.named_place is None
        assert parsed.term is None


# --------------------------------------------------------------- 比较的代数性质


@pytest.mark.parametrize("raw", DIRTY_CORPUS, ids=lambda value: repr(value)[:40])
def test_equivalence_is_symmetric_and_never_true_for_unparsed(raw: object) -> None:
    """对称性：a≡b 必须等于 b≡a，否则差异集合会依赖角色迭代顺序（Gate-0 第 15 条）。"""
    other = parse_incoterm("FOB Shanghai")
    assert incoterm_equivalent(raw, other) == incoterm_equivalent(other, raw)

    parsed = parse_incoterm(raw)
    # 自反性只对识别成功的一方成立；未识别一方必须走 INCOMPARABLE 而非 EQUAL。
    assert incoterm_equivalent(parsed, parsed) is parsed.ok


# --------------------------------------------------------------- 铁律与确定性


def test_module_contains_no_float_anywhere() -> None:
    """SPEC §7 / CLAUDE.md：域内模块禁止出现 `float(`，禁止二进制浮点字面量。

    用 AST 扫描而不是字符串查找——注释里出现 "float(" 不算违规，代码里才算。
    """
    tree = ast.parse(inspect.getsource(incoterm_module))
    float_names = [
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "float"
    ]
    float_literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert float_names == []
    assert float_literals == []


def test_spelled_out_candidates_are_totally_ordered() -> None:
    """候选列表必须是全序：只按长度排序时等长别名靠 dict 插入序决胜，那是隐式依赖。"""
    candidates = incoterm_module._SPELLED_OUT_SORTED
    assert list(candidates) == sorted(candidates, key=lambda item: (-len(item[0]), item[0]))
    lengths = [len(alias) for alias, _ in candidates]
    assert lengths == sorted(lengths, reverse=True)


def _corpus_digest() -> str:
    parts: list[str] = []
    for raw in DIRTY_CORPUS:
        parsed = parse_incoterm(raw)
        parts.append(f"{parsed.term}|{parsed.named_place}|{parsed.version}|{parsed.warning}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def test_repeated_parsing_is_byte_identical() -> None:
    assert _corpus_digest() == _corpus_digest()


def test_parsing_is_stable_across_hash_seeds() -> None:
    """Gate-0 第 15 条：换 PYTHONHASHSEED 结果必须逐字节一致。

    frozenset / dict 的迭代顺序随进程哈希种子变化，只在同一进程内重跑**测不出**这类
    不确定性——必须另起进程换种子跑。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    script = "from tests.test_normalization_incoterm import _corpus_digest; print(_corpus_digest())"
    digests = set()
    for seed in ("0", "1", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        digests.add(completed.stdout.strip())
    assert len(digests) == 1
    assert digests == {_corpus_digest()}


# --------------------------------------------------------------- 文档示例


def test_module_doctests_pass() -> None:
    results = doctest.testmod(incoterm_module, verbose=False)
    assert results.attempted > 0
    assert results.failed == 0
