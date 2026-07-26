# 已知限制

> 本文是**面向所有人**的诚实清单：这个工具做不到什么、哪些地方会漏报、哪些验收项还没完成。
>
> SPEC §2.4 的判定标准是：**一个只读 README 和本文件的用户，把文件丢进去拿到「零差异」，
> 不会因此对任何事情产生误解。** 本文按这个标准写。
>
> 状态标记：✅ 已实现并有测试 · ⚠️ 部分实现 · ⬜ 未实现。

---

## 1. 自产 fixture 免责声明（必读）

以下段落逐字引自 [`SPEC.md`](SPEC.md) §16.5：

> 本项目全部 fixture 由 `backend/tools/fixtures/build.py` 程序化自产，生成器与提取器共享同一套别名表与列布局假设。**Golden 通过率只证明确定性比较逻辑与解析管线的自洽与稳定，不代表对真实客户文件的提取准确率。** 真实准确率在接入真实语料前未知，且不得对外宣称。

补充说明（不改变上述结论）：

- 生成器在本仓库的实际位置是 `backend/tools/fixtures/build.py`，产物落到仓库根的
  `fixtures/orders/` 与 `fixtures/expected/`。
- 当前共 **16 组** golden 用例。12 组语义用例（SPEC §16.2）：`identical`、
  `po_quantity_changed`、`pi_unit_price_wrong`、`currency_mismatch`、`incoterm_mismatch`、
  `incoterm_place_mismatch`、`payment_terms_mismatch`、`po_extra_sku`、`pi_missing_sku`、
  `row_order_shuffled`、`duplicate_sku`、`line_total_wrong`；1 组两文件变体：`two_docs_only`；
  3 组版面变体（SPEC §16.1，复用其他组的期望值）：`layout_merged_title`（抬头+合并大标题）、
  `layout_chinese_headers`（纯中文标签与表头）、`layout_second_sheet`（订单表在第二个 sheet）。
- 这 16 组**全部由本项目自己生成**，用的是本项目自己的表头假设与列布局。
  它们能证明「引擎逻辑自洽、可重复、不产生误报」，**不能证明「能读懂客户发来的真实单据」**。

**因此：本项目不得对外宣称任何准确率数字。** 真实准确率要在接入真实语料并人工核对后才存在。

> 下一步建议（不列入 MVP 验收关键路径，因为它会阻塞在人工输入上）：
> 取 30–50 份真实历史单据做 holdout 验证，人工核对漏报与误报，得出第一个真实准确率区间。

---

## 2. 输入格式限制

| 限制 | 说明 |
|---|---|
| **只支持 `.xlsx`** | Excel 2007 及以后的标准格式 |
| **不支持 PDF** | 上传 `.pdf` 被显式拒绝，`reason_code = UNSUPPORTED_EXT`，中文提示「MVP-0 暂不支持 PDF，将在下一版本提供」。属 MVP-1 |
| **不支持扫描件、照片、截图** | 不做 OCR，不做图片识别。在 SPEC §1.2 的「坚决不实现」清单里 |
| **不支持 `.xls` / `.xlsm` / `.xlsb`** | `.xlsm` 含宏，一律拒绝（连打开都不给） |
| **不支持 `.doc` / `.docx`** | 不在范围内 |
| **不支持加密 / 带密码的 Excel** | 文件头是 OLE2 时判定为 `ENCRYPTED` 并拒绝 |
| 单个上传文件 ≤ 20MB | `settings.max_upload_bytes` |
| 解压后 ≤ 300MB、压缩比 ≤ 200:1 | 解压炸弹防护 |
| 工作表 ≤ 20 张 | `ParseLimits.max_sheets` |
| 每表 ≤ 5000 行，合计 ≤ 20000 行 | 边遍历边判，不读 `<dimension>` 预检 |
| 公式必须有缓存值 | 程序生成、从未被 Excel 保存过的文件读不到数值，会提示「请用 Excel 打开另存后重试」 |

