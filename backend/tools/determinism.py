"""跨进程确定性检查。Gate-0 第 15 条：连跑 3 次，差异集合与全部 fingerprint 逐字节一致。

**为什么必须跨进程、且必须换 `PYTHONHASHSEED`：**
同一进程内跑三次，`set` / `dict` 的迭代顺序是同一个种子下的同一个顺序 —— 三次当然一样。
真正的失效是「某处用了未排序的 set 参与输出」，它只在**换了哈希种子的新进程**里才暴露。
一次跑通、换台机器跑出另一份报告，正是这类 bug 的典型症状；而对一个自称
「同样的文件永远给同样答案」的核对工具，这就是产品承诺本身失守。

比对对象是**完整的差异集合序列化结果**（含顺序、difference_key、values_digest、
证据 id、说明参数），不是「条数一致」这种抓不住问题的弱断言。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: 刻意选互不相同、且与默认值不同的种子。
SEEDS = ("0", "1", "524287")

_CHILD = r"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from app.domain.enums import DocumentRole
from app.pipeline import process_document, run_project
from tests.conftest import BASE_ITEMS, document_input, order_rows, write_xlsx

TITLES = {
    DocumentRole.QUOTATION: ("QUOTATION", "Quotation No.", "Q2026-001"),
    DocumentRole.PURCHASE_ORDER: ("PURCHASE ORDER", "PO No.", "PO-8899"),
    DocumentRole.PROFORMA_INVOICE: ("PROFORMA INVOICE", "PI No.", "PI-2026-001"),
}
PI_ITEMS = [
    BASE_ITEMS[0],
    ("AB-200", "Ceramic Plate 8in", 500, "PCS", "2.50", "1200.00"),
    BASE_ITEMS[2],
]
PO_ITEMS = [("AB-100", "Ceramic Mug 350ml", 1200, "PCS", "1.25", "1500.00"), *BASE_ITEMS[1:]]
PLAN = {
    DocumentRole.QUOTATION: (BASE_ITEMS, "3070.00"),
    DocumentRole.PURCHASE_ORDER: (PO_ITEMS, "3320.00"),
    DocumentRole.PROFORMA_INVOICE: (PI_ITEMS, "3070.00"),
}

work = Path(tempfile.mkdtemp())
processed = {}
for role, (items, total) in PLAN.items():
    title, label, number = TITLES[role]
    rows = order_rows(
        title=title, doc_label=label, doc_no=number, date="2026-07-15",
        items=items, grand_total=total,
    )
    path = write_xlsx(work / (role.value.lower() + ".xlsx"), {title[:20]: rows})
    processed[role] = process_document(
        document_id=role.value.lower(), role=role, src=document_input(path)
    )

result = run_project("determinism", processed)
payload = [
    {
        "difference_key": d.difference_key,
        "identity_strength": d.identity_strength.value,
        "scope": d.scope.value,
        "subject_kind": d.subject_kind.value,
        "subject_key": d.subject_key,
        "difference_type": d.difference_type.value,
        "severity": d.severity.value,
        "severity_rule_id": d.severity_rule_id,
        "chain_stage": d.chain_stage.value,
        "field_name": d.field_name,
        "values_digest": d.values_digest,
        "explanation_key": d.explanation_key,
        "explanation_params": dict(d.explanation_params),
        "evidence_ids": list(d.evidence_ids),
    }
    for d in result.comparison.differences
]
groups = [
    {"group_key": g.group_key, "signature": g.role_signature,
     "members": [m.line_key for m in g.members]}
    for g in result.groups
]
# 顺序本身就是被检对象，**绝不在这里排序**。
# 直接写 buffer 并显式 UTF-8 编码：Windows 控制台默认 GBK，走 sys.stdout 会按
# 控制台编码转一道，中文单据内容当场炸掉，而这跟被检的确定性毫无关系。
sys.stdout.buffer.write(json.dumps({"differences": payload, "groups": groups},
                                   ensure_ascii=False, sort_keys=True).encode("utf-8"))
"""


def _run(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(BACKEND)],
        cwd=BACKEND,
        env=env,
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"子进程失败（PYTHONHASHSEED={seed}，退出码 {proc.returncode}）")
    return proc.stdout.decode("utf-8")


def main() -> int:
    runs = [(seed, _run(seed)) for seed in SEEDS]

    print(f"跑了 {len(runs)} 次，每次一个独立进程，PYTHONHASHSEED 各不相同：")
    for seed, payload in runs:
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        count = len(json.loads(payload)["differences"])
        print(f"  PYTHONHASHSEED={seed:<8} 差异 {count} 条  sha256={digest}")

    baseline_seed, baseline = runs[0]
    for seed, payload in runs[1:]:
        if payload != baseline:
            print(f"\n❌ PYTHONHASHSEED={seed} 的结果与 {baseline_seed} 不一致。", file=sys.stderr)
            first = json.loads(baseline)["differences"]
            other = json.loads(payload)["differences"]
            for index, (a, b) in enumerate(zip(first, other, strict=False)):
                if a != b:
                    print(f"第一处不同在第 {index} 条：", file=sys.stderr)
                    print(f"  {baseline_seed}: {a}", file=sys.stderr)
                    print(f"  {seed}: {b}", file=sys.stderr)
                    break
            else:
                print(f"条数不同：{len(first)} vs {len(other)}", file=sys.stderr)
            return 1

    print("\n三次结果逐字节一致（Gate-0 第 15 条）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
