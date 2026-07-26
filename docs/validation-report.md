# 验证证据报告

> **本文件由 `scripts/verify.sh` 自动生成，禁止手写。**
> 每一步都记录了实际命令、完整输出与退出码。失败不中断，跑完全部再汇总。

```
生成时间（本机）: 2026-07-27 03:15:06 +0800
仓库根           : $REPO_ROOT
Python 解释器    : $BACKEND/.venv
```

---

## 1. Python 版本

命令：`<repo>/backend/.venv/Scripts/python.exe -V`

退出码：**0** ✅

```
Python 3.12.10
```

## 2. pip 版本

命令：`<repo>/backend/.venv/Scripts/python.exe -m pip --version`

退出码：**0** ✅

```
pip 25.0.1 from <repo>\backend\.venv\Lib\site-packages\pip (python 3.12)
```

## 3. 后端格式化检查

命令：`<repo>/backend/.venv/Scripts/python.exe -m ruff format --check .`

退出码：**0** ✅

```
64 files already formatted
```

## 4. 后端 lint

命令：`<repo>/backend/.venv/Scripts/python.exe -m ruff check app tests tools`

退出码：**0** ✅

```
All checks passed!
```

## 5. 后端类型检查

命令：`<repo>/backend/.venv/Scripts/python.exe -m mypy app`

退出码：**0** ✅

```
Success: no issues found in 44 source files
```

## 6. 比较规则文档与注册表同步

命令：`env PYTHONIOENCODING=utf-8 <repo>/backend/.venv/Scripts/python.exe -m tools.gen_docs --check`

退出码：**0** ✅

```
已同步：comparison-rules.md
```

## 7. 后端全部测试

命令：`<repo>/backend/.venv/Scripts/python.exe -m pytest -q`

退出码：**0** ✅

```
........................................................................ [  7%]
........................................................................ [ 15%]
........................................................................ [ 22%]
........................................................................ [ 30%]
........................................................................ [ 38%]
........................................................................ [ 45%]
........................................................................ [ 53%]
........................................................................ [ 61%]
........................................................................ [ 68%]
........................................................................ [ 76%]
........................................................................ [ 84%]
........................................................................ [ 91%]
........................................................................ [ 99%]
....                                                                     [100%]
940 passed in 16.92s
```

## 8. 零 skip / MVP-1 反选数量

命令：`env PYTHONIOENCODING=utf-8 <repo>/backend/.venv/Scripts/python.exe -m tools.mvp1_report`

退出码：**0** ✅

```
本轮实际执行（Gate-0 口径）: 940
标记 mvp1 被反选           : 0
仓库内测试总数             : 940

没有任何用例被反选——本仓库当前不含 MVP-1 标记的测试。
```

## 9. Golden 测试（单独）

命令：`<repo>/backend/.venv/Scripts/python.exe -m pytest -q -m golden`

退出码：**0** ✅

```
........................................................................ [ 53%]
...............................................................          [100%]
135 passed, 805 deselected in 4.14s
```

## 10. Golden 指标报告总览

命令：`head -30 docs/golden-report.md`

退出码：**0** ✅

```
# Golden 用例指标报告

> **本文件由 `pytest` 的 `pytest_sessionfinish` 钩子自动生成，禁止手写。**
> 覆盖 16 组 fixtures（SPEC §16.2 的 12 组语义 + 1 组两文件变体 + 3 组版面变体）。

> ⚠️ **自产 fixture 免责声明（SPEC §16.5）**：本组 fixtures 由 `tools.fixtures.build` 程序化自产，生成器与提取器共享同一套别名表与列布局假设。全绿只证明确定性比较逻辑与解析管线的自洽与稳定，**不代表对真实客户文件的提取准确率**。

## 总览

| 用例 | 参与角色 | 必须命中 | 实际命中 | 非植入 CRITICAL | 差异总数 |
|---|---|---:|---:|---:|---:|
| `currency_mismatch` | Q+P+I | 8 | 8 | 0 | 8 |
| `duplicate_sku` | Q+P+I | 1 | 1 | 0 | 1 |
| `identical` | Q+P+I | 0 | 0 | 0 | 0 |
| `incoterm_mismatch` | Q+P+I | 1 | 1 | 0 | 1 |
| `incoterm_place_mismatch` | Q+P+I | 1 | 1 | 0 | 1 |
| `layout_chinese_headers` | Q+P+I | 1 | 1 | 0 | 1 |
| `layout_merged_title` | Q+P+I | 0 | 0 | 0 | 0 |
| `layout_second_sheet` | Q+P+I | 2 | 2 | 0 | 2 |
| `line_total_wrong` | Q+P+I | 3 | 3 | 0 | 3 |
| `payment_terms_mismatch` | Q+P+I | 1 | 1 | 0 | 1 |
| `pi_missing_sku` | Q+P+I | 2 | 2 | 0 | 2 |
| `pi_unit_price_wrong` | Q+P+I | 2 | 2 | 0 | 2 |
| `po_extra_sku` | Q+P+I | 2 | 2 | 0 | 2 |
| `po_quantity_changed` | Q+P+I | 3 | 3 | 0 | 3 |
| `row_order_shuffled` | Q+P+I | 0 | 0 | 0 | 0 |
| `two_docs_only` | P+I | 0 | 0 | 0 | 0 |

**合计**：16 组，植入差异 27 条，实际命中 27 条（召回 27/27）；非植入 CRITICAL 误报 **0** 条（上限恒为 0，见 Gate-0 第 5 条）。
```