---

## 3. 提取与匹配限制

### 3.1 只做一级 SKU 精确匹配

- **没有型号（SKU）的行不与任何行匹配。** 它会被单独归为一个 `NOSKU:...` 组，
  产出 `UNMATCHED_LINE_ITEM`，但**不会**去猜它对应另一份文件的哪一行。
- 型号写法不同（`AB-100` vs `AB100`）**默认视为两个不同型号**。归一化只做去空格、
  统一大小写与全角半角，**默认不删前导零**（`0012` 与 `12` 不是同一个料号）。
- **客户料号映射**（客户 PO 上写他们自己的料号）属 MVP-1，未实现。
- **模糊匹配**（按品名 / 规格相似度找候选）属 MVP-1，未实现。

这不是能力不足，是**刻意的取舍**：优先目标是降低错误匹配，而不是追求所有行都被匹配。
错误匹配会产出看起来很具体、实际完全错误的差异，比「没匹配上」危险得多。

### 3.2 不做任何单位换算

只统一**同义**单位（`PCS/PC/PIECE`、`SETS/SET`、`CTNS/CARTONS`、`KG/KGS`）。

**`SETS` 与 `PCS` 不换算，`CTNS` 与 `PCS` 不换算。** 遇到单位不同，结果是
`INCOMPARABLE`（无法比较，`REVIEW`），不是「不一致」。

理由：没有企业级换算规则时，`1 SET = ? PCS` 是猜的。猜错会产出一个数值精确、
结论错误的差异。

### 3.3 多行组只标歧义，不求和

同一型号在一份文件里出现多行（例如按颜色拆行）时：

- `multiplicity_state = MULTI_PER_ROLE`
- **不做字段比较**，产出**一条** `AMBIGUOUS_MATCH`（`REVIEW`），列出全部候选
- **不求和、不做拆合推断、不做套装展开**

理由：多行求和后比较，等于替企业裁定「分批交货 = 整批交货」——这是商务判断，
直接违反「不得自动判断哪份文件正确」。

> 这会让 `AMBIGUOUS_MATCH` 变多、演示观感变差。这是自觉选择，不是缺陷。

### 3.4 读不出的列不会被比较，也不会有提示

如果表头写法不在别名表里（比如写成 `Nos.`），那一列**不会被识别**，对应字段的差异
**不会被发现**，界面上也不会有「有一列我没读懂」的提示。

**这是本工具最主要的漏报来源。** 缓解手段是：

- 表头至少要同时出现「数量类」与「单价或金额类」，否则整张表都不认，
  文档降级为 `NEEDS_REVIEW` + `NO_TABLE_FOUND` 并**不进入比较集合**（不会静默返回零差异）
- 未映射的表头会被收集进 `unmapped_headers`（但 MVP-0 的界面尚未展示它）

### 3.5 不判断哪份文件正确

工具只陈述「PO 写 1200，PI 写 1000」，**不会**说「应该以 PO 为准」。
不自动批准订单、不自动修改单据、不提供贸易合规 / 法律 / 财务结论。

### 3.6 缺席角色 = 未检查

未上传或解析失败的角色**完全不参与比较**，因而**不产生任何差异**（包括不产生
`MISSING_VALUE`）。

这是刻意的——否则两文件场景会被几十条假缺失淹没。代价是引入一种新的失效模式，
因此界面与报告首屏**强制**显示横幅：

> **缺席角色 = 未检查，不等于无差异**

### 3.7 「零差异」的准确含义

**「零差异」只表示：在本工具能读到的范围内、在参与比较的角色之间，没有发现不一致。**

它不表示这单没问题。至少三种情况会得到「零差异」但实际有问题：

1. 某一列的表头没被识别 → 那一列根本没比
2. 某个角色没上传 / 解析失败 → 那份文件根本没参与
3. 差异发生在本工具不提取的字段上（见 [`future-scope.md`](future-scope.md) 的 15 个推迟字段）

