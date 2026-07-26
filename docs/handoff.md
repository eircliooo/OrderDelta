# 交接文档

> 写给**下一个接手这个仓库的人（或会话）**。
>
> 规格权威是 [`SPEC.md`](SPEC.md)，日常命令与红线在仓库根的 `CLAUDE.md`。
> 本文只回答那些「读代码读不出来、读文档会被误导」的东西：**现在到哪一步了、
> 哪些地方文档在说谎、下一步该动哪里、以及动它之前必须知道什么。**
>
> 最后更新：2026-07-27（Gate-0 全绿 + 5.1/5.3 缺陷修复 + CI 接入之后）

---

## 1. 一句话现状

**MVP-0（纯 XLSX 全链路）已完成，`bash scripts/verify.sh` 18 步全绿，945 条后端测试 + 62 条前端测试通过，零 skip。**

能跑通的完整链路：上传 2–3 份 `.xlsx` → 解析提取 → SKU 精确匹配 → N 元比较 →
证据落库 → 人工审核 → 自包含 HTML 报告 → 删除项目。

---

## 2. ⚠️ 动手之前必读的三件事

### 2.1 已推送到**公开**仓库

远程：`https://github.com/eircliooo/OrderDelta.git`，分支 `main`，
**visibility = PUBLIC**。首个提交与 `.gitattributes` 提交已在上面。

- 用户此前的约束仍然有效：**不要执行 `git reset --hard` / `git clean` 等破坏性命令；
  除非用户明确要求，不要自行推送或建 PR。**（本次推送是用户明确要求的。）
- 仓库是公开的，所以**推之前必须扫一遍绝对路径与密钥**。已经踩过一次：
  `docs/validation-report.md` 里每一步的命令行都带着
  `/c/Users/<用户名>/...`，共 15 处——这既是隐私泄漏，也直接违反
  SPEC §12.1 / §15.1「不泄露服务器绝对路径」。已在 `verify.sh` 里加
  `sanitize()` 统一抹成 `<repo>`（三种写法：`/c/Users/...`、`C:/Users/...`、
  `C:\Users\...`）。**新增任何会打印路径的验证步骤时，记得它会被原样写进公开报告。**
- `docs/validation-report.md` 与 `docs/golden-report.md` 现在已进版本控制，
  但它们仍是**产物**：本地跑一次 `verify.sh` / `pytest` 就会被重写，
  于是 `git status` 会变脏。这是预期行为，不是 bug。

### 2.2 Python 不在 PATH

所有后端命令必须用 venv 内的解释器：

```
backend\.venv\Scripts\python.exe
```

### 2.3 这个仓库反复出现同一种失效：**文档比代码漂亮**

已经抓到过多起：文档描述了不存在的类（`ValueResolver`）、不生效的字段
（`timeout_seconds`）、不存在的模块路径（`scripts.gen_docs`）、空目录
（`fixtures/generators`），以及**把已经做完的东西写成"未实现"**。

> **接手守则：任何关于「有没有 / 生不生效」的判断，一律以代码和实际跑过的命令为准，
> 不以 `docs/*.md` 的转述为准。** 发现文档与代码不符时，那本身就是一条要修的 bug。

---

