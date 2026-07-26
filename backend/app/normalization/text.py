"""文本归一化。SPEC §6.2、§7。

表头别名匹配的归一化管道在这里定死。**禁止子串包含匹配，禁止在表头环节用模糊匹配**
——裸别名 `price` 会命中 `Total Price`、`数量` 会命中 `箱数量`，每行都会报假
CALCULATION_ERROR（CLAUDE.md 坑 #7）。
"""

from __future__ import annotations

import re
import unicodedata

#: 去掉首个括号及其后全部内容。表头常见 "Unit Price (USD)"、"数量（PCS）"。
_TRAILING_PAREN = re.compile(r"[(（\[【].*$", re.DOTALL)

#: 表头归一化时移除的标点与空白（保留字母数字与 CJK）。
_PUNCT_WS = re.compile(r"[\s　!-/:-@\[-`{-~ -⁯、-〿！-･]+")

_WS_RUN = re.compile(r"[\s　]+")


def nfkc(value: str) -> str:
    """统一全角半角（SPEC §7）。全角数字/字母/标点在中文单据里极常见。"""
    return unicodedata.normalize("NFKC", value)


def collapse_ws(value: str) -> str:
    """清理无意义空白：折叠连续空白为单个空格并去首尾。"""
    return _WS_RUN.sub(" ", value).strip()


def normalize_text(value: str) -> str:
    """通用文本归一化：NFKC + 折叠空白。保留大小写与标点。"""
    return collapse_ws(nfkc(value))


def normalize_header(value: str) -> str:
    """表头归一化管道（SPEC §6.2，顺序不可调换）。

    小写 -> NFKC -> 去掉首个括号及其后内容 -> 去空白与标点

    归一化后**只做精确字典查找**。

    >>> normalize_header("Unit Price (USD)")
    'unitprice'
    >>> normalize_header("单价（USD）")
    '单价'
    >>> normalize_header("Total  Price")
    'totalprice'
    >>> normalize_header("箱数量")
    '箱数量'
    """
    folded = nfkc(value).lower()
    folded = _TRAILING_PAREN.sub("", folded)
    return _PUNCT_WS.sub("", folded).strip()


def is_blankish(value: object) -> bool:
    """单元格是否视为空。用于表头打分与数据区终止判定。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