---

## 4. 判定与展示限制

| 情况 | 行为 | 为什么不做得更「聪明」 |
|---|---|---|
| 日期写成 `08/09/2026` | 标为歧义，**不猜**是 8 月 9 日还是 9 月 8 日 | 猜错会产出一个看起来很确定的错误结论 |
| 币种是单独的 `$` | 标为歧义，**不认定**为 USD | `$` 也可能是 HKD / AUD / CAD / SGD / NZD |
| 付款条件无法结构化 | 保留原文，标为待确认（`REVIEW`） | 「机器读不懂」不等于「两份文件不一致」 |
| 交期一方写相对条款、一方写绝对日期 | 标为需人工换算（`REVIEW`） | 换算需要知道定金到账日，工具不知道 |
| `Σ行金额 ≠ 总金额` | 标为「存在未解释差额 X」（`REVIEW`），**不判 CRITICAL** | 真实 PI 几乎总有运费 / 折扣 / 模具费 |
| 认不出 Incoterm 术语 | `term = None` + 提示，**绝不**把整串塞进「地点」 | 那等于用一个看起来有值的字段掩盖一次识别失败 |

风险等级按**贸易链条阶段**查表，不按字段：Q→PO 的砍价改量多为 `INFO`，
PO→PI 的同类差异为 `CRITICAL`。如果你的业务判断不同，需要修改
`backend/app/domain/fields.py` 的严重度表（改配置，不改逻辑）。

---

## 5. 运行与部署限制

| 限制 | 说明 |
|---|---|
| **无身份认证** | 没有登录、用户、权限、会话、审计日志 |
| **单机单用户** | 靠默认绑定 `127.0.0.1` 保护。**不得改成 `0.0.0.0`**——那会让同一局域网内任何人无需密码打开你的订单数据 |
| **不适合部署到服务器** | 「生产部署」在 SPEC §1.2 的「坚决不实现」清单里 |
| **无 Docker** | 属 MVP-1 |
| **无数据库迁移**（Alembic） | 当前用 `Base.metadata.create_all()` 建表。**schema 变更没有升级路径**，属 MVP-1 |
| **无备份** | `data/` 目录被误删就没了。刻意不做自动备份——那会在用户不知情的位置留下订单数据副本 |
| **无处理超时** | 见 §6.2 |
| 删除只能表述为「删除数据库记录与磁盘上的原始文件」 | 在 SQLite + 普通文件系统 + SSD 上无法真正安全擦除。见 [`security.md`](security.md) §2.3 |

---

## 6. 未完成项清单

### 6.1 尚未落库的数据

`backend/app/db/models.py` 当前有 8 张表：`project`、`document`、`user_correction`、
`match_group`、`match_member`、`evidence`、`difference`、`difference_review`。

SPEC §3.2 定义但**尚未落库**的：

| 表 | 状态 | 实际影响 |
|---|---|---|
| `extracted_field` | ⬜ 未落库 | 每次读取都从磁盘上的原始文件**重新解析**（`services/projects.py::_process`）。解析是确定性的、原始文件始终在，所以结果正确；代价是每次请求都要重跑解析 |
| `line_item` | ⬜ 未落库 | 同上 |
| `line_item.raw_cells` | ⬜ 未落库 | **SPEC §2.1 的「可逆性保险」当前不成立。** 它的作用是「后续补任何字段都能从库内回填、不必重新解析原始文件」——现在因为总是重新解析，效果上等价；但一旦原始文件被删除，历史项目就无法回填 |
| `difference_evidence`（链接表） | ⚠️ 用 JSON 列替代 | `difference.evidence_ids` 是一个 JSON 数组列，而非独立的多对多链接表。功能等价，但**无法按证据反查差异**，也没有引用完整性 |

### 6.2 已知功能缺口