## 11. 架构守卫测试

命令：`<repo>/backend/.venv/Scripts/python.exe -m pytest -q tests/test_guards.py`

退出码：**0** ✅

```
......................................                                   [100%]
38 passed in 1.18s
```

## 12. 只产已声明枚举（enum_subset）

命令：`<repo>/backend/.venv/Scripts/python.exe -m pytest -q -m enum_subset`

退出码：**0** ✅

```
........                                                                 [100%]
8 passed, 932 deselected in 1.72s
```

## 13. 确定性：PYTHONHASHSEED=1

命令：`env PYTHONHASHSEED=1 <repo>/backend/.venv/Scripts/python.exe -m pytest -q`

退出码：**0** ✅

```
........................................................................ [  7%]
........................................................................ [ 15%]
........................................................................ [ 22%]
........................................................................ [ 30%]
........................................................................ [ 38%]
........................................................................ [ 45%]
........................................................................ [ 53%]
........................................................................ [ 61%]
........................................................................ [ 68%]
........................................................................ [ 76%]
........................................................................ [ 84%]
........................................................................ [ 91%]
........................................................................ [ 99%]
....                                                                     [100%]
940 passed in 15.79s
```

## 14. 确定性：跨进程 3 次逐字节一致

命令：`env PYTHONIOENCODING=utf-8 <repo>/backend/.venv/Scripts/python.exe -m tools.determinism`

退出码：**0** ✅

```
跑了 3 次，每次一个独立进程，PYTHONHASHSEED 各不相同：
  PYTHONHASHSEED=0        差异 6 条  sha256=78b3c10afbcb602870fdf11e72f344d5d59ad4f78f019ff98f07eaee436806fb
  PYTHONHASHSEED=1        差异 6 条  sha256=78b3c10afbcb602870fdf11e72f344d5d59ad4f78f019ff98f07eaee436806fb
  PYTHONHASHSEED=524287   差异 6 条  sha256=78b3c10afbcb602870fdf11e72f344d5d59ad4f78f019ff98f07eaee436806fb

三次结果逐字节一致（Gate-0 第 15 条）。
```

## 15. 前端 lint

命令：`npm run lint`

退出码：**0** ✅

```

> orderdelta-frontend@0.1.0 lint
> eslint .
```

## 16. 前端类型检查

命令：`npm run typecheck`

退出码：**0** ✅

```

> orderdelta-frontend@0.1.0 typecheck
> tsc --noEmit
```

## 17. 前端单元测试

命令：`npm test`

退出码：**0** ✅

```

> orderdelta-frontend@0.1.0 test
> vitest run


[1m[46m RUN [49m[22m [36mv3.2.7 [39m[90m<repo>/frontend[39m

 [32m✓[39m tests/identifiers.test.ts [2m([22m[2m9 tests[22m[2m)[22m[32m 5[2mms[22m[39m
 [32m✓[39m tests/explanations.test.ts [2m([22m[2m11 tests[22m[2m)[22m[32m 8[2mms[22m[39m
 [32m✓[39m tests/apiBase.test.ts [2m([22m[2m3 tests[22m[2m)[22m[32m 3[2mms[22m[39m
 [32m✓[39m tests/uiLanguage.test.tsx [2m([22m[2m6 tests[22m[2m)[22m[33m 401[2mms[22m[39m
 [32m✓[39m tests/upload.test.tsx [2m([22m[2m7 tests[22m[2m)[22m[33m 463[2mms[22m[39m
 [32m✓[39m tests/projects.test.tsx [2m([22m[2m6 tests[22m[2m)[22m[33m 665[2mms[22m[39m
 [32m✓[39m tests/workbench.test.tsx [2m([22m[2m20 tests[22m[2m)[22m[33m 1659[2mms[22m[39m
   [33m[2m✓[22m[39m 差异工作台[2m > [22m修改审核状态与备注后发出正确的 PUT 请求 [33m 336[2mms[22m[39m

[2m Test Files [22m [1m[32m7 passed[39m[22m[90m (7)[39m
[2m      Tests [22m [1m[32m62 passed[39m[22m[90m (62)[39m
[2m   Start at [22m 03:16:19
[2m   Duration [22m 4.59s[2m (transform 761ms, setup 3.10s, collect 3.15s, tests 3.20s, environment 7.79s, prepare 1.24s)[22m
```

## 18. 前端构建

命令：`npm run build`

退出码：**0** ✅

```

> orderdelta-frontend@0.1.0 build
> vite build

[36mvite v7.3.6 [32mbuilding client environment for production...[36m[39m
transforming...
[32m✓[39m 106 modules transformed.
rendering chunks...
computing gzip size...
[2mdist/[22m[32mindex.html                 [39m[1m[2m  0.41 kB[22m[1m[22m[2m │ gzip:  0.31 kB[22m
[2mdist/[22m[35massets/index-16_wxO6O.css  [39m[1m[2m  8.73 kB[22m[1m[22m[2m │ gzip:  2.36 kB[22m
[2mdist/[22m[36massets/index-D9SwrVqz.js   [39m[1m[2m296.96 kB[22m[1m[22m[2m │ gzip: 94.53 kB[22m
[32m✓ built in 1.18s[39m
```

---

## 汇总

- 步骤总数：18
- 失败步骤：**0**

全部步骤退出码为 0。

