"""架构守卫测试：把 CLAUDE.md 的架构边界变成**机器检查**，不靠 code review。

覆盖：

  硬约束 #7   域内禁止 `float(` 与浮点字面量 -> TestNoFloatInDomain
  硬约束 #8   比较/匹配/导出禁止 import ORM  -> TestImportBoundary
  Gate-0 #16  只产已声明枚举 + 词表冻结      -> TestOnlyDeclaredEnums（-m enum_subset）
  SPEC §14    extraction_method 白名单      -> TestExtractionMethodWhitelist
  硬约束 #16  零 skip                       -> TestNoSkippedTests

守卫测试的全部价值在**失败时说清楚原因**。只报 `assert False` 的守卫，
下一个人会直接把它删掉——所以每条断言的失败信息都必须回答两个问题：
「违反了哪条约束」与「这条约束为什么存在」。

三条设计约束，缺一条守卫就会变成摆设：

  1. **扫描器与文件系统解耦**：每个检测器都有一个吃 AST、吐违规列表的纯函数
     （`_scan_float` / `_imported_targets` / `_extraction_method_literals` /
     `_skip_violations_in`），因此「检测器对已知违规样本报警」这件事本身
     被合成样本锁住，而不是靠「今天恰好是绿的」。
  2. **正反两面都要锁**：只测「违规被抓到」会纵容一个误杀一切的实现；
     只测「合法写法不被误伤」会纵容一个永远返回空列表的实现。
  3. **扫到 0 个文件 / 遍历到 0 个对象 = 失败**：在空集合上通过的断言不证明
     任何事，而它看起来和真的通过一模一样。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.enums import (
    ALLOWED_EXTRACTION_METHODS,
    ChainStage,
    CorrectionKind,
    CoverageState,
    DifferenceType,
    DocumentRole,
    EvidenceSourceType,
    ExtractionMethod,
    IdentityStrength,
    MatchMethod,
    MultiplicityState,
    ParseStatus,
    ProjectStatus,
    ReviewStatus,
    Scope,
    SelectionState,
    Severity,
    SubjectKind,
    ValueSource,
    Verdict,
)
from app.domain.models import ProjectSnapshot, SnapshotDocument, SnapshotLineItem
from app.pipeline import ProjectResult, process_document, run_project
from tests.conftest import (
    BASE_GRAND_TOTAL,
    BASE_ITEMS,
    document_input,
    order_rows,
    write_xlsx,
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_APP_DIR = _BACKEND_DIR / "app"
_TOOLS_DIR = _BACKEND_DIR / "tools"
_TESTS_DIR = Path(__file__).resolve().parent

#: 「域内」= 会算出金额/数量并写进 golden 与报告的全部一方代码。
#: `tools/` 一并纳入：fixture 生成器算出的期望值直接进 golden，
#: 它引入二进制浮点等于把误差写进「正确答案」，且威胁 SPEC §16.1
#: 「固定种子二次生成 sha256 必须一致」。
_DOMAIN_ROOTS: tuple[Path, ...] = (_APP_DIR, _TOOLS_DIR)


# ================================================================================
# 通用 AST 扫描工具
# ================================================================================


def _python_files(root: Path) -> Iterator[Path]:
    """递归列出源码文件。跳过 __pycache__（.pyc 不是源码，且内容不可靠）。"""
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: Path) -> str:
    """相对 backend/ 的可读路径，报错信息里给人看的。

    **一律相对**：失败信息里出现 `C:\\Users\\<某人>\\...` 会随开发机漂移，
    既不可复制粘贴，也和 SPEC §12.1 / §15.1「不泄露服务器绝对路径」的口径相反。
    """
    try:
        return path.relative_to(_BACKEND_DIR).as_posix()
    except ValueError:  # pragma: no cover - 扫描根始终在 backend/ 内
        return path.name


def _parse(path: Path) -> ast.Module:
    """解析成 AST。**刻意不做字符串匹配**——注释与文档字符串里出现 `float(`
    或 `import app.db` 是正常的（本文件自己就有），字符串匹配会误伤。

    filename 传相对路径：语法错误时 pytest 会把它原样打进 traceback。
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=_rel(path))


def _flatten(found: Mapping[str, str]) -> list[str]:
    """把 {位置: 说明} 摊平成报错行。

    按位置去重：`from app.db import models` 一条语句会同时命中 `app.db` 与
    `app.db.models`，同一行报两遍只会让人以为有两处违规。
    """
    return [f"{location} {detail}" for location, detail in found.items()]


def _fail_message(rule: str, why: str, violations: Sequence[str]) -> str:
    lines = [
        "",
        f"违反约束：{rule}",
        f"这条约束为什么存在：{why}",
        f"违反位置（共 {len(violations)} 处）：",
    ]
    lines.extend(f"  - {item}" for item in violations)
    return "\n".join(lines)


