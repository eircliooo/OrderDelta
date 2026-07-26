# CLAUDE.md — 外贸订单差异雷达

**规格权威来源：`docs/SPEC.md`。本文件只记命令、边界和坑。**

**接手这个仓库请先读 `docs/handoff.md`**（当前进度、已知缺陷、git 零提交状态）。

当前交付目标：**MVP-0 = 纯 XLSX 全链路**。PDF 属 MVP-1，接口形状已预留但不实现。

---

## 启动与测试命令

Python 3.12 未加入 PATH，用 venv 内的解释器（Windows）：

```
backend\.venv\Scripts\python.exe
```

| 用途 | 命令（在 `backend/` 下） |
|---|---|
| 装依赖 | `.venv\Scripts\python.exe -m pip install -e ".[dev]"` |
| 起后端 | `.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` |
| 单元+集成测试 | `.venv\Scripts\python.exe -m pytest` |
| lint | `.venv\Scripts\python.exe -m ruff check .` |
| 格式化 | `.venv\Scripts\python.exe -m ruff format .`（`--check` 只校验，进 verify.sh） |
| 类型检查 | `.venv\Scripts\python.exe -m mypy app` |
| 生成规则文档 | `.venv\Scripts\python.exe -m tools.gen_docs`（`--check` 只校验同步） |
| 生成 fixtures | `.venv\Scripts\python.exe -m tools.fixtures.build` |
| 零 skip / MVP-1 反选数量 | `.venv\Scripts\python.exe -m tools.mvp1_report`（坑 #14 的机械检查） |
| 跨进程确定性 | `.venv\Scripts\python.exe -m tools.determinism`（3 个进程 3 个哈希种子） |
| 全量验证 + 生成证据 | `bash scripts/verify.sh`（Windows: `scripts\verify.ps1`） |

前端（在 `frontend/` 下）：`npm ci` / `npm run dev` / `npm run build` / `npm run lint` / `npm run typecheck` / `npm test`

两份文档是**跑出来的，禁止手写**：`docs/validation-report.md` 由 `verify.sh` 写，
`docs/golden-report.md` 由 pytest 的 `pytest_sessionfinish` 钩子写（只在 16 组 golden
全部跑过时才落盘，定向跑一组不会覆盖）。SPEC §17：最终交付报告的「验证证据」与
「测试数据结果」两节必须是这两个文件的原样粘贴。

---

## 架构边界（违反即架构错误，有测试强制）

- `app.comparison` / `app.matching` / `app.exports` **禁止 import `app.db.models`**。
  比较是纯函数：**全库唯一取值入口**是 `app/extraction/snapshot.py::build_project_snapshot()`
  → `ProjectSnapshot`（服务层入口 `services/projects.py::build_result()`）。
  SPEC §11.2 把它写作 `ValueResolver.snapshot(project_id)`，**本仓库没有这个类**，只有名字不同。
- 域内模块**禁止出现 `float(`**。金额、数量、比例全程 `Decimal`。有 AST 扫描测试。
- **matching 层不做求和、不做拆合推断、不做套装展开。** 多成员组一律 `AMBIGUOUS_MATCH` 交人工。
- `difference` 表**不含** `review_status` / `review_note`（审核裁决在 `difference_review`，独立生命周期）。
- `extracted_field.normalized_value` / `line_item.*` **永不被用户写**（人工修正进 `user_correction`）。

### 四类数据与写权限

| 类别 | 实体 | 谁能写 |
|---|---|---|
| ① 文档所述 | `extracted_field`、`line_item` | 只有解析器 |
| ② 人工断言 | `user_correction` | 只有人（锚领域坐标，不锚主键） |
| ③ 计算产物 | `difference`、`match_group`、`match_member` | 只有比较引擎，可整体删除重算 |
| ④ 人工裁决 | `difference_review` | 只有人，重跑时一行都不碰 |

---

## 安全红线

