"""打印「零 skip」与「MVP-1 反选数量」。硬约束 #14。

`pyproject.toml` 的 `addopts` 里挂了 `-m 'not mvp1'`，所以 `pytest -q` 的
「930 passed」是**反选之后**的数字。反选数量不打印出来，读报告的人无从判断
那 930 条是全部，还是有一批被悄悄摘掉了——而「减少测试来制造通过结果」
恰好是 CLAUDE.md 明令禁止的做法。

计数走 pytest 自己的收集器（不做 AST 猜测）：AST 扫描认不出
`pytestmark = pytest.mark.mvp1` 之类的写法，一旦漏认就会把「有 40 条被反选」
报成「一条都没反选」——错的方向恰好是让人放心的那个方向。

**本工具不判定通过与否**，只负责把数字摆出来；零 skip 的强制在
`tests/test_guards.py::TestNoSkippedTests`。退出码非 0 只表示计数本身没跑成。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: `-p no:cacheprovider`：不写 .pytest_cache，验证脚本跑完不留痕迹。
_BASE = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]


def _collect(marker: str) -> tuple[int, list[str]]:
    """按 marker 表达式收集，返回 (条数, 节点 id 列表)。

    `--collect-only -q` 每行一个节点 id，末尾跟一个空行 + 汇总行。
    一条都收不到时 pytest 退出码是 5，这里**不当成失败**——
    「MVP-1 一条都没有」是 MVP-0 交付时完全正常的结果。
    """
    proc = subprocess.run(
        [*_BASE, "-m", marker],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 5):
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"收集失败（marker={marker!r}，退出码 {proc.returncode}）")
    ids = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
    return len(ids), ids


def main() -> int:
    selected, _ = _collect("not mvp1")
    deselected, deselected_ids = _collect("mvp1")
    everything, _ = _collect("mvp1 or not mvp1")

    print(f"本轮实际执行（Gate-0 口径）: {selected}")
    print(f"标记 mvp1 被反选           : {deselected}")
    print(f"仓库内测试总数             : {everything}")

    if selected + deselected != everything:
        # 两个互补的 marker 表达式加起来对不上总数 = 计数口径有问题，
        # 这时候报出来的「反选 0 条」不可信，必须显式失败。
        print(
            f"计数不自洽：{selected} + {deselected} != {everything}",
            file=sys.stderr,
        )
        return 1

    if deselected_ids:
        print()
        print("被反选的用例（MVP-1 范围，Gate-0 不执行）：")
        for node_id in sorted(deselected_ids):
            print(f"  - {node_id}")
    else:
        print()
        print("没有任何用例被反选——本仓库当前不含 MVP-1 标记的测试。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