## 3. 五分钟跑起来

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q                 # 945 passed
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend
npm ci && npm run dev
```

**证明自己没弄坏东西的唯一入口**（跑完会重写两份证据文件）：

```bash
bash scripts/verify.sh
```

Windows 下 `scripts\verify.ps1` 只是 6 行转发到 Git Bash，不维护第二套逻辑。
**没有 CI**——没人会替你跑它。

---

## 4. 架构：三条不能碰的边界

有测试强制，违反即红，不靠 code review：

| 边界 | 守卫 | 破坏后的真实后果 |
|---|---|---|
| `app.comparison` / `app.matching` / `app.exports` **禁止 import `app.db.models`** | `test_guards.py::TestImportBoundary` | 比较不再是纯函数，无法在无数据库的情况下测试与复现 |
| 域内模块（`app/` + `tools/`）**禁止 `float(` 与浮点字面量** | `TestNoFloatInDomain`（AST 扫描） | `1000 × 1.25` 在报告里显示成 `1249.9999999999998`，或算术校验产出假 `CALCULATION_ERROR` |
| **matching 层不做求和 / 拆合推断 / 套装展开** | 代码约定 + golden `duplicate_sku` 组 | 多行求和后比较 = 替企业裁定「分批交货 = 整批交货」，直接违反「不判断哪份文件正确」 |

**全库唯一取值入口**是 `app/extraction/snapshot.py::build_project_snapshot()` →
`ProjectSnapshot`（服务层入口 `services/projects.py::build_result()`）。
SPEC §11.2 把它写作 `ValueResolver.snapshot(project_id)`，**本仓库没有这个类**，只是名字不同。

### 四类数据，写权限互斥

| 类别 | 实体 | 谁能写 | 重跑时 |
|---|---|---|---|
| ① 文档所述 | `extracted_field`、`line_item` | 只有解析器 | — |
| ② 人工断言 | `user_correction` | 只有人 | 保留 |
| ③ 计算产物 | `difference`、`match_group`、`match_member`、`evidence` | 只有比较引擎 | **整体删除重算** |
| ④ 人工裁决 | `difference_review` | 只有人 | **一行都不碰** |

---

## 5. 缺陷台账（5.1 / 5.3 本轮已修）

### 5.1 ✅ 已修：改了比较规则后旧审核裁决被静默继承

**这曾是最严重的一条。** 前提摘要原本只由取值构成，于是把某字段从 `REVIEW` 调成
`CRITICAL` 后重跑，一条早被标成「已接受差异」的记录会原样带着旧裁决出现在报告里，
看起来像「这条新的 CRITICAL 已经有人看过并接受了」——恰好在风险刚被调高时失去人工复核。

两处一起补上（只堵一条等于没堵）：

- `input_fingerprint()` 补齐 SPEC §3.2 的第三段 `rules:`，值取
  `sha256(render_comparison_rules_md())`。**不另写「规则版本号」**：那份 Markdown
  已经是注册表的规范化全量序列化（归一化、比较方式、逐阶段严重度、容差、别名全在里面），
  而手写版本号必然会被忘记递增——忘记的后果恰好是这个指纹存在的唯一目的失守。
- `values_digest()` 新增必填关键字参数 `rule_signature`（严重度 + 规则 id）。
  做成**必填**是刻意的：调用点漏改会直接 `TypeError`，不会静默用旧语义跑下去。

结果是**只有严重度真的变了的那些差异**转为待确认，值和规则都没动的照常继承——
不会因为一次无关的规则微调让人重审几百条。

> 验证过它会咬人：把 `identity.py` 里那行 `@rule=` 去掉，
> `test_调高严重度后旧裁决不再被静默继承` 立刻红（实际继承了 `ACCEPTED_DIFFERENCE`）。

### 5.2 🟡 解析超时字段存在但零效果

`ParseLimits.timeout_seconds = 30` 没有任何代码读取。**这是一个真实的拒绝服务风险。**

**不要图省事在解析器内部实现**：真正耗时的 `load_workbook` 不可中断，而往行遍历里
插墙钟判断会把系统时钟读进解析路径，**直接破坏 Gate-0 第 15 条**
（`tools.determinism` 的 3 个进程 sha256 一致会随机变红）。正确位置是请求层。

### 5.3 ✅ 已修：删除单份文档留下孤儿证据

`evidence.document_id` 仍然**没有外键**（级联只挂在 `project_id` 上），
改的是语义：删掉一份文档 = 上一轮比较的输入已不成立，所以主动作废③计算产物
（`_drop_computed`）并把项目退回 `DRAFT`。

不这么做的话，那些证据行会留在库里、仍被 `difference.evidence_ids` 引用，
报告里照样渲染出「引用原文：某某单元格」——指向一份已经不在项目里的文件。

### 5.4 其他未实现（已在 `limitations.md` 记账）

`GET /api/v1/differences/{id}/evidence`（SPEC §12.2 明列，路由不存在）、
`extracted_field`/`line_item` 未落库（每次从磁盘重新解析）、
`unmapped_headers` 收集了但零消费者（**这是最主要漏报来源的唯一可见线索**）、
LLM 适配器零代码（`llm_enabled` 是写死的 `False` 字面量）、
PDF 整线、Excel 导出、匹配二三级、Docker、Alembic、Playwright、LICENSE 文件。

---

## 6. 埋在暗处的坑

| 坑 | 失效形态 |
|---|---|
| **命令行 `-m` 会整体覆盖 `addopts` 里的 `-m 'not mvp1'`** | ✅ verify.sh 已改成 `-m 'golden and not mvp1'` / `'enum_subset and not mvp1'`。**你自己敲 `pytest -m xxx` 时同样会覆盖**，MVP-1 开工后要记得带上 |
| **verify.sh 里任何会输出中文的新步骤都要带 `env PYTHONIOENCODING=utf-8`** | 否则 Windows 下按 GBK 编码落进报告，证据文件里出现乱码（已踩过一次） |
| **改了规则导致 `total_differences` 对不上时，不要手改 `expected.json`** | 那是快照值，但 fixtures 有逐字节比对守着。唯一合法路径：改 `tools/fixtures/build.py` 里那一组的 `total_differences=` 并重跑生成器 |
| **`filterwarnings = ["error"]`** | 升级任何依赖后，一条新的 `DeprecationWarning` 就会让一批测试变红，失败信息看起来和业务 bug 一模一样 |
| **不要在 `frontend/src/` 下放 `*.test.ts`** | `vite.config.ts` 的 include 写死了 `tests/**`，放在 src 下的测试会被**静默跳过**，`npm test` 照样全绿 |
| ~~仓库根的 `.env` 不会被读到~~ | ✅ 已修：`config.py` 的 `env_file` 改成绝对路径 `REPO_ROOT / ".env"`。原先相对进程 CWD，而文档化的命令都在 `backend/` 下跑，配置「看起来生效了其实没生效」 |
| **`GET /projects/{id}/differences` 读的是库里的旧结果，`report.html` 是现算的** | 两者可能不一致，且没有任何地方会提示 |

其余高频坑（openpyxl 两次加载、`PRAGMA foreign_keys` 默认 OFF、表头别名禁止子串匹配、
先量化再分桶……）见 `CLAUDE.md` 的「容易踩的坑」，那份是逐条踩出来的，值得先读一遍。

---

## 7. 两份证据文件是**跑出来的，禁止手写**

| 文件 | 谁写的 | 闸门 |
|---|---|---|
| `docs/validation-report.md` | `scripts/verify.sh`（整体覆写） | 失败不中断，末尾按累计失败数返回退出码 |
| `docs/golden-report.md` | pytest 的 `pytest_sessionfinish` 钩子（`tests/conftest.py`） | ① `tests.test_golden` 真被收集过 ② 本轮 `exitstatus == 0` ③ 16 组一组不少 |

第 ② 道闸门是本轮补的：`_RESULTS` 是管线跑出来的，断言失败不影响它，
所以 `test_重复运行的差异顺序完全一致` 或 `test_仓库内的fixture就是生成器的产物`
挂掉时，渲染出的报告**依然是一份完美的全绿表**——而那两条恰恰是报告本身看不出来的失效。

SPEC §17：最终交付报告的「验证证据」与「测试数据结果」两节**必须是这两个文件的原样粘贴**。

---

## 8. 下一步建议（按优先级）

1. **拿 3–5 份真实客户单据跑一遍**。这是唯一能证伪「自产 fixture 全绿」的动作。
   16 组 fixtures 由 `tools/fixtures/build.py` 自产，生成器与提取器共享同一套别名表——
   全绿只证明引擎自洽，**不代表能读懂客户发来的真实单据**。表头打分器和别名表
   最可能在这里首次见血。
2. `timeout_seconds` 落到请求层，不是解析器内。
3. 补 `evidence.locator` 与外键——**在开始做 PDF 之前，不是之后**。
4. 把 `tools/determinism.py` 的语料换成 fixtures 全量：现在它只验一组自己现场构造的
   3 文档场景，16 组 golden 的跨进程一致性其实还没有证据。

---

## 9. 环境备注

- 运行时数据在 `data/`（已 gitignore）：`orderdelta.sqlite3` + `files/`。
  当前留有一行我建的空测试项目 `Gate-0 demo`。要清干净：删掉 `data/` 目录即可，
  下次启动会重建。
- winget 装 Python 在中国大陆要加 `--source winget`（msstore 源会超时）。
- 措辞红线：删除只能说「删除数据库记录与磁盘文件」，**不得宣称「安全擦除」「不可恢复」**。