| 缺口 | 位置 | 说明 |
|---|---|---|
| **处理超时未强制执行** | `ParseLimits.timeout_seconds = 30` | 字段存在，**没有任何代码使用它**，而且**刻意不在解析器内部实现**：真正耗时的 `load_workbook` 不可中断，往行遍历里插墙钟判断会把系统时钟读进解析路径，直接和 Gate-0 第 15 条确定性冲突。正确位置是请求层（独立进程 / worker）。构造得当的文件可让一次上传长时间占用工作线程。见 [`security.md`](security.md) §4.2 |
| **`docs/validation-report.md` 与 `docs/golden-report.md` 是跑出来的** | 前者由 `scripts/verify.sh` 写，后者由 `backend/tests/conftest.py` 的 `pytest_sessionfinish` 钩子写 | 两份都**已实现**（golden 是 16 组不是 SPEC 写的 12 组）。但它们是**产物不是源码**：干净 clone 出来没有，跑过才有；且当前 `docs/` 整个是 untracked 状态，本地重跑会无痕覆盖。golden 报告有三道闸门（test_golden 被收集过 / 本轮 `exitstatus == 0` / 16 组一组不少），任一不满足就保留上一份 |
| ~~改了比较规则后，旧审核裁决会被静默继承~~ ✅ 已修 | `services/projects.py::input_fingerprint()` + `domain/identity.py::values_digest()` | `input_fingerprint` 已补齐 SPEC §3.2 的第三段 `rules:`（哈希 `render_comparison_rules_md()` 全文，改任何一条规则它必变）；判断依据摘要也带上了「当时告诉用户的严重度 + 产出它的规则 id」。现在**只有严重度真的变了的那些差异**转为待确认，值和规则都没动的照常继承。见 `test_调高严重度后旧裁决不再被静默继承`（去掉修复即红） |
| **`GET /api/v1/differences/{id}/evidence` 未实现** | SPEC §12.2 明列的端点 | 路由不存在。证据目前只能随差异列表整体返回，无法按单条差异取证据 |
| ~~删单份文档留下孤儿证据~~ ✅ 已修 | `services/projects.py::delete_document()` | `evidence.document_id` 仍无外键（级联挂在 `project_id`），改为删文档时主动作废③计算产物并把项目退回 DRAFT。见 `test_删单份文档不留孤儿证据` |
| **`unmapped_headers` 未展示** | `HeaderDetection.unmapped_headers` | 数据已收集，界面与报告都没有展示。用户无从知道「有一列没被读懂」 |
| **解析确认页** | — | 属 MVP-1。用户当前无法在比较前检查提取结果是否正确 |
| **Excel 导出** | — | 属 MVP-1。**落地时必须实现公式注入防护**（见 `security.md` §3.1） |
| **`weak_identity_count` 未展示** | — | 弱身份差异重跑后不继承审核状态，前端应报告有多少条，当前未实现 |
| **孤儿裁决的「清理」按钮** | — | 本次未产生对应 key 的 `difference_review` 行会保留为孤儿，但没有清理入口 |
| **LLM 适配器完全不存在** | — | 不是「默认关闭」而是「还没有」：`backend/app/` 下没有 provider、没有 `NullProvider`、没有启用开关，`GET /health` 的 `llm_enabled` 是写死的 `False`。已预留的只有 `TextBlock.block_id`。见 [`architecture.md`](architecture.md) §12 |

### 6.3 恶意文件拒绝路径的测试覆盖

`backend/tests/test_security.py` 已覆盖以下拒绝路径（**代码存在 ≠ 行为被验证**，
所以这些路径必须有测试）：