def _dotted_tail(node: ast.expr) -> str | None:
    """取表达式属性链的末端名。

    pytest.mark.skipif(...) -> 'skipif'；importlib.import_module(...) -> 'import_module'。
    """
    if isinstance(node, ast.Call):
        return _dotted_tail(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _module_parts(path: Path) -> tuple[str, ...]:
    """由文件路径推出所属包的点分段（用于解析相对 import）。

    app/comparison/engine.py   -> ('app', 'comparison')
    app/comparison/__init__.py -> ('app', 'comparison')
    """
    return tuple(path.relative_to(_BACKEND_DIR).parts[:-1])


# ================================================================================
# 1. 硬约束 #7：域内禁止 float
# ================================================================================

#: PDF 页面几何坐标是**唯一**允许的 float 注解，且只允许出现在这些名字上。
#:
#: 理由：SPEC §5.4 把 bbox 写死为归一化 0–1 的页面坐标，它既不是金额也不是数量或
#: 比例，Decimal 铁律的动机（二进制浮点误差污染金额）在这里不成立。
#: **金额 / 数量 / 单价 / 比例语义的任何字段一律不得进入本白名单**——放进来一个，
#: 硬约束 #7 就从「机器保证」退化成「口头约定」。
_FLOAT_ANNOTATION_ALLOWLIST: dict[str, frozenset[str]] = {
    "app/parsers/base.py": frozenset({"bbox", "width", "height"}),
}

_FLOAT_RULE = (
    "硬约束 #7 —— 域内模块禁止出现 `float(`，也禁止二进制浮点字面量；金额/数量/比例全程 Decimal"
)
_FLOAT_WHY = (
    "二进制浮点无法精确表示 0.1，1000 × 1.25 会在报告里显示成 1249.9999999999998，"
    "或者让 quantity × unit_price == line_total 的算术校验产出假 CALCULATION_ERROR。"
    "SPEC §7 / Gate-0 第 6 条要求用 AST 扫描证明域内无 `float(`，"
    "而不是靠 code review 记得住。"
    "字面量同样在内：`Decimal(0.1)` 一个字符也没写 `float(`，"
    "却精确地制造出 0.1000000000000000055511151231257827——"
    "只查 `float(` 的守卫恰好放过最难看出来的那种写法。"
)


def _is_float_conversion_call(node: ast.AST) -> bool:
    """只认 `float(...)` 这种**转换调用**。

    `isinstance(x, float)` 是类型判断不是数值运算，openpyxl 会把数字单元格读成
    int/float，域内必须能对它做 isinstance 分支再转 Decimal——把这种写法一起禁掉，
    唯一的出路反而是绕过判断直接吞掉，那才是真正危险的。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "float"
    # builtins.float(...) / np.float(...) 同样是转换调用
    return isinstance(func, ast.Attribute) and func.attr == "float"


def _annotation_mentions_float(annotation: ast.expr | None) -> bool:
    """注解表达式里是否出现内置 float（含 tuple[float, ...] 这类嵌套）。"""
    if annotation is None:
        return False
    return any(isinstance(sub, ast.Name) and sub.id == "float" for sub in ast.walk(annotation))


def _annotation_target_name(node: ast.AnnAssign) -> str:
    target = node.target
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ast.unparse(target)


def _float_allowlist_for(path: Path) -> frozenset[str]:
    return _FLOAT_ANNOTATION_ALLOWLIST.get(_rel(path), frozenset())


def _scan_float(tree: ast.Module, label: str, allowed: frozenset[str]) -> list[str]:
    """扫一棵 AST 里的 float 违规。**与文件系统解耦**，因此可以直接喂合成样本，
    让「检测器对已知违规样本报警」这件事本身被测试锁住。"""
    violations: list[str] = []
    for node in ast.walk(tree):
        if _is_float_conversion_call(node):
            violations.append(f"{label}:{node.lineno} 调用了 float(...)")

        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            # bool / int / complex 都不是 float，不会走到这里
            violations.append(f"{label}:{node.lineno} 出现二进制浮点字面量 {node.value!r}")

        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg is not None:
                every.append(args.vararg)
            if args.kwarg is not None:
                every.append(args.kwarg)
            for arg in every:
                if _annotation_mentions_float(arg.annotation) and arg.arg not in allowed:
                    violations.append(
                        f"{label}:{arg.lineno} 形参 {node.name}({arg.arg}) 注解为 float"
                    )
            if _annotation_mentions_float(node.returns) and f"{node.name}()" not in allowed:
                violations.append(f"{label}:{node.lineno} 函数 {node.name} 的返回值注解为 float")

        elif isinstance(node, ast.AnnAssign):
            name = _annotation_target_name(node)
            if _annotation_mentions_float(node.annotation) and name not in allowed:
                violations.append(f"{label}:{node.lineno} 字段 {name} 注解为 float")
    return violations


def _float_violations() -> list[str]:
    violations: list[str] = []
    for root in _DOMAIN_ROOTS:
        for path in _python_files(root):
            violations.extend(_scan_float(_parse(path), _rel(path), _float_allowlist_for(path)))
    return violations


class TestNoFloatInDomain:
    """硬约束 #7 / Gate-0 第 6 条。"""

    def test_域内没有任何float转换调用与float注解(self) -> None:
        violations = _float_violations()
        assert not violations, _fail_message(_FLOAT_RULE, _FLOAT_WHY, violations)

    def test_扫描确实覆盖到了域内模块(self) -> None:
        """守卫自身的防退化：扫不到文件的扫描器永远是绿的。"""
        per_root = {_rel(root): len(list(_python_files(root))) for root in _DOMAIN_ROOTS}
        total = sum(per_root.values())
        assert total >= 20 and all(per_root.values()), _fail_message(
            "守卫有效性 —— float 扫描必须真的读到域内源码",
            "扫描 0 个文件的 AST 守卫会永远通过，比没有守卫更危险：它提供虚假的安全感。"
            "任何一个扫描根变空（目录改名、包移动）都必须让这条红掉。",
            [f"各扫描根的文件数（相对 backend/）：{per_root}"],
        )

    def test_isinstance类型判断不被误判为违规(self) -> None:
        """区分「转换调用」与「类型判断」——误杀 isinstance 会逼出更危险的写法。"""
        source = "if isinstance(raw, float):\n    x = Decimal(repr(raw))\n"
        assert not any(_is_float_conversion_call(n) for n in ast.walk(ast.parse(source)))
        assert _scan_float(ast.parse(source), "fake.py", frozenset()) == []

    def test_float转换调用确实会被抓到(self) -> None:
        """守卫自身的防退化：检测器必须对已知的违规样本报警。"""
        tree = ast.parse("total = float(raw) * 2\n")
        assert any(_is_float_conversion_call(n) for n in ast.walk(tree))
        assert len(_scan_float(tree, "fake.py", frozenset())) == 1

    def test_浮点字面量确实会被抓到(self) -> None:
        """守卫自身的防退化：`Decimal(0.1)` 与 `TOLERANCE = 0.01` 一个字符也没写
        `float(`，却正是硬约束 #7 要防的那种误差来源。"""
        violations = _scan_float(
            ast.parse("TOLERANCE = 0.01\nrate = Decimal(0.1)\nscale = 1e-9\n"),
            "fake.py",
            frozenset(),
        )
        assert len(violations) == 3, violations
        # 整数与布尔不得被误判——Decimal(0) / quantize 的指数参数全是整数
        assert _scan_float(ast.parse("n = 0\nflag = True\ncount = 12\n"), "f.py", frozenset()) == []

    def test_白名单只覆盖页面几何且逐名生效(self) -> None:
        """白名单是按「文件 + 字段名」而不是按文件整体豁免的。

        整文件豁免意味着 `parsers/base.py` 里以后加一个 `unit_price: float`
        也不会红——硬约束 #7 会从「机器保证」退化成「口头约定」。
        """
        allowed = _FLOAT_ANNOTATION_ALLOWLIST["app/parsers/base.py"]
        geometry = ast.parse("bbox: tuple[float, float, float, float] | None = None\n")
        money = ast.parse("unit_price: float | None = None\n")
        assert _scan_float(geometry, "app/parsers/base.py", allowed) == []
        assert len(_scan_float(money, "app/parsers/base.py", allowed)) == 1
        # 白名单之外的文件即便同名字段也不豁免
        assert len(_scan_float(geometry, "app/comparison/engine.py", frozenset())) == 1


# ================================================================================
# 2. 硬约束 #8：import 级架构边界
# ================================================================================

#: 这三个包是纯函数层，输入只能是 app.domain.models 的 frozen dataclass。
_BOUNDED_PACKAGES = ("comparison", "matching", "exports")

#: 禁止被它们 import 的包（含任何子模块）。
_FORBIDDEN_IMPORT_ROOT = "app.db"

#: 以字符串取模块的动态写法。只查 `import` 语句会把这两种整段放过。
_DYNAMIC_IMPORT_CALLS = frozenset({"import_module", "__import__"})

_BOUNDARY_RULE = (
    "硬约束 #8 —— app.comparison / app.matching / app.exports 禁止 import app.db（任何子模块）"
)
_BOUNDARY_WHY = (
    "SPEC §11.2：全代码库唯一取值入口是 ValueResolver.snapshot(project_id) -> ProjectSnapshot，"
    "compare(snapshot, rules) 必须是纯函数。比较层一旦能直接读 ORM，"
    "它就能顺手写 ORM，SPEC §3.1 的四类数据写权限互斥立刻失守"
    "（计算产物可整体删除重算，人工裁决一行都不许碰）；"
    "同时比较不再可重放——同一份快照不再必得同一份差异集合，Gate-0 第 15 条确定性随之失效。"
)


def _resolve_import_from(node: ast.ImportFrom, package: tuple[str, ...]) -> str:
    """把 `from ... import ...` 解析成绝对模块名（含相对 import）。"""
    if node.level == 0:
        return node.module or ""
    anchor = package[: len(package) - (node.level - 1)]
    tail = tuple(node.module.split(".")) if node.module else ()
    return ".".join(anchor + tail)


def _imported_targets(tree: ast.Module, package: tuple[str, ...]) -> Iterator[tuple[int, str]]:
    """产出 (行号, 被引用的模块全名)。

    `from app import db` 也必须被识别成引用了 app.db —— 只看 node.module
    会漏掉这一种，而它恰好是最容易写出来的绕过方式。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_from(node, package)
            if base:
                yield node.lineno, base
            for alias in node.names:
                yield node.lineno, f"{base}.{alias.name}" if base else alias.name
        elif isinstance(node, ast.Call) and _dotted_tail(node) in _DYNAMIC_IMPORT_CALLS:
            # importlib.import_module("app.db.models") / __import__("app.db")
            # 是同一件事的动态写法，只查 import 语句会整段放过
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield node.lineno, first.value


def _is_forbidden(module: str) -> bool:
    return module == _FORBIDDEN_IMPORT_ROOT or module.startswith(f"{_FORBIDDEN_IMPORT_ROOT}.")


def _boundary_violations() -> list[str]:
    found: dict[str, str] = {}
    for package in _BOUNDED_PACKAGES:
        root = _APP_DIR / package
        if not root.is_dir():
            continue
        for path in _python_files(root):
            tree = _parse(path)
            parts = _module_parts(path)
            for lineno, module in _imported_targets(tree, parts):
                if _is_forbidden(module):
                    found.setdefault(f"{_rel(path)}:{lineno}", f"import 了 {module}")
    return _flatten(found)


class TestImportBoundary:
    """硬约束 #8 / SPEC §11.2。"""

    def test_比较匹配导出三层不得import数据库层(self) -> None:
        violations = _boundary_violations()
        assert not violations, _fail_message(_BOUNDARY_RULE, _BOUNDARY_WHY, violations)

    def test_三个受约束的包都真实存在(self) -> None:
        """守卫自身的防退化：包被改名后，边界检查会静默地什么都不查。"""
        missing = [pkg for pkg in _BOUNDED_PACKAGES if not (_APP_DIR / pkg).is_dir()]
        assert not missing, _fail_message(
            "守卫有效性 —— 受 import 边界约束的包必须真实存在",
            "包改名或移动后，边界扫描会扫到 0 个文件并永远通过，边界名存实亡。"
            "改名时必须同步更新 _BOUNDED_PACKAGES。",
            [f"app/{pkg} 不存在" for pkg in missing],
        )

    def test_from_app_import_db_形式也会被抓到(self) -> None:
        """`from app import db` 不出现在 node.module 里，是最容易漏掉的绕过方式。"""
        tree = ast.parse("from app import db\n")
        targets = [m for _, m in _imported_targets(tree, ("app", "comparison"))]
        assert any(_is_forbidden(m) for m in targets), targets

    def test_相对import也会被抓到(self) -> None:
        tree = ast.parse("from ..db import models\n")
        targets = [m for _, m in _imported_targets(tree, ("app", "comparison"))]
        assert any(_is_forbidden(m) for m in targets), targets

    def test_动态import也会被抓到(self) -> None:
        """`importlib.import_module("app.db")` 与 `__import__("app.db")` 绕不过去。

        只查 import 语句的边界测试，第一个想绕过它的人会先试这两种写法。
        """
        for source in (
            'importlib.import_module("app.db.models")\n',
            '__import__("app.db")\n',
        ):
            targets = [m for _, m in _imported_targets(ast.parse(source), ("app", "comparison"))]
            assert any(_is_forbidden(m) for m in targets), (source, targets)

    def test_同名前缀的无辜模块不被误伤(self) -> None:
        """app.dbutils 不是 app.db —— 前缀匹配必须按点分段。"""
        assert not _is_forbidden("app.dbutils")
        assert not _is_forbidden("app.domain.models")


# ================================================================================
# 端到端场景（第 3、4 条守卫共用）
# ================================================================================

_TITLES: dict[DocumentRole, tuple[str, str, str]] = {
    DocumentRole.QUOTATION: ("QUOTATION", "Quotation No.", "Q2026-001"),
    DocumentRole.PURCHASE_ORDER: ("PURCHASE ORDER", "PO No.", "PO-8899"),
    DocumentRole.PROFORMA_INVOICE: ("PROFORMA INVOICE", "PI No.", "PI-2026-001"),
}


def _run_pipeline(tmp_path: Path, roles: dict[DocumentRole, dict[str, Any]]) -> ProjectResult:
    """按 tests/test_pipeline_e2e.py 的写法跑一遍纵向闭环。"""
    processed = {}
    for role, overrides in roles.items():
        title, label, number = _TITLES[role]
        rows = order_rows(
            title=title,
            doc_label=label,
            doc_no=number,
            date="2026-07-15",
            items=overrides.get("items", BASE_ITEMS),
            grand_total=overrides.get("grand_total", BASE_GRAND_TOTAL),
            **{k: v for k, v in overrides.items() if k not in ("items", "grand_total")},
        )
        path = write_xlsx(tmp_path / f"{role.value.lower()}.xlsx", {title[:20]: rows})
        processed[role] = process_document(
            document_id=role.value.lower(), role=role, src=document_input(path)
        )
    return run_project("proj-guards", processed)


def _rich_scenario() -> dict[DocumentRole, dict[str, Any]]:
    """刻意让三份文件互不相同，把尽量多的枚举值逼出来。

    只对「三份完全一致」跑守卫是自欺：那条路径产出的差异极少，
    枚举子集断言会在几乎空的集合上通过。
    """
    po_items = [
        # 数量被改：Q->PO 是正常砍价，PO->PI 是致命错误（严重度按 chain_stage 查表）
        ("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"),
        *BASE_ITEMS[1:],
    ]
    pi_items = [
        BASE_ITEMS[0],
        # 单价改了但金额没跟着改 -> 文档内算术错误
        ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1200.00"),
        # 第三行整行缺失 -> UNMATCHED_LINE_ITEM
    ]
    return {
        DocumentRole.QUOTATION: {},
        DocumentRole.PURCHASE_ORDER: {"items": po_items, "grand_total": "3320.00"},
        DocumentRole.PROFORMA_INVOICE: {
            "items": pi_items,
            "grand_total": "2450.00",
            "currency": "EUR",
            "incoterm": "CIF Shanghai, Incoterms 2020",
        },
    }


# ================================================================================
# 3. Gate-0 第 16 条：只产已声明枚举
# ================================================================================

#: Difference 上每个枚举字段 -> 它必须归属的声明全集。
_DIFFERENCE_ENUMS: tuple[tuple[str, Any], ...] = (
    ("scope", Scope),
    ("difference_type", DifferenceType),
    ("severity", Severity),
    ("chain_stage", ChainStage),
    ("subject_kind", SubjectKind),
    ("identity_strength", IdentityStrength),
)

_GROUP_ENUMS: tuple[tuple[str, Any], ...] = (
    ("match_method", MatchMethod),
    ("multiplicity_state", MultiplicityState),
    ("coverage_state", CoverageState),
)

#: 快照层同样会整体落库并进报告：`ValueCell.source` 就是报告上「机器读的／人填的」
#: 那一列（SPEC §9.6），`parse_status` 是 §5.3 的冻结词表。漏了它们，
#: 「运行时产出的**所有**枚举值」只覆盖了差异表。
_DOCUMENT_ENUMS: tuple[tuple[str, Any], ...] = (
    ("role", DocumentRole),
    ("parse_status", ParseStatus),
)
_VALUE_CELL_ENUMS: tuple[tuple[str, Any], ...] = (("source", ValueSource),)

#: 冻结词表。**期望值逐字抄自 SPEC，不是从代码倒推**——
#: 拿 `set(Severity)` 去断言 `set(Severity)` 只能证明「代码等于它自己」，
#: 任何人随手加一个 `Severity.HIGH` 都会被这种测试放行。
#:
#: SPEC §2.1 把这几张词表列为「现在必须定死的形状」，理由是它们会写进 golden、
#: 写进审核记录、写进发给客户的报告——事后无法收回。
_FROZEN_VOCABULARIES: tuple[tuple[str, Any, frozenset[str]], ...] = (
    (
        "difference_type（SPEC §9.1）",
        DifferenceType,
        frozenset(
            {
                "VALUE_CONFLICT",
                "MISSING_VALUE",
                "CALCULATION_ERROR",
                "UNMATCHED_LINE_ITEM",
                "AMBIGUOUS_MATCH",
                "SEMANTIC_DIFFERENCE",
                "EXTRACTION_UNCERTAIN",
                "INCOMPARABLE",
            }
        ),
    ),
    (
        "severity（SPEC §3.2 / §9.4）",
        Severity,
        frozenset({"CRITICAL", "WARNING", "REVIEW", "INFO"}),
    ),
    (
        "chain_stage（SPEC §9.4）",
        ChainStage,
        frozenset(
            {
                "OFFER_TO_ORDER",
                "ORDER_TO_CONFIRMATION",
                "OFFER_TO_CONFIRMATION",
                "WITHIN_DOCUMENT",
            }
        ),
    ),
    (
        "parse_status（SPEC §5.3）",
        ParseStatus,
        frozenset({"PENDING", "OK", "NEEDS_REVIEW", "REJECTED", "FAILED"}),
    ),
    (
        "review_status（SPEC §12.3）",
        ReviewStatus,
        frozenset(
            {
                "OPEN",
                "CONFIRMED_DIFFERENCE",
                "ACCEPTED_DIFFERENCE",
                "NEEDS_CONFIRMATION",
                "IGNORED",
                "RESOLVED",
            }
        ),
    ),
    ("scope（SPEC §3.2）", Scope, frozenset({"DOCUMENT", "LINE_ITEM", "CALCULATION"})),
    ("subject_kind（SPEC §3.2）", SubjectKind, frozenset({"DOCUMENT_ROLE", "MATCH_GROUP"})),
    ("identity_strength（SPEC §3.3）", IdentityStrength, frozenset({"STRONG", "WEAK"})),
    (
        "verdict（SPEC §9.2）",
        Verdict,
        frozenset({"EQUAL", "DIFFERENT", "INCOMPARABLE", "UNCERTAIN", "MISSING"}),
    ),
    (
        "document_role（SPEC §3.2）",
        DocumentRole,
        frozenset({"QUOTATION", "PURCHASE_ORDER", "PROFORMA_INVOICE"}),
    ),
    (
        "match_method（SPEC §3.2）",
        MatchMethod,
        frozenset(
            {
                "SKU_EXACT",
                "CUSTOMER_PART_MAP",
                "FUZZY_CANDIDATE",
                "UNMATCHED",
                "USER_MANUAL",
            }
        ),
    ),
    (
        "multiplicity_state（SPEC §3.2）",
        MultiplicityState,
        frozenset({"UNIQUE_PER_ROLE", "MULTI_PER_ROLE"}),
    ),
    ("coverage_state（SPEC §3.2）", CoverageState, frozenset({"FULL", "PARTIAL", "ISOLATED"})),
    (
        "selection_state（SPEC §3.2）",
        SelectionState,
        frozenset({"AUTO_SELECTED", "CANDIDATE", "USER_SELECTED", "USER_REJECTED"}),
    ),
    (
        "evidence source_type（SPEC §3.2）",
        EvidenceSourceType,
        frozenset({"XLSX_CELL", "XLSX_RANGE", "PDF_TEXT", "DERIVED"}),
    ),
    ("value source（SPEC §9.6）", ValueSource, frozenset({"PARSER", "USER_CORRECTION"})),
    ("correction kind（SPEC §3.2）", CorrectionKind, frozenset({"OVERRIDE", "CONFIRM"})),
    ("project status（SPEC §3.2）", ProjectStatus, frozenset({"DRAFT", "READY", "COMPARED"})),
)

_ENUM_RULE = "Gate-0 第 16 条 —— 运行时产出的所有枚举值必须是 app.domain.enums 声明全集的子集"
_ENUM_WHY = (
    "枚举值会写进 golden、写进数据库、写进发给客户的报告。frozen dataclass **不做运行时类型校验**，"
    '`severity="HIGH"` 这样的裸字符串能一路穿到库里，等到前端「枚举 -> 中文」映射表查不到它时，'
    "用户看到的是一条没有风险等级的差异，或者一条被静默丢弃的差异。"
    "SPEC §2.1 把枚举全集列为「现在必须定死的形状」，正是因为它事后无法收回。"
)


def _enum_violations(owner: str, obj: object, fields: Sequence[tuple[str, Any]]) -> list[str]:
    problems: list[str] = []
    for attr, enum_cls in fields:
        value = getattr(obj, attr)
        declared = {member.value for member in enum_cls}
        if not isinstance(value, enum_cls):
            problems.append(
                f"{owner}.{attr} = {value!r}（{type(value).__name__}）"
                f"不是 {enum_cls.__name__} 的成员"
            )
        elif str(value) not in declared:
            problems.append(
                f"{owner}.{attr} = {value!r} 不在 {enum_cls.__name__} 声明全集内："
                f"{sorted(declared)}"
            )
    return problems


@pytest.mark.enum_subset
class TestOnlyDeclaredEnums:
    """Gate-0 第 16 条（`-m enum_subset`）。"""

    def test_每条差异的枚举字段都在声明全集内(self, tmp_path: Path) -> None:
        result = _run_pipeline(tmp_path, _rich_scenario())
        violations: list[str] = []
        for diff in result.comparison.differences:
            owner = f"Difference[{diff.subject_key}/{diff.field_name or '-'}]"
            violations.extend(_enum_violations(owner, diff, _DIFFERENCE_ENUMS))
        assert not violations, _fail_message(_ENUM_RULE, _ENUM_WHY, violations)

    def test_每条差异的角色字段都在声明全集内(self, tmp_path: Path) -> None:
        """baseline_role / target_role 可以为 None，非 None 时必须是 DocumentRole。"""
        result = _run_pipeline(tmp_path, _rich_scenario())
        violations: list[str] = []
        declared = {member.value for member in DocumentRole}
        for diff in result.comparison.differences:
            for attr in ("baseline_role", "target_role"):
                role = getattr(diff, attr)
                if role is not None and (
                    not isinstance(role, DocumentRole) or str(role) not in declared
                ):
                    violations.append(f"Difference[{diff.subject_key}].{attr} = {role!r}")
            for role_key in diff.values_by_document:
                if role_key not in declared:
                    violations.append(
                        f"Difference[{diff.subject_key}].values_by_document 出现未声明角色键"
                        f" {role_key!r}"
                    )
        assert not violations, _fail_message(_ENUM_RULE, _ENUM_WHY, violations)

    def test_每条证据的来源类型都在声明全集内(self, tmp_path: Path) -> None:
        result = _run_pipeline(tmp_path, _rich_scenario())
        violations: list[str] = []
        for evidence in result.evidence.values():
            violations.extend(
                _enum_violations(
                    f"Evidence[{evidence.evidence_id}]",
                    evidence,
                    (("source_type", EvidenceSourceType), ("role", DocumentRole)),
                )
            )
        assert not violations, _fail_message(_ENUM_RULE, _ENUM_WHY, violations)

    def test_每个匹配组的枚举字段都在声明全集内(self, tmp_path: Path) -> None:
        result = _run_pipeline(tmp_path, _rich_scenario())
        violations: list[str] = []
        for group in result.groups:
            violations.extend(
                _enum_violations(f"MatchGroup[{group.group_key}]", group, _GROUP_ENUMS)
            )
            for member in group.members:
                violations.extend(
                    _enum_violations(
                        f"MatchMember[{group.group_key}/{member.line_key}]",
                        member,
                        (("role", DocumentRole),),
                    )
                )
        assert not violations, _fail_message(_ENUM_RULE, _ENUM_WHY, violations)

    def test_快照层的枚举字段都在声明全集内(self, tmp_path: Path) -> None:
        """`ValueCell.source` 是报告上「机器读的／人填的」那一列（SPEC §9.6），
        `parse_status` 是 §5.3 冻结词表——两者都整体进库进报告，
        只检查差异表等于把 Gate-0 第 16 条查了一半。"""
        result = _run_pipeline(tmp_path, _rich_scenario())
        violations: list[str] = []
        checked = 0
        for role, document in result.snapshot.documents.items():
            violations.extend(
                _enum_violations(f"Snapshot[{role.value}]", document, _DOCUMENT_ENUMS)
            )
            for key, cell in document.fields.items():
                checked += 1
                violations.extend(
                    _enum_violations(
                        f"Snapshot[{role.value}].fields[{key}]", cell, _VALUE_CELL_ENUMS
                    )
                )
            for item in document.line_items:
                violations.extend(
                    _enum_violations(
                        f"Snapshot[{role.value}].{item.line_key}", item, (("role", DocumentRole),)
                    )
                )
                for key, cell in item.fields.items():
                    checked += 1
                    violations.extend(
                        _enum_violations(
                            f"Snapshot[{role.value}].{item.line_key}[{key}]",
                            cell,
                            _VALUE_CELL_ENUMS,
                        )
                    )
        for diff in result.comparison.differences:
            for role_key, cell in diff.values_by_document.items():
                checked += 1
                violations.extend(
                    _enum_violations(
                        f"Difference[{diff.subject_key}].values[{role_key}]",
                        cell,
                        _VALUE_CELL_ENUMS,
                    )
                )
        assert not violations, _fail_message(_ENUM_RULE, _ENUM_WHY, violations)
        assert checked >= 50, _fail_message(
            "守卫有效性 —— 快照层枚举检查必须真的走到 ValueCell",
            "遍历到 0 个 ValueCell 时断言恒真。链路结构变化必须让这条红掉，"
            "而不是让它安静地什么都不查。",
            [f"只检查了 {checked} 个 ValueCell"],
        )

    def test_冻结词表与SPEC逐字一致(self) -> None:
        """Gate-0 第 16 条的另一半：运行时 ⊆ 声明，声明 == SPEC。

        只查前一半是自证：加一个 `Severity.HIGH` 之后运行时产出它照样「合法」，
        而前端「枚举 -> 中文」映射表查不到它，用户看到的是一条没有风险等级的差异。
        """
        violations: list[str] = []
        for label, enum_cls, expected in _FROZEN_VOCABULARIES:
            declared = {member.value for member in enum_cls}
            if declared != expected:
                violations.append(
                    f"{label}：代码声明 {sorted(declared)}，SPEC 冻结为 {sorted(expected)}"
                    f"（多出 {sorted(declared - expected)}，缺少 {sorted(expected - declared)}）"
                )
        assert not violations, _fail_message(
            "SPEC §2.1 —— 会写进 golden 与审核记录的枚举全集已冻结",
            "枚举值一旦进过 golden / 审核记录 / 发出去的报告就无法收回。"
            "改词表必须是一次显式的规格修订（并按 SPEC §9.4 的先例写进 "
            "docs/validation-report.md），不能是一次顺手的 commit。",
            violations,
        )

    def test_裸字符串枚举值确实会被抓到(self) -> None:
        """守卫自身的防退化。

        frozen dataclass **不做运行时类型校验**，`severity="HIGH"` 能一路穿到库里。
        所以检查器必须同时管住两件事：值不在声明全集内，以及值根本不是枚举成员。
        """
        fake = SimpleNamespace(scope="LINE_ITEM", difference_type="TYPO")
        problems = _enum_violations(
            "Fake", fake, (("scope", Scope), ("difference_type", DifferenceType))
        )
        assert len(problems) == 2, problems

    def test_枚举断言不是在空集合上通过的(self, tmp_path: Path) -> None:
        """守卫自身的防退化。

        「零差异」也能让上面几条全绿——那样的绿是假的。

        期望值**由植入意图推出，不是把实现跑一遍抄回来的**：
          - PI 少了整整一行 AB-300            -> 必须有 UNMATCHED_LINE_ITEM
          - PI 的 AB-200 改了单价没改金额      -> 必须有 CALCULATION_ERROR
            （SPEC §9.7：算术校验挂在单份文档上，永不被匹配状态阻断）
          - PO 把 AB-100 的数量从 1000 改成 1200 -> 必须有 VALUE_CONFLICT
          - PI 记 EUR 而 Q/PO 记 USD           -> 金额类字段必须是 INCOMPARABLE
            （SPEC §9.1：币种混合结构上无法比较，坍缩成 VALUE_CONFLICT 就是假警报）
        """
        result = _run_pipeline(tmp_path, _rich_scenario())
        differences = result.comparison.differences
        kinds = {d.difference_type for d in differences}
        severities = {d.severity for d in differences}
        required = {
            DifferenceType.UNMATCHED_LINE_ITEM,
            DifferenceType.CALCULATION_ERROR,
            DifferenceType.VALUE_CONFLICT,
            DifferenceType.INCOMPARABLE,
        }
        missing = sorted(k.value for k in required - kinds)
        assert not missing and len(severities) >= 2, _fail_message(
            "守卫有效性 —— 枚举子集断言必须跑在非空且覆盖全部植入缺陷的产出上",
            "在空集合上通过的断言不证明任何事。植入的缺陷若没有被全部检出，"
            "是流水线退化（漏报），不是「没有差异」这个好消息。",
            [
                f"植入缺陷未被检出：{missing}" if missing else "植入缺陷全部检出",
                f"实际产出：差异 {len(differences)} 条，"
                f"类型 {sorted(k.value for k in kinds)}，"
                f"严重度 {sorted(s.value for s in severities)}",
            ],
        )


# ================================================================================
# 4. SPEC §14：extraction_method 白名单
# ================================================================================

_METHOD_RULE = "SPEC §14 —— extraction_method 只能是 alias / layout / user_confirmed"
_METHOD_WHY = (
    "这是「LLM 没有偷偷变成必需依赖」的机器证明。MVP-0 不实现 LLM 字段映射（SPEC §6.4），"
    "而 LLM 适配器按设计只能「选」不能「写」（只返回已有 block_id，凭空造值在结构上不可能）。"
    "一旦白名单外的提取方法出现在产出链路上，就意味着有值不是从确定性 normalizer 来的："
    "它没有 Evidence 可追溯，且断网即失效——本工具「带原文证据的差异清单」这一核心承诺随之作废。"
)


def _assigned_names(node: ast.AnnAssign | ast.Assign) -> set[str]:
    targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
    return {
        target.id if isinstance(target, ast.Name) else target.attr
        for target in targets
        if isinstance(target, ast.Name | ast.Attribute)
    }


def _extraction_method_literals(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """产出 (行号, 字面量)：所有写死给 extraction_method 的字符串。

    三种写法都要认，因为它们的效果完全相同：
      1. 关键字实参    `ExtractedCell(..., extraction_method="llm")`
      2. 赋值          `cell.extraction_method = "llm"`
      3. **字典键**    `parser_metadata={"extraction_method": "llm"}`

    第 3 种是最该认的一种：`Evidence.parser_metadata` 是 `Mapping[str, str]`，
    一个不受约束的自由字典，是把非确定性来源夹带进产出链路最省事的路径——
    运行时那条守卫专门盯着它，源码这条却漏掉它，等于把闸门修在了下游。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                bound = keyword.value
                if (
                    keyword.arg == "extraction_method"
                    and isinstance(bound, ast.Constant)
                    and isinstance(bound.value, str)
                ):
                    yield node.lineno, bound.value
        elif isinstance(node, ast.Dict):
            for key, bound in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "extraction_method"
                    and isinstance(bound, ast.Constant)
                    and isinstance(bound.value, str)
                ):
                    yield key.lineno, bound.value
        elif isinstance(node, ast.AnnAssign | ast.Assign):
            assigned = node.value
            if (
                assigned is not None
                and isinstance(assigned, ast.Constant)
                and isinstance(assigned.value, str)
                and "extraction_method" in _assigned_names(node)
            ):
                yield node.lineno, assigned.value


def _collect_extraction_methods(result: ProjectResult) -> tuple[list[tuple[str, object]], int]:
    """遍历产出链路，收集所有 extraction_method 取值，并返回被检查的对象数。

    按属性名收集而非按类型收集：MVP-0 的 ExtractedCell / ValueCell 尚未携带
    extraction_method 字段，本函数因此现在收不到任何值。**这正是它要长期生效的形态**
    —— 等哪天有人给它们加上这个字段（或往 parser_metadata 里塞一个），
    守卫立即开始检查，不需要有人记得回来改测试。
    """
    observations: list[tuple[str, object]] = []
    inspected = 0

    def note(where: str, obj: object) -> None:
        nonlocal inspected
        inspected += 1
        method = getattr(obj, "extraction_method", None)
        if method is not None:
            observations.append((where, method))
        metadata = getattr(obj, "parser_metadata", None)
        if isinstance(metadata, Mapping) and "extraction_method" in metadata:
            observations.append((f"{where}.parser_metadata", metadata["extraction_method"]))

    # ① 提取层：ExtractedDocField / ExtractedCell
    for role, processing in result.processing.items():
        for key, doc_field in processing.doc_fields.fields.items():
            note(f"{role.value}.doc_fields[{key}]", doc_field)
        for item in processing.line_items.items:
            for key, cell in item.cells.items():
                note(f"{role.value}.{item.line_key}.cells[{key}]", cell)

    # ② 快照层：ValueCell
    for role, document in result.snapshot.documents.items():
        for key, value_cell in document.fields.items():
            note(f"{role.value}.snapshot.fields[{key}]", value_cell)
        for item in document.line_items:
            for key, value_cell in item.fields.items():
                note(f"{role.value}.snapshot.{item.line_key}[{key}]", value_cell)

    # ③ 证据层：Evidence 的 parser_metadata 是最容易夹带的自由字典
    for evidence in result.evidence.values():
        note(f"Evidence[{evidence.evidence_id}]", evidence)

    # ④ 差异层：values_by_document 里的 ValueCell
    for diff in result.comparison.differences:
        for role_key, value_cell in diff.values_by_document.items():
            note(f"Difference[{diff.subject_key}].values[{role_key}]", value_cell)

    return observations, inspected


class TestExtractionMethodWhitelist:
    """SPEC §14 / §6.3。MVP-0 没有 LLM，这条守卫的作用是**防回归**。"""

    def test_声明的提取方法全集不得超出白名单(self, tmp_path: Path) -> None:
        """声明层的闸门：新增一个 ExtractionMethod 成员必须同步做出显式决定。

        没有这一条，未来加一个 `LLM = "llm"` 枚举成员不会触发任何测试失败，
        白名单会在无人察觉时变成一张只覆盖历史值的空头支票。
        """
        declared = {member.value for member in ExtractionMethod}
        allowed = {str(method) for method in ALLOWED_EXTRACTION_METHODS}
        extra = sorted(declared - allowed)
        assert not extra, _fail_message(
            _METHOD_RULE,
            _METHOD_WHY,
            [
                f"ExtractionMethod 声明了白名单外的成员：{extra}。"
                "若确属 MVP 范围内的确定性提取方法，请同步更新 ALLOWED_EXTRACTION_METHODS；"
                "若是 LLM 或任何非确定性来源，它不属于 MVP-0。"
            ],
        )
        assert allowed == {"alias", "layout", "user_confirmed"}, _fail_message(
            _METHOD_RULE,
            _METHOD_WHY,
            [f"白名单被改成了 {sorted(allowed)}，与 SPEC §14 / SPEC §3.2 表定义不一致"],
        )

    def test_源码里没有白名单外的extraction_method字面量(self) -> None:
        """源码层的闸门：`extraction_method="llm"` 写在任何地方都会被抓到。

        用 AST 找形如 `extraction_method=<字符串字面量>` 的关键字实参与赋值，
        比运行时遍历更早、更全——它连本次场景没走到的分支也覆盖。
        """
        allowed = {str(method) for method in ALLOWED_EXTRACTION_METHODS}
        violations = [
            f"{_rel(path)}:{lineno} extraction_method = {literal!r}"
            for path in _python_files(_APP_DIR)
            for lineno, literal in _extraction_method_literals(_parse(path))
            if literal not in allowed
        ]
        assert not violations, _fail_message(_METHOD_RULE, _METHOD_WHY, violations)

    def test_白名单外的字面量确实会被抓到(self) -> None:
        """守卫自身的防退化：检测器必须对已知的违规样本报警。

        三种写法一个都不能漏——尤其是塞进 `parser_metadata` 自由字典的那种。
        """
        tree = ast.parse(
            "cell = ExtractedCell(field_key='x', extraction_method='llm')\n"
            "other = ValueCell()\n"
            "other.extraction_method = 'llm'\n"
            "ev = EvidenceDraft(parser_metadata={'extraction_method': 'llm'})\n"
        )
        assert sorted(literal for _, literal in _extraction_method_literals(tree)) == [
            "llm",
            "llm",
            "llm",
        ]

    def test_合法的提取方法字面量不被误伤(self) -> None:
        """白名单内的值必须放行，否则实现者会把守卫本身删掉。"""
        tree = ast.parse(
            "cell = ExtractedCell(extraction_method='alias')\n"
            "meta = {'extraction_method': 'user_confirmed'}\n"
        )
        allowed = {str(method) for method in ALLOWED_EXTRACTION_METHODS}
        assert all(lit in allowed for _, lit in _extraction_method_literals(tree))

    def test_产出链路上没有白名单外的提取方法(self, tmp_path: Path) -> None:
        """运行时的闸门：真跑一遍全链路，检查实际产出的每个取值。

        **诚实说明**：MVP-0 的产出对象尚不携带 `extraction_method`
        （SPEC §3.2 的 `extracted_field` / `line_item` 两张表还没建），
        所以这一层此刻观测到 0 个取值——**承重的是上面两条声明层与源码层**。
        本条的作用是「哪天有人加上这个字段即刻生效」，
        以及用 inspected 计数证明遍历确实走到了对象上，而不是走了个空。
        """
        result = _run_pipeline(tmp_path, _rich_scenario())
        observations, inspected = _collect_extraction_methods(result)
        allowed = {str(method) for method in ALLOWED_EXTRACTION_METHODS}
        violations = [
            f"{where} = {value!r}" for where, value in observations if str(value) not in allowed
        ]
        assert not violations, _fail_message(_METHOD_RULE, _METHOD_WHY, violations)

        # 防退化：遍历必须真的走到了对象上。inspected == 0 时上面的断言毫无意义。
        assert inspected >= 50, _fail_message(
            "守卫有效性 —— extraction_method 遍历必须真的走到产出对象",
            "遍历到 0 个对象时，白名单断言恒真。链路结构变化（字段改名、层级下沉）"
            "必须让这条守卫红掉，而不是让它安静地什么都不查。",
            [
                f"只检查了 {inspected} 个对象"
                f"（其中携带 extraction_method 的：{len(observations)} 个）"
            ],
        )


# ================================================================================
# 5. 硬约束 #16：零 skip
# ================================================================================

#: 会让 pytest 输出出现 skip 的标记名。`mvp1` 不在其列——那是**反选**不是 skip：
#: 反选的测试会被 `-m 'not mvp1'` 整体排除并单独计数打印进 validation-report，
#: 而 skip 是「跑了但没跑」，混在绿色里没人看。
_SKIP_MARKER_NAMES = frozenset({"skip", "skipif"})

#: 运行期跳过：效果与 skip 标记完全相同，只是写在函数体里。
_SKIP_CALL_NAMES = frozenset({"skip", "importorskip"})

_SKIP_RULE = "硬约束 #16 —— pytest 输出零 skip（MVP-1 测试用 @pytest.mark.mvp1 反选）"
_SKIP_WHY = (
    "skip 是「可合法停止」的两张门表唯一的滥用出口（SPEC §2.4 第 15–17 条）。"
    "一条被 skip 的测试在输出里是灰色的一行，读者会自动把它归入「通过」；"
    "而反选是显式的：`-m 'not mvp1'` 会整体排除并要求把反选数量打印进 validation-report，"
    "任何人都能一眼看出「有多少东西这次没验」。"
)


def _marks_of(node: ast.expr | None) -> list[ast.expr]:
    """把 `pytest.mark.x` / `[a, b]` / `(a, b)` 统一成标记列表。"""
    if node is None:
        return []
    return list(node.elts) if isinstance(node, ast.List | ast.Tuple) else [node]


def _skip_violations_in(tree: ast.Module, label: str) -> dict[str, str]:
    """扫一棵 AST 里的 skip。与文件系统解耦，方便对合成样本做对照测试。"""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                tail = _dotted_tail(decorator)
                if tail in _SKIP_MARKER_NAMES:
                    found.setdefault(f"{label}:{decorator.lineno}", f"{node.name} 带 {tail} 装饰器")
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            # `pytestmark: list[MarkDecorator] = [...]` 与不带注解的写法效果完全相同，
            # 只认 ast.Assign 会整段放过带注解的那种。
            if "pytestmark" not in _assigned_names(node):
                continue
            for mark in _marks_of(node.value):
                tail = _dotted_tail(mark)
                if tail in _SKIP_MARKER_NAMES:
                    found.setdefault(f"{label}:{node.lineno}", f"模块级 pytestmark 使用了 {tail}")
        elif isinstance(node, ast.Call):
            tail = _dotted_tail(node)
            if tail in _SKIP_CALL_NAMES:
                found.setdefault(f"{label}:{node.lineno}", f"调用了运行期跳过 {tail}()")
            for keyword in node.keywords:
                # pytest.param(..., marks=pytest.mark.skip) —— 单条参数被跳过时，
                # 输出里同样是一行灰色的 s，且比整个函数被 skip 更难看见。
                if keyword.arg != "marks":
                    continue
                for mark in _marks_of(keyword.value):
                    mark_tail = _dotted_tail(mark)
                    if mark_tail in _SKIP_MARKER_NAMES:
                        found.setdefault(
                            f"{label}:{keyword.value.lineno}",
                            f"参数化条目的 marks= 使用了 {mark_tail}",
                        )
    return found


def _skip_violations() -> list[str]:
    found: dict[str, str] = {}
    for path in _python_files(_TESTS_DIR):
        found.update(_skip_violations_in(_parse(path), _rel(path)))
    return _flatten(found)


class TestNoSkippedTests:
    """硬约束 #16 / Gate-0 第 17 条。"""

    def test_整个测试目录没有任何skip(self) -> None:
        violations = _skip_violations()
        assert not violations, _fail_message(_SKIP_RULE, _SKIP_WHY, violations)

    def test_mvp1反选标记是被允许的(self) -> None:
        """mvp1 是反选不是 skip，必须明确不被误伤，否则实现者会退回用 skip。"""
        tree = ast.parse("@pytest.mark.mvp1\ndef test_x() -> None:\n    pass\n")
        tails = [
            _dotted_tail(d)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            for d in n.decorator_list
        ]
        assert tails == ["mvp1"]
        assert not any(tail in _SKIP_MARKER_NAMES for tail in tails)

    def test_skipif装饰器确实会被抓到(self) -> None:
        """守卫自身的防退化：检测器必须对已知的违规样本报警。"""
        tree = ast.parse('@pytest.mark.skipif(True, reason="x")\ndef test_y() -> None:\n    pass\n')
        tails = [
            _dotted_tail(d)
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            for d in n.decorator_list
        ]
        assert any(tail in _SKIP_MARKER_NAMES for tail in tails), tails
        assert _skip_violations_in(tree, "fake.py")

    def test_四种skip写法一个都不漏(self) -> None:
        """装饰器只是最显眼的一种。另外三种写法效果完全相同，
        而其中两种恰好是第一个想绕过这条守卫的人会先试的：

          - 模块级 `pytestmark`（**含带类型注解的写法**）
          - 运行期 `pytest.skip()` / `pytest.importorskip()`
          - `pytest.param(..., marks=pytest.mark.skip)`：只跳一条参数，
            输出里同样是一行灰色的 s，比整个函数被 skip 更难看见
        """
        samples = {
            "装饰器": "@pytest.mark.skip\ndef test_a() -> None:\n    pass\n",
            "pytestmark 赋值": "pytestmark = pytest.mark.skip\n",
            "pytestmark 带注解": (
                "pytestmark: list[object] = [pytest.mark.skipif(True, reason='x')]\n"
            ),
            "运行期 skip": "def test_b() -> None:\n    pytest.skip('x')\n",
            "运行期 importorskip": "mod = pytest.importorskip('numpy')\n",
            "参数化 marks": (
                "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.skip)])\n"
                "def test_c(x: int) -> None:\n    pass\n"
            ),
        }
        missed = [
            name
            for name, source in samples.items()
            if not _skip_violations_in(ast.parse(source), "fake.py")
        ]
        assert not missed, _fail_message(
            "守卫有效性 —— 零 skip 扫描必须认全 skip 的所有等价写法",
            "只认装饰器的守卫，第一个想绕过它的人五分钟内就能绕过去，"
            "而输出里的灰色一行仍然会被读者归入「通过」。",
            [f"未被识别的写法：{name}" for name in missed],
        )

    def test_正常测试写法不被误伤(self) -> None:
        """误报会让实施者删掉守卫本身，所以对照面同样要锁住。"""
        benign = (
            "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.mvp1)])\n"
            "def test_ok(x: int) -> None:\n    pass\n"
            "pytestmark = [pytest.mark.enum_subset]\n"
        )
        assert _skip_violations_in(ast.parse(benign), "fake.py") == {}

    def test_扫描确实覆盖到了测试目录(self) -> None:
        scanned = list(_python_files(_TESTS_DIR))
        assert len(scanned) >= 5, _fail_message(
            "守卫有效性 —— 零 skip 扫描必须真的读到 tests/ 下的源码",
            "扫描 0 个文件的守卫永远是绿的，正好把它要防的东西放行。",
            [f"扫描根 {_rel(_TESTS_DIR)}/ 下只扫描到 {len(scanned)} 个文件"],
        )


class TestPartitionGuardBites:
    """Gate-0 第 7 条：未对齐行不被强行匹配、也不被静默丢弃。

    `assert_partition` 在 `compare()` 里逐次调用，所以全仓库每一次比较（含 135 条
    golden）都在跑它——覆盖面已经够了。**缺的是证明这个守卫本身会咬人**：
    把它改成 `pass` 的那次改动，现有测试一条都不会红，而它保护的恰好是
    「不为提高匹配率牺牲误匹配率」这条产品底线。

    三个方向分别锁死一种失效：丢行（漏报）、重复归组（一行被算两次）、
    引用不存在的行（组里凭空多出成员）。
    """

    @staticmethod
    def _snapshot_and_groups() -> tuple[Any, tuple[Any, ...]]:
        from app.matching.engine import match_line_items

        role = DocumentRole.PURCHASE_ORDER
        items = tuple(
            SnapshotLineItem(
                line_key=f"sku:AB-{n}#1",
                document_id="doc-po",
                role=role,
                row_index=10 + n,
                sheet_name="S",
                sku_norm=f"AB-{n}",
                customer_part_norm=None,
                unit_norm="PCS",
                currency="USD",
                fields={},
            )
            for n in (100, 200)
        )
        document = SnapshotDocument(
            document_id="doc-po",
            role=role,
            original_filename="po.xlsx",
            parse_status=ParseStatus.OK,
            currency="USD",
            fields={},
            line_items=items,
            evidence={},
        )
        other = SnapshotDocument(
            document_id="doc-pi",
            role=DocumentRole.PROFORMA_INVOICE,
            original_filename="pi.xlsx",
            parse_status=ParseStatus.OK,
            currency="USD",
            fields={},
            line_items=(),
            evidence={},
        )
        snapshot = ProjectSnapshot(
            project_id="p", documents={role: document, DocumentRole.PROFORMA_INVOICE: other}
        )
        return snapshot, match_line_items(snapshot)

    def test_正常结果通过分区检查(self) -> None:
        """先锁住反面：一个永远抛异常的守卫同样毫无价值。"""
        from app.matching.engine import assert_partition

        snapshot, groups = self._snapshot_and_groups()
        assert groups, "样本没有产出任何匹配组，后面的断言会在空集合上通过"
        assert_partition(snapshot, groups)

    def test_丢掉一行会被抓到(self) -> None:
        from app.matching.engine import assert_partition

        snapshot, groups = self._snapshot_and_groups()
        with pytest.raises(AssertionError, match="静默丢弃"):
            assert_partition(snapshot, groups[:-1])

    def test_一行同时属于两个组会被抓到(self) -> None:
        from app.matching.engine import assert_partition

        snapshot, groups = self._snapshot_and_groups()
        with pytest.raises(AssertionError, match="多个匹配组"):
            assert_partition(snapshot, (*groups, groups[0]))

    def test_组里引用不存在的行会被抓到(self) -> None:
        import dataclasses

        from app.matching.engine import assert_partition

        snapshot, groups = self._snapshot_and_groups()
        ghost = dataclasses.replace(
            groups[0],
            members=(
                *groups[0].members,
                dataclasses.replace(groups[0].members[0], line_item_id="doc-po::sku:GHOST#1"),
            ),
        )
        with pytest.raises(AssertionError, match="不存在的行"):
            assert_partition(snapshot, (ghost, *groups[1:]))


class TestDisclaimerIsVerbatim:
    """SPEC §18 第 14 条：`limitations.md` 必须逐字包含 §16.5 自产 fixture 免责声明。

    这条硬约束原本**没有任何机械检查**——而它保护的正是「不得对外宣称准确率」这个
    最容易在交付压力下被稀释的承诺。三处副本（SPEC / limitations / 会被贴进交付报告的
    `test_golden.DISCLAIMER`）各自漂移过：前两处曾一起指向空目录 `fixtures/generators`。
    """

    _REPO_ROOT = _BACKEND_DIR.parent

    def _extract(self, path: str, marker: str) -> str:
        text = (self._REPO_ROOT / path).read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines() if marker in ln]
        assert len(hits) == 1, f"{path} 里 {marker!r} 命中 {len(hits)} 次，期望 1 次"
        return hits[0].lstrip("> ").strip()

    def test_限制文档逐字引用规格里的免责声明(self) -> None:
        marker = "程序化自产，生成器与提取器共享同一套别名表与列布局假设"
        spec = self._extract("docs/SPEC.md", marker)
        limitations = self._extract("docs/limitations.md", marker)
        assert limitations == spec, (
            "limitations.md 的免责声明与 SPEC §16.5 不再逐字一致（SPEC §18 第 14 条）。\n"
            f"SPEC        : {spec}\nlimitations : {limitations}"
        )
        assert "不代表对真实客户文件的提取准确率" in spec

    def test_免责声明指向的生成器真的存在(self) -> None:
        """指向一个不存在的路径，等于让读者无法验证「确由程序自产」这个前提。"""
        marker = "程序化自产，生成器与提取器共享同一套别名表与列布局假设"
        spec = self._extract("docs/SPEC.md", marker)
        quoted = re.findall(r"`([^`]+)`", spec)
        assert quoted, f"免责声明里没有反引号路径：{spec}"
        for path in quoted:
            assert (self._REPO_ROOT / path).exists(), f"免责声明指向的 {path} 不存在于仓库中"

    def test_交付报告里的免责声明与规格同义(self) -> None:
        """`golden-report.md` 会被整段贴进交付报告，它那份不能自成一派。"""
        from tests.test_golden import DISCLAIMER

        for phrase in (
            "程序化自产",
            "生成器与提取器共享同一套别名表与列布局假设",
            "不代表对真实客户文件的提取准确率",
        ):
            assert phrase in DISCLAIMER, f"golden-report 的免责声明缺少「{phrase}」"
