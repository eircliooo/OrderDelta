"""生成由代码派生的文档。硬约束 #12。

`docs/comparison-rules.md` 由 `app.domain.fields` 的 FieldSpec 注册表生成，
**禁止手写**。生成后 `git diff` 必须为空，否则说明有人手写过或注册表被改了没重新生成。

用法（在 backend/ 下）：
    python -m tools.gen_docs           写入
    python -m tools.gen_docs --check   只校验是否已同步（CI/verify 用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.domain.fields import render_comparison_rules_md

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPARISON_RULES = REPO_ROOT / "docs" / "comparison-rules.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验文件是否与注册表同步，不写入。不同步返回退出码 1。",
    )
    args = parser.parse_args(argv)

    expected = render_comparison_rules_md()

    if args.check:
        if not COMPARISON_RULES.exists():
            print(f"缺失：{COMPARISON_RULES}，请运行 python -m tools.gen_docs")
            return 1
        actual = COMPARISON_RULES.read_text(encoding="utf-8")
        if actual != expected:
            print(
                f"不同步：{COMPARISON_RULES} 与 FieldSpec 注册表不一致。\n"
                "请运行 python -m tools.gen_docs 重新生成（禁止手写）。"
            )
            return 1
        print(f"已同步：{COMPARISON_RULES.name}")
        return 0

    COMPARISON_RULES.parent.mkdir(parents=True, exist_ok=True)
    COMPARISON_RULES.write_text(expected, encoding="utf-8")
    print(f"已生成：{COMPARISON_RULES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