| 路径 | 代码位置 | 测试 |
|---|---|---|
| `.xlsm` / `.xlsb` / `.xls` 被拒 | `parsers/xlsx.py::can_parse` | ✅ `test_拒绝xlsm含宏`、`test_拒绝旧版xls` |
| 加密的 OOXML（OLE2 头）→ `ENCRYPTED` | 同上 | ✅ `test_加密文件给出明确原因` |
| 伪装扩展名 | 同上 | ✅ `test_拒绝伪装扩展名` |
| PDF 被拒 → `UNSUPPORTED_EXT` | 同上 | ✅ `test_pdf被明确拒绝且带原因` |
| 解压炸弹 → `FILE_TOO_LARGE` | `parsers/xlsx.py::_check_zip_bomb` | ✅ `test_解压炸弹被拒绝`、`test_解压炸弹在解析层显式失败而不是崩溃` |
| 损坏的 ZIP → `CORRUPT` | `parsers/xlsx.py::parse` | ✅ `test_损坏的zip显式失败` |
| 工作表数超限 → `SHEET_LIMIT` | `parsers/xlsx.py::_parse_workbook` | ✅ `test_工作表过多被拒绝` |
| 行数超限 → `ROW_LIMIT` | 同上 | ✅ `test_行数超限被拒绝` |
| 公式无缓存值 → `FORMULA_WITHOUT_CACHE` | `parsers/xlsx.py::_scan_sheet` | ✅ `test_公式原文作为证据保留` |
| 上传超过 20MB → `FILE_TOO_LARGE` | `services/projects.py::_validate_upload` | ✅ `test_拒绝超大文件` |
| MIME 不在白名单 → `UNSUPPORTED_MIME` | 同上 | ✅ `test_拒绝错误mime` |
| 文件名路径穿越 | 同上 + 随机 UUID 落盘 | ✅ `test_文件名里的路径穿越不落地`、`test_落盘文件名与原名无关` |
| 错误信息不含绝对路径 | `ServiceError` + 异常处理器 | ✅ `test_错误信息不含绝对路径`、`test_解析失败信息不含绝对路径` |
| 默认绑定回环、默认不启用模型 | `core/config.py` | ✅ `test_默认绑定回环地址`、`test_默认不启用任何外部模型` |
| 空工作簿不假装成功 | `parsers/xlsx.py::_parse_workbook` | ✅ `test_空工作簿不假装成功` |

**仍未覆盖**：解析超时（因为**功能本身未实现**，见 §6.2）。

### 6.4 前端完成度

`frontend/src/` 已有入口（`main.tsx`）、`App.tsx` 路由、两个页面
（`ProjectsPage` / `WorkbenchPage`）、**十个组件**（`components/` 下逐个数）、
中文标签表与说明模板；`frontend/tests/` 有 7 个测试文件。
`npm run build` 可产出 `frontend/dist`，由后端单端口托管。

**已知偏差**：`App.tsx` 同时注册了 `/projects/:projectId` 与
`/projects/:projectId/differences` 两条路由指向同一个页面；SPEC §13.2 只要求后者。
这不影响功能，但意味着同一页面有两个 URL。

> 本节是撰写时的快照。前端由并行工作流推进，**以 `frontend/src/` 与
> `npm test` 的实际结果为准**；本文不复述可能随时变化的测试通过数。

---

## 7. Gate-0 验收门表状态

以下逐条对照 SPEC §2.4 的 18 项。**未完成的一律标注为未完成，不表述为「已基本完成」。**

