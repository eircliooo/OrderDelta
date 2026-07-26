"""解析层契约。SPEC §5.1。

**这个模块是「砍实现保形状」策略的支点。** MVP-1 的 pdfplumber 只需产出带
page/bbox/page_w/page_h 的 ParsedCell 与 ParsedTable，下游提取、标准化、匹配、
比较、证据五层一行不改。省掉它、让 openpyxl 直接写领域对象，PDF 就是一次重写
而非一次追加。

can_parse 返回 ParseCapability 而非 bool ——「扫描 PDF 被明确拒绝」要的正是
**拒绝原因**，bool 承载不了。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.domain.enums import ParseReasonCode, ParseStatus


class CoordinateSpace:
    """坐标系。MVP-1 的 PDF bbox 必须归一化到 0-1、原点左上。

    现在就写死，避免 PDF.js 高亮整体错位（pdfplumber 原点在左下）。
    """

    XLSX_GRID = "XLSX_GRID"
    PDF_PT_TOPLEFT = "PDF_PT_TOPLEFT"


@dataclass(frozen=True)
class DocumentInput:
    """交给解析器的输入。**路径由服务端生成，绝不含用户可控成分。**"""

    path: Path
    original_filename: str
    mime_type: str
    file_size: int
    sha256: str


@dataclass(frozen=True)
class ParseLimits:
    """处理上限。SPEC §15.1 要求「添加处理超时和最大页数、最大行数限制」。

    行数/表数限制必须在遍历时**边数边判**，不要读 XLSX 的 <dimension> 做预检
    —— openpyxl 官方警告该值常被写错，恶意文件声明 A1:A1 即可绕过。
    """

    max_sheets: int = 20
    max_rows_per_sheet: int = 5000
    max_total_rows: int = 20000
    max_header_scan_rows: int = 20

    #: ⚠️ **MVP-0 未强制执行**，只是把预期值记在契约里。
    #:
    #: 之所以留着而不删：README §14 第 10 条与 docs/security.md §4.2 都把
    #: 「解析超时尚未实现」列为已知风险，字段删掉反而让文档指向一个不存在的东西。
    #: 之所以不实现：真正耗时的是 openpyxl 的 `load_workbook` 本身，它不可中断；
    #: 在可中断的行遍历里插一个墙钟判断只能挡住构造得极巧的输入，
    #: 却会把系统时钟读进解析路径——而「同一输入两次解析结果一致」是 Gate-0 第 15 条。
    #: 当前的实际缓解是确定性的多重上限：文件体积、解压比、工作表数、行数。
    #: 真做超时应当放在**请求层**（独立进程 / worker），不在解析器内部。见
    #: docs/future-scope.md。
    timeout_seconds: int = 30


DEFAULT_LIMITS = ParseLimits()


@dataclass(frozen=True)
class ParseCapability:
    """能否解析，以及不能的原因。"""

    accepted: bool
    reason_code: ParseReasonCode | None = None
    detail: str | None = None

    @classmethod
    def ok(cls) -> ParseCapability:
        return cls(accepted=True)

    @classmethod
    def reject(cls, code: ParseReasonCode, detail: str | None = None) -> ParseCapability:
        return cls(accepted=False, reason_code=code, detail=detail)


@dataclass(frozen=True)
class CellRef:
    """单元格定位。XLSX 用 sheet+addr，PDF（MVP-1）用 page+bbox。"""

    sheet_name: str
    row_index: int
    col_index: int
    address: str
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    coordinate_space: str = CoordinateSpace.XLSX_GRID


@dataclass(frozen=True)
class ParsedCell:
    """一个单元格的完整读数。

    formula 与 cached_value **必须分开存**：openpyxl 一次 load_workbook 只能拿
    公式串或缓存值之一（data_only 互斥），两者都要就必须加载两次（SPEC §5.2）。
    """

    ref: CellRef
    value_raw: str | None
    value_typed: Any
    data_type: str
    formula: str | None = None
    cached_value: str | None = None
    merged_range: str | None = None

    @property
    def is_formula_without_cache(self) -> bool:
        """公式无缓存值。

        判定 = 是公式 **且** 缓存值为 None。
        禁止把「值为空」误报为「无缓存」——空单元格不是公式。
        """
        return self.formula is not None and self.cached_value is None


@dataclass(frozen=True)
class ParsedTable:
    """提取层的唯一入口。XLSX 与 PDF（MVP-1）共用。"""

    table_id: str
    sheet_name: str
    header_rows: tuple[int, ...]
    cells: tuple[tuple[ParsedCell, ...], ...]
    merged_ranges: tuple[str, ...] = ()

    def row(self, index: int) -> tuple[ParsedCell, ...]:
        return self.cells[index]


@dataclass(frozen=True)
class TextBlock:
    """表格之外的文本块（抬头、Key-Value 区、备注）。

    block_id 是 LLM 适配器（MVP-1）唯一被允许引用的东西——适配器只能「选」
    不能「写」，凭空造值在结构上不可能（SPEC §14）。
    """

    block_id: str
    text: str
    ref: CellRef


@dataclass(frozen=True)
class ParsedPage:
    """MVP-1 PDF 用。MVP-0 恒为空。"""

    page_number: int
    width: float
    height: float
    rotation: int = 0


@dataclass(frozen=True)
class ParsedDocument:
    """解析结果。**显式失败**：status/reason_code 是字段，不是抛异常也不是中文文案。"""

    parser_name: str
    parser_version: str
    status: ParseStatus
    reason_code: ParseReasonCode | None = None
    detail: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    tables: tuple[ParsedTable, ...] = ()
    blocks: tuple[TextBlock, ...] = ()
    pages: tuple[ParsedPage, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """能否进入比较集合。REJECTED / FAILED 的文档不参与任何比较，
        因而不产生任何 Difference（SPEC §9.8）——否则两文件场景会被假缺失淹没。"""
        return self.status in (ParseStatus.OK, ParseStatus.NEEDS_REVIEW)


@runtime_checkable
class DocumentParser(Protocol):
    """解析器接口。未来加 OCR / Docling 实现走同一接口。"""

    name: str
    version: str

    def can_parse(self, src: DocumentInput) -> ParseCapability: ...

    def parse(self, src: DocumentInput, limits: ParseLimits = ...) -> ParsedDocument: ...


def select_parser(
    src: DocumentInput, parsers: Sequence[DocumentParser]
) -> tuple[DocumentParser | None, ParseCapability]:
    """挑第一个接受该文件的解析器。

    全部拒绝时返回**最具体的**拒绝原因（非 UNSUPPORTED_EXT 优先），
    这样 PDF 上传拿到的是「MVP-0 暂不支持 PDF」而不是含糊的「不支持的文件」。
    """
    fallback: ParseCapability | None = None
    for parser in parsers:
        cap = parser.can_parse(src)
        if cap.accepted:
            return parser, cap
        if fallback is None or (
            fallback.reason_code is ParseReasonCode.UNSUPPORTED_EXT
            and cap.reason_code is not ParseReasonCode.UNSUPPORTED_EXT
        ):
            fallback = cap
    return None, fallback or ParseCapability.reject(ParseReasonCode.UNSUPPORTED_EXT)