- 默认绑定 **`127.0.0.1`**。MVP 无鉴权——回环绑定就是替代鉴权的手段，**禁止引入登录/Token/HTTPS/限流**。
- 导出 Excel：所有文档来源字符串必须 `cell.data_type = "s"`。openpyxl 对 `=` 开头字符串**默认置为公式**，客户单据里 `=D5*E5` 极常见，不处理会在「引用原文」处静默显示错数字。
- HTML 报告：Jinja2 **显式 `autoescape=True`**（默认 False）+ CSP meta + **零 JS**。
- 不执行宏；只接受 `.xlsx`（`.xlsm`/`.xls` 拒绝）。
- 上传文件用随机 UUID 存名，原文件名只作元数据。
- 错误信息不含服务器绝对路径；日志不记完整订单内容；密钥不入日志/数据库。
- 措辞：只能说「删除数据库记录与磁盘文件」，**不得宣称「安全擦除」「不可恢复」**。

---

## 容易踩的坑

1. **openpyxl `data_only` 互斥** → 必须 `load_workbook` 两次：pass A `data_only=False` 取公式+`merged_cells`+`data_type`，pass B `data_only=True` 取缓存值。**两次都禁止 `read_only=True`**（read_only 下 `merged_cells` 不可用）。
2. **`FORMULA_WITHOUT_CACHE`** = pass A 是公式 **且** pass B 为 `None`。别把「值为空」误报成「无缓存」。
3. **不要读 `<dimension>` 预检行数** —— openpyxl 官方警告该值常被写错，恶意文件声明 `A1:A1` 即可绕过。要边遍历边数。
4. **`PRAGMA foreign_keys` 默认 OFF**，SQLAlchemy 不会替你开。必须挂 `@event.listens_for(Engine, "connect")`，否则所有 `ON DELETE CASCADE` 是装饰品，删项目留孤儿。
5. **跨文档判等：先量化再分桶，禁止 `abs(a-b) <= tol`。** 容差关系不传递，两两比较会得出自相矛盾且依赖顺序的结果。文档内算术校验（二元）才允许 1 个最小单位容差。
6. **N 元收集，绝不两两组合产出多条差异** —— 否则同一冲突出 3 条，总览计数翻三倍。
7. **表头别名只做归一化后精确字典查找**，禁止子串包含，禁止在表头环节用 rapidfuzz。裸别名 `price` 会命中 `Total Price`、`数量` 会命中 `箱数量`，每行都报假 `CALCULATION_ERROR`。
8. **严重度按 `chain_stage` 查表，不按字段。** 买方砍价（Q→PO 降价）是正常业务，一律 CRITICAL 会淹没真正致命的 PO↔PI 错误。
9. **`sum(line_total) != grand_total`** 不判 CALCULATION_ERROR/CRITICAL，而是 `REVIEW` +「存在未解释差额 X」（真实 PI 几乎总有运费/折扣/模具费）。
10. **`difference_key` 不含具体数值** —— 含了值一变 key 就变，恰好在最需要继承审核状态时失效。
11. **`line_key` 基于解析器读数冻结**，永不因人工修正改变，否则改 SKU 会让修正记录自己解锚。
12. **golden 比较必须顺序敏感**，禁止 `sorted(actual) == sorted(expected)` 绕过。
13. **界面/报告中文单语**，禁止引入 i18n 框架；API/DB/golden 只用英文枚举标识符。
14. **零 skip**。MVP-1 测试用 `@pytest.mark.mvp1` 反选，反选数量必须打印进 validation-report。
15. winget 装 Python 在中国大陆要加 `--source winget`（msstore 源会超时）。

---

## 不要做的事

- 不实现 OCR / 扫描件 / 照片 / DOC / DOCX / XLS。
- 不自动判断哪份文件正确，不自动批准订单，不自动改单。
- 不引入 Redis / Celery / Kafka / K8s / 向量库 / 微服务。
- 不为提高匹配率牺牲误匹配率。
- 不通过隐藏错误、减少测试或修改测试预期来制造通过结果。
- 超出范围的需求 → 记入 `docs/future-scope.md`，不在本版实现。