| # | 检查项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 干净环境可启动（Windows 原生路径） | ✅ | 已实测：`winget` 装 Python 3.12 → `python -m venv .venv` → `pip install -e ".[dev]"` → `npm ci` + `npm run build` → `uvicorn app.main:app --host 127.0.0.1 --port 8000`，`/api/v1/health` 返回 `ok`，`netstat` 确认只监听 `127.0.0.1`。步骤见 [`README.md`](../README.md) §6 |
| 2 | 用户能完成 2 份或 3 份 XLSX 上传与检查 | ✅ | API 层有集成测试（`test_完整闭环`、`test_两份文件即可运行检查`、`test_只有一份文件时明确拒绝`）；界面为两个路由的 React SPA |
| 3 | 12 组 golden 全绿 | ✅ | 实为 16 组（12 语义 + `two_docs_only` + 3 组版面变体），`pytest -m golden` 全绿 |
| 4 | 所有植入的关键差异被发现 | ✅ | `test_必须命中的差异一条都不能少`，缺一即失败 |
| 5 | fixture `identical` 的 CRITICAL 误报 = 0 | ✅ | `test_非植入的critical误报不超过上限`，上限恒为 0 |
| 6 | 全部金额计算使用 Decimal（AST 扫描证明域内无 `float(`） | ✅ | `test_guards.py::TestNoFloatInDomain`，含守卫自身的防退化断言 |
| 7 | 未对齐行不被强行匹配 | ✅ | `matching/engine.py::assert_partition`，在 `compare()` 里实际调用；`test_guards.py::TestPartitionGuardBites` 证明该守卫会咬人（丢行 / 一行进两组 / 引用不存在的行三种都必须抛）；DB 层另有 `match_member.line_item_ref UNIQUE` |
| 8 | PDF 上传被显式拒绝且带结构化 `reason_code` | ✅ | `test_拒绝pdf并给出明确原因`（API 层）、`test_pdf被明确拒绝且带原因`（解析层） |
| 9 | 每个 Difference 至少关联一条 Evidence | ✅ | `test_每条差异都至少有一条证据`（全量断言） |
| 10 | 审核状态持久化 | ✅ | `difference_review` 表 + `PUT /reviews/{key}` |
| 11 | 重跑继承：修正 1 个单价后重跑，其余审核状态原样保留 | ✅ | `test_修正后重跑保留未受影响的裁决`、`test_前提变化的差异置为待确认且保留备注` |
| 12 | HTML 报告断网 + 后端停机可打开，文件内无外部 `http(s)://` | ✅ | `test_文件内无任何外部http引用`、`test_不引用任何外部资源标签`（机械断言） |
| 13 | 删除项目 → orphan 计数 = 0 且磁盘文件已删 | ✅ | `test_删除项目清库清盘` |
| 14 | 后端 lint / 类型检查 / 单元测试 / 集成测试通过 | ⚠️ | **不得凭本文断言，一律以 `scripts/verify.sh` 的实际输出为准**。它写出 `docs/validation-report.md`——**该文件由脚本生成，仓库不预先附带，跑过才有**。脚本同时覆盖 `ruff format --check` / `ruff check` / `mypy` / `pytest` / `gen_docs --check` 与前端 lint / typecheck / test / build |
| 15 | 确定性：连跑 3 次，差异集合与 fingerprint 逐字节一致 | ✅ | `verify.sh` 第 14 步 `python -m tools.determinism`：**3 个独立进程、3 个不同 `PYTHONHASHSEED`（0/1/524287）**，比对完整差异集合序列化结果的 sha256。必须跨进程换种子——同进程连跑三次共用同一种子下的迭代顺序，抓不到「用了未排序的 set」。另有 `test_重复运行的差异顺序完全一致`、`test_摘要与前提摘要稳定`、`test_同一输入渲染两次结果完全一致`，以及全量测试在 `PYTHONHASHSEED=1` 下再跑一遍。⚠️ 该工具用的是自己现场构造的一组 3 文档场景（6 条差异），**不跑 fixtures/ 下那 16 组**——16 组的跨进程一致性尚无证据 |
| 16 | 只产已声明枚举 | ✅ | `test_guards.py::TestOnlyDeclaredEnums`（`-m enum_subset`），含「不是在空集合上通过」的防退化断言 |
| 17 | 零 skip，且 MVP-1 反选数量打印进 `validation-report.md` | ✅ 机制已就位 | 零 skip 有 AST 守卫（`TestNoSkippedTests`）；`verify.sh` 在「后端全部测试」之后专门跑一步 `python -m tools.mvp1_report`，把「本轮实际执行 / `mvp1` 反选数 / 仓库内测试总数」三个数一起写进报告，**三者对不上即失败**（防止「反选 0 条」这个让人放心的方向被漏认）。**当前 `@pytest.mark.mvp1` 反选数为 0**——也就是说这套反选机制至今零用户，从未被真正验证过。⚠️ 报告文件本身要跑过 `verify.sh` 才存在 |
| 18 | README / limitations / data-model / security / comparison-rules 完整 | ✅ | 本文件即其中之一。`comparison-rules.md` 由 `python -m tools.gen_docs` 生成，同步性由 `verify.sh` 的 `--check` 一步机械保证（手写会被抓） |

### 7.1 被 `@pytest.mark.mvp1` 反选的测试

**当前反选数量：0 条。**

`pytest` 默认参数是 `-m 'not mvp1'`（见 `backend/pyproject.toml`），但仓库里目前
**没有任何测试带 `mvp1` 标记**，因此没有测试因反选而未运行。

这个数字**不是手写的**，自己跑一遍就能核对（在 `backend/` 下）：

```
.venv\Scripts\python.exe -m tools.mvp1_report
```

它用 pytest 自己的收集器分别按 `not mvp1` / `mvp1` / `mvp1 or not mvp1` 收集三次
（**不做 AST 猜测**——AST 认不出 `pytestmark = pytest.mark.mvp1` 这类写法，
一旦漏认就会把「有 40 条被反选」报成「一条都没反选」，错的方向恰好是让人放心的那个），
三个数加不上就直接失败。`scripts/verify.sh` 把这一步接在「后端全部测试」后面。

这意味着：**MVP-1 的功能不是「测试被反选」，而是「代码与测试都还不存在」**——
具体缺什么见 §8。

---

## 8. Gate-1 未完成项

Gate-1 = 原第二十二节 20 条全量 + Gate-0 全部 18 条。以下为 MVP-1 范围内**完全未开始**的部分：

| 项 | 状态 | 对应的被反选测试 |
|---|---|---|
| PDF 整线（pdfplumber 三级降级 + 文本层闸门 + bbox 归一化 + PDF.js 高亮） | ⬜ 未开始 | **无**（测试也不存在） |
| 匹配二级：客户料号映射（`MappingRule` 表） | ⬜ 未开始 | **无** |
| 匹配三级：rapidfuzz 候选 + 人工消歧 | ⬜ 未开始 | **无** |
| 其余 8 组 fixtures（含 PDF 与混合格式，补齐至 20 组） | ⬜ 未开始 | **无** |
| 解析确认页 | ⬜ 未开始 | **无** |
| Excel 导出（含公式注入防护） | ⬜ 未开始 | **无** |
| Docker | ⬜ 未开始 | **无** |
| Playwright 端到端 | ⬜ 未开始 | **无** |
| Alembic baseline | ⬜ 未开始 | **无** |
| LLM 适配器 | ⬜ 未开始 | **无**（接口形状已定死，见 [`architecture.md`](architecture.md) §12） |
| `extracted_field` / `line_item` 落库（含 `raw_cells`） | ⬜ 未开始 | **无** |

**「对应的被反选测试」全部为「无」，这是一个诚实的说明，不是遗漏**：这些功能既没有实现，
也没有写下会失败的测试，所以 `pytest` 输出里看不到它们。要判断 MVP-1 的完成度，
只能看本表，不能看测试是否全绿。

---

## 9. 相关文档

- [`README.md`](../README.md) —— 面向使用者的说明
- [`SPEC.md`](SPEC.md) —— 权威规格
- [`security.md`](security.md) —— 威胁模型与安全限制
- [`architecture.md`](architecture.md) —— 各层实现状态
- [`future-scope.md`](future-scope.md) —— 被推迟的需求及理由
- [`comparison-rules.md`](comparison-rules.md) —— 字段比较规则（由代码生成）
