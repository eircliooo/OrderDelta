# 外贸订单差异雷达 — 修订版规格书 v2

> 本文档取代原 Codex 计划，是实施的唯一权威来源。
> 原计划中被保留的部分在文中标注「保留」；被修订的部分标注「修订自原第 N 节」并给出理由。
> **凡本文档与原计划冲突，以本文档为准。**

---

## 0. 修订摘要

原计划是一份优秀的产品边界文档、一份不合格的工程规格书。本次修订只动三类东西：

1. **把停在形容词上的核心算法写成可单测的规格**——匹配基数、写入语义、表头定位。
2. **把会产生系统性假警报的默认规则改对**——严重度按链路阶段而非按字段、容差与舍入、未解释差额。
3. **给出一个可以诚实停下的中间交付点**——Gate-0 / Gate-1 两张验收门表。

原计划第三节「坚决不实现」清单、第十节标准化规则、第十三节 Evidence 设计、第二十四节执行原则**原样保留**，本文档的每一条修改都是在兑现它们，不是放宽它们。

---

## 1. 产品定义与边界

### 1.1 是什么

面向中国中小外贸企业的**订单辅助核对工具**。用户上传报价单（Quotation）、客户采购订单（Purchase Order）、形式发票（Proforma Invoice）中的**两份或三份**，系统提取字段与行项目、对齐 SKU、执行确定性比较、输出带原文证据的差异清单，供人工审核后导出报告。

### 1.2 不是什么（保留原第三节，逐字生效）

本工具**只能辅助核对**。不得自动判断哪份文件正确，不得自动批准订单，不得提供贸易合规、法律或财务结论，不得自动修改任何单据。

第一版坚决不实现：扫描 PDF OCR、照片识别、DOC/DOCX/XLS、WhatsApp/邮箱/Chatwoot/ERP/CRM 接入、自动改单、自动发信、订单管理、多租户、权限体系、支付、HS 编码、出口合规、信用证审核、自动选择正确文件、生产部署。

遇到上述需求 → 记入 `docs/future-scope.md`，不在本版实现。

### 1.3 修订：不再强制三份齐全

**修订自原第十四节 / 第二十二节第 3 条。**

理由：中小外贸的现实分布是正式报价单常常不存在（微信/邮件里报的价），价值最高的一次比对其实是 **PO ↔ PI 两份**。强制三份会让多数用户第一次试用就卡在上传页。

规格：
- 参与比较的角色集合 `R = {已上传且解析成功的角色}`，`|R| >= 2` 即可运行检查。
- **比较引擎签名中不得出现数字 3**：`compare(doc_set: Mapping[DocumentRole, ProjectSnapshotDocument], rules) -> list[Difference]`，内部对 `combinations(R, 2)` 迭代。
- **未上传或解析失败的角色不进入比较集合，因而不产生任何 Difference**（含 MISSING_VALUE）。
- API 响应必须带 `compared_roles` 与 `skipped_roles`；前端与报告首屏**强制**显示横幅：**「缺席角色 = 未检查，不等于无差异」**。这是集合驱动引入的新失效模式，不得隐藏。

---

## 2. MVP-0 / MVP-1 与两张验收门表

**修订自原第十九节 / 第二十二节。**

理由：原计划按第六至十八节逐项估算约 60–85 人天却称 MVP，且第二十二节 20 条全有全无、无中间态。没有中间态的验收，实施者在预算耗尽时的唯一出口是「把最后 20% 编出来」。

### 2.1 切分原则：砍实现，保形状

| 现在必须定死（形状） | 现在可以砍（实现） |
|---|---|
| 粒度 / 身份 / 连接键：`line_key`、`group_key`、`difference_key`、`sku_norm` 落库 | 具体算法：PDF 抽取、模糊评分参数 |
| 会写进 golden 与审核记录的枚举全集：`difference_type`、`severity`、`chain_stage`、`parse_status`、`review_status` | 整表整实体：`MappingRule`、LLM 候选表 |
| 语义承载的类型结构：四态 `Verdict`、Decimal、`CoordinateSpace`、`ParsedTable` | 编排：Docker、Alembic、Playwright |
| 跨进程契约：`/api/v1` 前缀、`{items,total}` 信封 | 追加式功能：Excel 导出、解析确认页 |

一条几乎免费的保险：**`line_item.raw_cells JSON NOT NULL`**，存本行每个单元格的 `{addr, value, data_type, formula, cached_value, merged_range}`。有了它，后续补任何字段都能**从库内回填、不必重新解析原始文件**——它把「漏列」从不可逆降级为可逆。

### 2.2 MVP-0 范围（本次交付目标）

**纯 XLSX 全链路**：上传 → 双次加载解析 → 标准化 → 表头定位与字段提取 → 一级 SKU 精确匹配 → N 元比较 → Evidence → SQLite 持久化 → 人工审核 → 自包含 HTML 报告 → 删除项目。前端两个路由。12 组 golden fixtures。

MVP-0 **只接受 `.xlsx`**。上传 `.pdf` 必须被显式拒绝，`reason_code = UNSUPPORTED_EXT`，中文提示「MVP-0 暂不支持 PDF，将在下一版本提供」。**不得假装成功，不得静默忽略。**

### 2.3 MVP-1 范围（本次不做，形状已预留）

PDF 整线（pdfplumber 三级降级 + 文本层闸门 + bbox 归一化 + PDF.js）、匹配二三级（MappingRule + rapidfuzz 候选 + 人工消歧）、其余 8 组 fixtures、解析确认页、Excel 导出、Docker、Playwright、Alembic baseline、LLM 适配器。

### 2.4 两张门表

**Gate-0 = 「可演示、可合法停止」的交付点。** Gate-0 全绿时，必须在 `docs/limitations.md` 中**逐条列出 Gate-1 未完成项及其被反选的测试名**，不得表述为「已基本完成」。

**门表条目一经开工不得下移。**

**「可合法停止」仅指范围，不指质量。** 以下理由对两张门表同等适用，一律不接受：「理论上可以运行」「代码已经写完」「由于时间原因未测试」「测试环境问题，应该没问题」「主要功能完成，边缘情况以后再说」。

**判定标准**：一个只读 README 和 limitations.md 的用户，把文件丢进去拿到「零差异」，**不会因此对任何事情产生误解**。MVP-0 不是「大圈画了一半」，而是「小圈完整填满」。

#### Gate-0 检查项

1. 干净环境可启动（Windows 原生路径）
2. 用户能完成 2 份或 3 份 XLSX 上传与检查
3. 12 组 golden 全绿
4. 所有植入的关键差异被发现（召回 100%）
5. **fixture #1「三份完全一致」的 CRITICAL 误报 = 0**（最重要的一条）
6. 全部金额计算使用 Decimal（AST 扫描测试证明域内模块无 `float(`）
7. 未对齐行不被强行匹配（`COUNT(line_item) == COUNT(match_member)` 全量断言）
8. PDF 上传被显式拒绝且带结构化 `reason_code`
9. 每个 Difference 至少关联一条 Evidence（全量断言）
10. 审核状态持久化
11. 重跑继承：修正 1 个单价后重跑，其余审核状态原样保留
12. HTML 报告断网 + 后端停机可打开；**文件内无任何外部 `http(s)://`**（机械断言）
13. 删除项目 → 库内 orphan 计数 = 0 且磁盘文件已删
14. 后端 lint / 类型检查 / 单元测试 / 集成测试通过
15. **确定性**：连跑 3 次，差异集合与全部 fingerprint 逐字节一致
16. **只产已声明枚举**：运行时产出的所有枚举值必须是声明全集的子集（`-m enum_subset`）
17. **零 skip**，且 MVP-1 测试的反选数量必须打印进 `validation-report.md`
18. README / limitations / data-model / security / comparison-rules 完整

第 15–17 条是两级门表唯一的反滥用机制，不得省略。

#### Gate-1 检查项

原计划第二十二节 20 条全量 + Gate-0 全部 18 条。

---

## 3. 领域模型

### 3.1 四类数据与写权限互斥（修订自原第六节，核心新增）

原计划允许「修正提取结果」和「替换文件」，但从未定义修正值写到哪一列、重跑时旧 Difference 与审核状态怎么办。默认实现必然是覆写 `normalized_value` + `delete-all + insert`：用户改一个单价 → 已标注的审核状态全部静默归零。而「人工审核后导出报告」正是本产品的核心价值。

**确立四类数据，写权限互斥，任何代码不得越界：**

| 类别 | 实体 | 谁能写 | 生命周期 |
|---|---|---|---|
| ① 文档所述 | `extracted_field`、`line_item` | **只有解析器** | 随 Document |
| ② 人工断言 | `user_correction` | **只有人** | 独立，锚在领域坐标 |
| ③ 计算产物 | `difference`、`match_group`、`match_member` | **只有比较引擎** | 任何时刻可整体删除重算 |
| ④ 人工裁决 | `difference_review` | **只有人** | 独立于 ③ |

**推论（必须写进 CLAUDE.md）：**
- `extracted_field.normalized_value` / `line_item.*` **永远不被用户写**。
- `difference` 表**不含** `review_status` / `review_note`。
- 重跑 = `DELETE difference / match_group / match_member` + 全量 insert，**一行 `difference_review` 都不碰**。

### 3.2 表结构

```sql
-- ① 项目
CREATE TABLE project (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL,                    -- DRAFT | READY | COMPARED
  compared_at TEXT NULL,
  comparison_input_fingerprint TEXT NULL,  -- 三段子哈希 docs:...|corrections:...|rules:...
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- 不设 STALE 状态：新鲜度与流程阶段正交，靠指纹自证，不靠写路径记得置位。

-- ② 文档
CREATE TABLE document (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  role TEXT NOT NULL,                      -- QUOTATION | PURCHASE_ORDER | PROFORMA_INVOICE
  revision INTEGER NOT NULL DEFAULT 1,
  superseded_at TEXT NULL,
  original_filename TEXT NOT NULL,
  stored_filename TEXT NOT NULL,           -- 随机 UUID，不含用户可控成分
  mime_type TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  parser_name TEXT NULL,
  parser_version TEXT NULL,
  parse_status TEXT NOT NULL,              -- 见 §5.3 冻结词表
  parse_reason_code TEXT NULL,             -- 结构化，非自由文本
  parse_diagnostics TEXT NULL,             -- JSON
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_document_active_role
  ON document(project_id, role) WHERE superseded_at IS NULL;

-- ① 文档级提取字段
CREATE TABLE extracted_field (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  raw_value TEXT NULL,                     -- 四元组补齐（原第六节只给两个，第十节要求四个）
  parsed_value TEXT NULL,
  normalized_value TEXT NULL,
  parse_warning TEXT NULL,
  value_type TEXT NOT NULL,
  confidence TEXT NULL,                    -- Decimal as TEXT
  extraction_method TEXT NOT NULL,         -- alias | layout | user_confirmed
  evidence_id TEXT NULL REFERENCES evidence(id) ON DELETE SET NULL
);
-- is_user_confirmed 移出表，改为 DTO 计算字段（= 是否存在对应 user_correction）

-- ① 行项目
CREATE TABLE line_item (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  line_key TEXT NOT NULL,                  -- 冻结身份，见 §3.3
  row_index INTEGER NOT NULL,
  internal_sku TEXT NULL,
  sku_norm TEXT NULL,                      -- 索引用
  customer_part_number TEXT NULL,
  description TEXT NULL,
  specification TEXT NULL,
  color TEXT NULL,
  quantity TEXT NULL,                      -- Decimal as TEXT，全程禁止 float
  unit TEXT NULL,
  unit_norm TEXT NULL,
  unit_price TEXT NULL,
  currency TEXT NULL,
  line_total TEXT NULL,
  packaging_quantity TEXT NULL,
  carton_count TEXT NULL,
  remarks TEXT NULL,
  raw_cells TEXT NOT NULL,                 -- JSON，见 §2.1（可逆性保险）
  confidence TEXT NULL
);
CREATE INDEX ix_line_item_sku ON line_item(document_id, sku_norm);

-- ③ 匹配组（替换原 LineItemMatch）
CREATE TABLE match_group (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  group_key TEXT NOT NULL,                 -- 纯自然键，禁止含任何 DB 主键
  match_method TEXT NOT NULL,              -- SKU_EXACT | CUSTOMER_PART_MAP | FUZZY_CANDIDATE
                                           -- | UNMATCHED | USER_MANUAL
  match_confidence TEXT NULL,
  match_reason TEXT NOT NULL,              -- 人可读匹配理由（原第十一节强制）
  role_signature TEXT NOT NULL,            -- 'Q1:P2:I0'
  multiplicity_state TEXT NOT NULL,        -- UNIQUE_PER_ROLE | MULTI_PER_ROLE
  coverage_state TEXT NOT NULL,            -- FULL | PARTIAL | ISOLATED
  status TEXT NOT NULL,
  user_decision TEXT NULL,
  UNIQUE(project_id, group_key)
);

CREATE TABLE match_member (
  id TEXT PRIMARY KEY,
  match_group_id TEXT NOT NULL REFERENCES match_group(id) ON DELETE CASCADE,
  line_item_id TEXT NOT NULL UNIQUE REFERENCES line_item(id) ON DELETE CASCADE,
  document_role TEXT NOT NULL,
  role_ordinal INTEGER NOT NULL,
  selection_state TEXT NOT NULL            -- AUTO_SELECTED | CANDIDATE | USER_SELECTED | USER_REJECTED
);
```

`match_member.line_item_id` 上的 `UNIQUE` 是完成标准「未对齐项目不会被强行匹配」的**机器证明**。配一条全量断言，同时守住它的对偶——「没有行被静默丢弃」：

```
COUNT(line_item WHERE project=P) == COUNT(match_member JOIN match_group WHERE project=P)
```

```sql
-- ③ 差异（不含审核字段）
CREATE TABLE difference (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  difference_key TEXT NOT NULL,            -- 见 §3.3
  identity_strength TEXT NOT NULL,         -- STRONG | WEAK
  scope TEXT NOT NULL,                     -- DOCUMENT | LINE_ITEM | CALCULATION
  subject_kind TEXT NOT NULL,              -- DOCUMENT_ROLE | MATCH_GROUP
  subject_key TEXT NOT NULL,
  field_name TEXT NULL,
  difference_type TEXT NOT NULL,           -- 见 §9.1
  severity TEXT NOT NULL,                  -- CRITICAL | WARNING | REVIEW | INFO
  severity_rule_id TEXT NOT NULL,          -- 可追溯到 FieldSpec 的哪一条
  chain_stage TEXT NOT NULL,               -- 见 §9.4
  baseline_role TEXT NULL,
  target_role TEXT NULL,
  values_by_document TEXT NOT NULL,        -- JSON，见 §9.6
  values_digest TEXT NOT NULL,             -- 前提摘要，用于失效判定
  has_user_input INTEGER NOT NULL DEFAULT 0,
  explanation_key TEXT NOT NULL,           -- 不存拼好的句子
  explanation_params TEXT NOT NULL,        -- JSON
  confidence TEXT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, difference_key)
);

-- 差异 ↔ 证据链接表（一条差异对多份文件的证据，单个 evidence_id 基数就是错的）
CREATE TABLE difference_evidence (
  difference_id TEXT NOT NULL REFERENCES difference(id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  PRIMARY KEY (difference_id, evidence_id)
);

-- ④ 人工裁决（刻意不设 FK）
CREATE TABLE difference_review (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  difference_key TEXT NOT NULL,
  identity_strength TEXT NOT NULL,
  review_status TEXT NOT NULL,             -- 见 §12.3
  review_note TEXT NULL,
  premise_digest TEXT NOT NULL,            -- 做出判断时的 values_digest
  premise_snapshot TEXT NOT NULL,          -- JSON，用于"你上次是基于 X 判断的"
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, difference_key)
);

-- ② 人工修正（锚在领域坐标，不锚 extracted_field.id）
CREATE TABLE user_correction (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                      -- OVERRIDE | CONFIRM
  scope TEXT NOT NULL,                     -- DOCUMENT | LINE_ITEM
  line_key TEXT NOT NULL DEFAULT '',       -- DOCUMENT scope 时为空串
  field_name TEXT NOT NULL,
  user_value TEXT NULL,
  value_type TEXT NOT NULL,
  superseded_value TEXT NULL,              -- 被覆盖的机器值，报告要能显示
  reason TEXT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, scope, line_key, field_name)
);

-- 证据
CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,               -- XLSX_CELL | XLSX_RANGE | PDF_TEXT | DERIVED
  sheet_name TEXT NULL,                    -- XLSX 定位拍平成列（要按它查询）
  cell_reference TEXT NULL,
  row_index INTEGER NULL,
  col_index INTEGER NULL,
  locator TEXT NULL,                       -- PDF 定位判别联合 JSON（只按 id 取，不查询）
  raw_text TEXT NULL,
  derived_from TEXT NULL,                  -- JSON：CALCULATION_ERROR 的证据 = 两个格 + 一个算式
  parser_metadata TEXT NULL                -- JSON
);
```

### 3.3 三个身份函数（纯函数，MVP-0 阶段 1 必须先写 + 单测）

```python
def line_key(li: ParsedLineItem) -> str:
    """由解析器确定性生成，基于解析器读数冻结，永不因人工修正改变。
    否则改 SKU 会让 user_correction 自己解锚。"""
    if li.sku_norm:            return f"sku:{li.sku_norm}#{li.sku_ordinal}"
    if li.customer_part_norm:  return f"cpn:{li.customer_part_norm}#{li.cpn_ordinal}"
    return f"pos:{li.sheet_name}!{li.row_index}"

def group_key(...) -> str:
    """全自然键，保证重复执行稳定。禁止出现任何 DB 主键。"""
    # SKU:{归一SKU}
    # SKU:{归一SKU}#{attr}={值}          消歧后
    # FUZZY:{排序拼接的 ROLE:row_index}
    # NOSKU:{ROLE}:{row_index}

def difference_key(scope, difference_type, field_name, subject_kind, subject_key) -> str:
    """sha256 前 16 字节 hex。**不含具体数值**——含了值一变 key 就变，
    恰好在最需要继承审核状态的场景失效。"""
    return sha256(f"{scope}|{difference_type}|{field_name or ''}|{subject_kind}:{subject_key}")
```

`identity_strength`：`STRONG` = subject 能由稳定自然键唯一确定；`WEAK` = 重复 SKU 的第 2 行及以后、无 SKU 无客户料号的行、`AMBIGUOUS_MATCH`。**WEAK 不继承审核状态**——宁可可见地丢，不可错挂。

**禁止**使用 `unstable:<uuid4>` 之类的占位——那会让 difference 表每跑一次内容不同，直接违反「重复执行结果稳定」。

### 3.4 SQLite 陷阱（必须处理）

`PRAGMA foreign_keys` 默认 **OFF**，SQLAlchemy 不会替你开。必须挂：

```python
@event.listens_for(Engine, "connect")
def _fk_on(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

否则上述全部 `ON DELETE CASCADE` 是装饰品，删项目会留孤儿行 + 孤儿文件，「删除项目会删除对应数据和文件」静默失效。

---

## 4. FieldSpec 注册表

**不落库**，Python 内的单一注册表。`docs/comparison-rules.md` **由它生成，禁止手写**——约 1 小时工作量，换来永不与代码漂移。

```python
@dataclass(frozen=True)
class FieldSpec:
    key: str
    value_kind: ValueKind          # TEXT | DECIMAL | CURRENCY | DATE | ENUM | STRUCTURED
    normalizer: str                # 归一化函数名
    comparator: str                # 比较器名
    tolerance: Tolerance | None
    aliases: tuple[str, ...]       # 中英文别名，见 §6.2
    severity_by_stage: Mapping[ChainStage, Severity]
    ambiguity_policy: str          # 何时降级为 REVIEW / INCOMPARABLE
```

### 4.1 文档级字段（MVP-0）

`document_number`、`document_date`、`buyer_name`、`seller_name`、`currency`、`incoterm`、`incoterm_named_place`、`incoterm_version`、`payment_terms`、`delivery_terms`、`destination`、`shipping_method`、`grand_total`、`remarks`

**修订**：原第七节的 `delivery_date` + `delivery_window` 合并为 **`delivery_terms`**，按 `payment_terms` 同款处理：

> 优先尝试结构化 `lead_time_days`、`delivery_trigger`（deposit / order / sample_approval / lc）、`absolute_date`、`raw_text`；无法可靠结构化时保留原始文本并标记待确认。

理由：真实报价单与 PI 上的交期九成写成「30 days after receipt of deposit」「收到定金及确认样后 45 天」。原设计只能表达绝对日期，每份报价单都会产出一条 MISSING_VALUE 假警报，或把「Ship by 2026-09-15」与「30 days after deposit」直接比字符串产出 CRITICAL 假警报。

`destination` 定义为**最终收货地/交货地**，与 `incoterm_named_place` 语义互斥；**两者禁止跨字段比较**。

**推入 future-scope**（不进 MVP）：装运港、目的港、分批装运、转运、溢短装比例、有效期、原产地、品牌/商标、唛头、毛净重体积、HS code、MOQ、保险、佣金、银行账户。

> 可以安全推迟的硬理由：`extracted_field` 是 EAV 结构（`field_name` 为字符串列），别名走 YAML，严重度走配置。v2 加任意文档级字段 = 一条 YAML 别名 + 一条比较规则配置，**零数据库迁移、零接口变更**。

### 4.2 行项目字段（MVP-0）

`internal_sku`、`customer_part_number`、`description`、`specification`、`color`、`quantity`、`unit`、`unit_price`、`currency`、`line_total`、`packaging_quantity`、`carton_count`、`remarks`

---

## 5. 解析层契约

### 5.1 接口（MVP-0 必须写全，约 70 行）

```python
class ParseCapability(BaseModel):
    accepted: bool
    reason_code: str | None      # UNSUPPORTED_EXT | ENCRYPTED | CORRUPT
                                 # | INSUFFICIENT_TEXT | UNSUPPORTED_TEXT_LAYER
                                 # | ROW_LIMIT | SHEET_LIMIT | NO_TABLE_FOUND

class DocumentParser(Protocol):
    name: str
    version: str
    def can_parse(self, src: DocumentInput) -> ParseCapability: ...
    def parse(self, src: DocumentInput, limits: ParseLimits) -> ParsedDocument: ...
```

`can_parse` 返回 `ParseCapability` 而非 `bool` —— 「扫描 PDF 被明确拒绝」要的正是**拒绝原因**，`bool` 承载不了。

```python
@dataclass(frozen=True)
class ParsedCell:
    value_raw: str | None
    value_typed: Any
    data_type: str
    formula: str | None          # 与 cached_value 分开存，见 §5.2
    cached_value: str | None
    ref: CellRef

@dataclass(frozen=True)
class ParsedTable:               # ← 提取层唯一入口，XLSX / PDF 共用
    table_id: str
    header_rows: tuple[int, ...]
    cells: tuple[tuple[ParsedCell, ...], ...]
    merged_ranges: tuple[str, ...]

@dataclass(frozen=True)
class ParsedDocument:
    parser_name: str
    parser_version: str
    status: ParseStatus
    reason_code: str | None
    diagnostics: Mapping[str, Any]
    tables: tuple[ParsedTable, ...]
    blocks: tuple[TextBlock, ...]
    pages: tuple[ParsedPage, ...] = ()    # MVP-0 恒为空
```

> **这段是「砍实现保形状」策略的支点。** MVP-1 的 pdfplumber 只需产出带 `page/bbox/page_w/page_h` 的 `ParsedCell` 与 `ParsedTable`，下游提取、标准化、匹配、比较、证据五层**一行不改**。省掉它、让 openpyxl 直接写领域对象，PDF 就是一次重写而非一次追加。

### 5.2 XLSX 加载协议（必须逐条执行）

openpyxl 一次 `load_workbook` 只能拿公式串**或**缓存值之一（`data_only` 互斥）。写死：

- **pass A**：`load_workbook(path, data_only=False)` —— 取公式、`ws.merged_cells.ranges`、单元格地址、`data_type`
- **pass B**：`load_workbook(path, data_only=True)` —— 只取缓存值
- **两次都禁止 `read_only=True`** —— read_only 模式下 `merged_cells` 不可用，合并大标题定位会失效
- `FORMULA_WITHOUT_CACHE` 判定 = pass A 是公式 **且** pass B 为 `None`。**禁止把「值为空」误报为「无缓存」**
- 行数 / 表数限制在 pass A 遍历时**边数边判**，**不要读 `<dimension>` 做预检**——openpyxl 官方明确警告该值常被写错，恶意文件声明 `A1:A1` 即可绕过
- **不执行宏**；只接受 `.xlsx`（`.xlsm` / `.xls` 一律拒绝）

### 5.3 `parse_status` 冻结词表

`PENDING` | `OK` | `NEEDS_REVIEW` | `REJECTED` | `FAILED`

`reason_code` 必须是**结构化字段**而非拼在中文文案里，否则 golden test 只能靠字符串匹配。

### 5.4 MVP-1 预留：PDF（本次不实现，规格先写死）

- **三级降级**：pdfplumber 默认 `vertical/horizontal_strategy` 均为 `"lines"`，无框线表（外贸 PI 常见）直接返回 `None`。固定阶梯 L1 `lines/lines` → L2 `text/lines` → L3 `text/text`，采纳判据复用别名表（表头行命中 ≥2 必需列）。**全部用内置策略，禁止自研行列聚类。** 三级全失败必须显式失败，**不得返回 0 条行项目却算解析成功**。
- **文本层闸门**：扫描件不是唯一的不可用形态。中文 PDF 常见 CID 子集字体缺 ToUnicode，pdfminer 输出 `(cid:123)` —— 字符数达标、系统照常跑完、输出一份带 Evidence 但**全错**的报告，比拒绝危险得多。加两个确定性指标 `cid_glyph_ratio`、`undecoded_ratio`，任一 > 2% 即拒，`reason_code = UNSUPPORTED_TEXT_LAYER`。
  **不要**做 `printable_ratio` 白名单闸门——会误杀含 `×`、`°`、`±`、`é`、全角字母的正常文件。
- **坐标系现在就写死**：`CoordinateSpace.PDF_PT_TOPLEFT`，bbox 存归一化 0–1 的 `{x0, top, x1, bottom}`，原点左上，禁止存 `y0/y1` 和 PDF point 原值；`page_width/height/rotation` 塞进 `parser_metadata`。`rotation != 0` 或 `cropbox != mediabox` 时置 `highlight_unavailable`，走降级路径（只显示页码 + 文本片段）。

---

## 6. 提取层

### 6.1 表头打分器（修订自原第九节第二层——原文只有一句形容词）

真实外贸单据是：3–8 行公司抬头 + 合并大标题 + 跨两行表头 + 中段空行 + 尾部小计行。不给算法规格，实现者必然退化成「假定第 1 行是表头」，真实文件 0 命中。

```
对每个 sheet：
  扫前 20 行，逐行统计命中别名表的单元格数 → score[i]
  同一循环里，为每对 (row i, row i+1) 的纵向拼接文本再打一次分 → score2[i]
      （3 行代码即覆盖双行表头与合并表头）
  header_row = argmax(score ∪ score2)

  硬门槛：至少命中 2 个不同列类，
          且必须包含「数量类」与「单价或金额类」各一
  不满足 → 不落 header_row，parse_status = NEEDS_REVIEW，
           reason_code = NO_TABLE_FOUND

数据区终止规则：
  SKU/描述列 与 数量列 同时为空或非数值 → 终止
  非空单元格 <= 2 的行 → 跳过，不产出 LineItem（滤掉小计/运费/合计行）
```

### 6.2 别名匹配算法（修订自原第九节第一层——原文没定义「匹配」是什么）

不定义就会出现：裸别名 `price` 命中 `Total Price` 成 `unit_price`、`数量` 命中 `箱数量` 成 `quantity` → **每行都报假 CALCULATION_ERROR**。

```
归一化管道：
  小写 → NFKC（统一全角半角）
       → 一条正则去掉首个 ( / （ / [ 及其后全部内容
       → 去空白与标点
再做：精确字典查找
```

- **禁止子串包含匹配**
- **表头匹配环节禁止使用 rapidfuzz**（rapidfuzz 仅限行项目第三级候选匹配，MVP-1）
- 一个表头只映射一个字段；**命中多字段或同一字段被多列命中 → 两边都不映射**，进 `unmapped_headers`
- 别名表把竞争表头**显式归位**：`total price / 金额 / 小计 → line_total`；`ctns / 箱数 → carton_count`；`装箱数量 → packaging_quantity`
- **删除裸别名 `price`**
- 保存命中的原始表头文本

**三条负例单测（必须存在）**：`Total Price` 不得命中 `unit_price`；`箱数量` 不得命中 `quantity`；`Unit Price` 不得命中 `unit`。

### 6.3 三层提取的优先级

`user_confirmed` > `alias` > `layout`。同一字段被多层抽到不同值 → 取高优先级层，低优先级值存入 `parse_warning`，不产生 Difference。

### 6.4 MVP-0 不实现 LLM

第三层 LLM 字段映射**不在 MVP-0**。但接口形状现在定死（见 §14），因为它决定适配器的形状，事后加要重构。

---

## 7. 标准化规则（保留原第十节，整节几乎不动）

原第十节是全文性价比最高的一节，格式无关、PDF 来了一行不改。**原样保留**：

- 所有金额、数量、比例使用 `Decimal`；**禁止二进制浮点**
- 保存 `raw_value` / `parsed_value` / `normalized_value` / `parse_warning` 四元组
- 支持千位分隔符、小数点、百分比、货币符号、括号负数
- SKU：去首尾空格、统一大小写、统一全角半角、可配置是否忽略空格/短横线/下划线、**不得默认删除前导零**、保留原始 SKU
- 单位别名：`PCS/PC/PIECE`、`SETS/SET`、`CTNS/CARTONS`、`KG/KGS`。**只统一同义单位，不执行跨单位换算**。SETS 与 PCS 不能自动换算，CTNS 与 PCS 不能自动换算
- 币种：支持常见 ISO 代码；**单独的 `$` 必须标记歧义**
- 日期：转 ISO 但保留原文；`08/09/2026` 类日月歧义**不得擅自确定**
- Incoterm：拆 `term` / `named_place` / `version`；**比较时不能只比 term**
- 付款条件：优先结构化 `deposit_percent` / `balance_percent` / `deposit_trigger` / `balance_trigger` / `due_days` / `raw_text`；无法可靠结构化时保留原文并标记待确认

### 7.1 新增：币种精度与舍入

```python
CURRENCY_DECIMALS = {"default": 2, "JPY": 0, "KRW": 0, "VND": 0}
ROUNDING = ROUND_HALF_UP
```

容差表达为「**该币种 1 个最小单位**」，而非硬编码 `0.01`。这一句同时修好 JPY/KRW（无小数位币种按 0.01 容差会把正确金额判错）。

---

## 8. 行项目匹配

### 8.1 匹配级别（MVP-0 只做第一级）

- **第一级：内部 SKU 精确匹配**（基于 `sku_norm`）— MVP-0
- 第二级：客户料号映射（MappingRule）— MVP-1
- 第三级：复合字段候选匹配（rapidfuzz）— MVP-1

保留原第十一节原则：**优先目标是降低错误匹配，而不是追求所有行都被匹配。**

### 8.2 基数（修订自原第六节 LineItemMatch——三固定外键是建模错误）

原设计的三个固定外键在结构上只能表达 1:1:1，而原第十一节自己把「同一 SKU 重复多行」「套装与单件」列为**必须处理**，第十六节 fixture #14 就是这个场景——**计划自己和自己矛盾**。

真实场景：报价单 1 行 SKU-A 共 150pcs，客户 PO 按颜色拆成 100pcs + 50pcs 两行。三外键模型下实现者只能写两条共用同一 `quotation_line_item_id` 的记录 → 差异引擎拿 150 分别比 100 和 50 → **两条假 VALUE_CONFLICT + 假 CALCULATION_ERROR**。

改为 `match_group` + `match_member`（见 §3.2）。

### 8.3 MVP 明确不做（红线，写进 CLAUDE.md）

**matching 层不做求和、不做拆合推断、不做套装展开。**

多成员组一律产出 `AMBIGUOUS_MATCH` 交人工。理由：多行求和后比较等于替企业裁定「分批交货 = 整批」，直接违反「不得自动判断哪份文件正确」。

配一条回归测试断言：**「2 成员单角色组产出 1 条 AMBIGUOUS_MATCH 且 0 条 VALUE_CONFLICT」**。

> 这会让 AMBIGUOUS_MATCH 变多，演示观感变差。这是自觉选择，**不要为了「看起来更聪明」回调**。

---

## 9. 比较引擎

### 9.1 `difference_type` 全集（冻结）

`VALUE_CONFLICT` | `MISSING_VALUE` | `CALCULATION_ERROR` | `UNMATCHED_LINE_ITEM` | `AMBIGUOUS_MATCH` | `SEMANTIC_DIFFERENCE` | `EXTRACTION_UNCERTAIN` | **`INCOMPARABLE`**（新增）

`INCOMPARABLE` 用于币种混合、单位换算未知、计价基准不同等**结构上无法比较**的情况。原计划第十二节要求这类情况「必须标记为无法完整计算」，却没在 `difference_type` 里给它一个值。

**它绝不能坍缩成 `DIFFERENT`（假警报）或 `EQUAL`（危险的沉默）。** 必须实际产出且 UI 可见。

### 9.2 四态比较器

```python
class Verdict(Enum):
    EQUAL = "EQUAL"
    DIFFERENT = "DIFFERENT"
    INCOMPARABLE = "INCOMPARABLE"     # 结构上无法比较
    UNCERTAIN = "UNCERTAIN"           # 低置信度提取 / 歧义
    MISSING = "MISSING"               # 该角色文档有、但字段未提取到
```

### 9.3 N 元判等（关键，最容易写错）

**跨文档：先量化再精确相等分桶，禁止 `abs(a-b) <= tol`。**

理由：容差关系**不传递**（a~b、b~c 但 a≁c），两两比较会得到自相矛盾且依赖顺序的差异集。

```
每字段收集 {role: value}
非空值按 quantize(value, currency_decimals) 分桶
桶数 > 1 → 产出【一条】VALUE_CONFLICT，values_by_document 记录全部角色的值
```

**绝不两两组合产出多条**——否则同一冲突产出 3 条，总览计数翻三倍。

**文档内算术校验（二元）** 才允许 1 个量化单位的容差。两套规则的差异必须写进 `comparison-rules.md` —— 这是最容易被实现者「统一成一套」的地方。

### 9.4 严重度按链路阶段，不按字段（本次修订在领域层面最重要的一条）

原第十二节把「数量不同、单价不同」一律定为 CRITICAL。但：

> **买方砍价（Q→PO 单价降低）+ 卖方按 PO 确认（PO→PI 一致）= 一笔成功的交易**

按原规则会产出满屏 CRITICAL，把真正致命的 PO↔PI 错误淹没在正常谈判噪音里。同理「报价单是菜单，客户只订其中一部分」是正常业务，一律 CRITICAL 会让**每一单真实订单的报告都不可用**。

```python
class ChainStage(Enum):
    OFFER_TO_ORDER        = "OFFER_TO_ORDER"          # Q → PO
    ORDER_TO_CONFIRMATION = "ORDER_TO_CONFIRMATION"   # PO → PI
    OFFER_TO_CONFIRMATION = "OFFER_TO_CONFIRMATION"   # Q → PI
    WITHIN_DOCUMENT       = "WITHIN_DOCUMENT"
```

| 字段 | Q→PO | PO→PI | Q→PI | 文档内 |
|---|---|---|---|---|
| quantity / unit_price | INFO | **CRITICAL** | WARNING | — |
| currency | WARNING | CRITICAL | CRITICAL | — |
| line_total / grand_total | INFO | CRITICAL | WARNING | **CRITICAL**（恒等式） |
| incoterm / incoterm_named_place | WARNING | CRITICAL | WARNING | — |
| payment_terms（比例） | WARNING | CRITICAL | WARNING | — |
| delivery_terms | REVIEW | WARNING | REVIEW | — |
| unit / 计价基准 | — | REVIEW(INCOMPARABLE) | REVIEW | — |
| packaging_quantity / carton_count | INFO | WARNING | INFO | — |
| specification / color | WARNING | CRITICAL | WARNING | — |
| description | REVIEW | REVIEW | REVIEW | — |
| destination | INFO | WARNING | INFO | — |
| document_date | REVIEW | REVIEW | REVIEW | — |
| customer_part_number | INFO | WARNING | INFO | — |

**SKU 存在性同理走有向表**：
- 只在 {Q} 出现 → `INFO`（报价项未被采纳，正常）
- {Q,P} 有而 PI 无 → `CRITICAL`（漏货）
- {P,I} 有而 Q 无 → `REVIEW`（未经报价的成交项）
- 只在 {P} 或只在 {I} → `CRITICAL`

**交期例外规则**：两侧表述不同类（一方相对条款、一方绝对日期）时输出 `REVIEW`（需人工换算），**不得输出 VALUE_CONFLICT / CRITICAL**。

> ⚠️ 本节会实质变更原 fixture #2 / #10 的期望值。必须在 `docs/validation-report.md` 明示为**「先于实现的规格修订」**，避免被误读为「修改测试预期制造通过」。

### 9.5 多重性与覆盖的判定矩阵

设 `R` = 本次参与比较的角色集合，`P` = 该组有有效成员的角色集合：

| 多重性 | 覆盖 | 字段比较 | 产出 |
|---|---|---|---|
| `MULTI_PER_ROLE` | 任意 | **不做** | `AMBIGUOUS_MATCH`(REVIEW)，列出全部候选行摘要 |
| `UNIQUE` | `FULL` (`P==R`) | 做 | VALUE_CONFLICT / MISSING_VALUE / INCOMPARABLE |
| `UNIQUE` | `PARTIAL` (`2<=|P|<|R|`) | **照做，覆盖 P** | 字段差异 **+** UNMATCHED_LINE_ITEM |
| `UNIQUE` | `ISOLATED` (`|P|==1`) | 无 | UNMATCHED_LINE_ITEM（严重度查 §9.4 有向表） |

**第三行是关键**：缺席不得屏蔽已存在角色之间的真冲突。Q 和 PO 都有该 SKU 且单价不一致，仅因 PI 漏了这行就不报——**这是漏报，比误报严重**。

判据分工：**多重性阻断比较（数据本身歧义）；覆盖缺口不阻断比较（只是范围问题）。**

### 9.6 `values_by_document` 结构

```json
{
  "PURCHASE_ORDER": {
    "value": "5000",
    "source": "PARSER",              // PARSER | USER_CORRECTION
    "parser_value": "5000",
    "correction_reason": null,
    "confidence": "0.95"
  },
  "PROFORMA_INVOICE": { "value": "4800", "source": "USER_CORRECTION", "parser_value": "480O", ... }
}
```

报告要发给老板和客户，**「这个数是机器读的还是人填的」必须精确到字段**。

### 9.7 金额校验

```
quantity × unit_price = line_total          （文档内，二元，容差 1 个最小单位）
sum(line_total) = grand_total               （文档内）
```

**修订**：真实 PI 几乎总有运费、折扣、模具费。原计划说「默认不包含税费/运费/折扣」，那 `sum(line_total) = grand_total` 必然失败并产生 CRITICAL 误报。

规格：检测到差额时**不判定为 CALCULATION_ERROR**，而是产出 `difference_type = CALCULATION_ERROR` 且 `severity = REVIEW`，`explanation_key = "unexplained_total_delta"`，参数带上差额金额，文案为「合计与行金额之和存在未解释差额 X，可能来自运费/折扣/税费」。

以下情况必须标记为无法完整计算（`INCOMPARABLE`）：存在未识别费用、币种混合、单位换算未知、数量或单价缺失、折扣规则无法理解。

`scope = CALCULATION` 的算术校验挂在**单份文档**上，**永不被匹配状态阻断**——一份文件自己的行金额算错，不该因为对不上另一份而漏报。

### 9.8 `MISSING_VALUE` 语义收紧

**仅用于「该角色文档已上传且解析成功、但该字段未提取到」。** 未上传/解析失败的角色不进入比较集合，不产生任何 Difference。

否则两文件场景会被几十条假缺失淹没。

---

## 10. Evidence（保留原第十三节）

每个 Difference **必须**至少关联一条 Evidence（Gate-0 全量断言）。

用户必须能看到：文件名、文档角色、工作表、单元格地址、原始文本、标准化值、提取方法、提取置信度。

XLSX：显示工作表名 + 单元格地址 + 附近几行几列的只读上下文。**不执行或修改原 Excel。**

**保留原文授权**：「不得因为高亮尚未实现而省略证据数据模型」——这是本文档「砍实现保形状」策略的依据。

`CALCULATION_ERROR` 的 Evidence 走 `source_type = DERIVED`，`derived_from` 记录参与运算的两个单元格 + 算式。

---

## 11. 人工修正与重跑语义

### 11.1 修正写入

`user_correction` **锚在领域坐标上**（`document_id + scope + line_key + field_name`），**不锚 `extracted_field.id`**。

理由：重解析同一文件时主键全换新，锚主键 = 所有修正静默变孤儿。锚领域坐标免费得到正确行为：重解析 → 修正存活；替换文件 → 随 Document 级联消失。

### 11.2 取值入口

**全代码库唯一取值入口**：

```python
ValueResolver.snapshot(project_id) -> ProjectSnapshot   # 无 ORM 对象
compare(snapshot, rules) -> list[Difference]            # 纯函数
```

配一条 **import 级架构边界测试**：`app.comparison` / `app.matching` / `app.exports` **不得 import `app.db.models`**。

### 11.3 重跑

```
重跑 = DELETE difference / difference_evidence / match_group / match_member
     + 全量 insert
     + 一行 difference_review 都不碰
```

读取时按 `difference_key` 合成：

| 情况 | 行为 |
|---|---|
| 前提未变（`values_digest == premise_digest`） | 沿用原 `review_status` + `review_note` |
| 前提已变 | 置 `NEEDS_CONFIRMATION`，**保留备注**，展示「你上次是基于 PO 5000 / PI 4800 判断的」 |
| `identity_strength = WEAK` | **不继承**（宁可可见地丢，不可错挂），前端报 `weak_identity_count` |
| 本次未产生该 key | `difference_review` 行保留为孤儿，提供「清理」按钮 |

`difference_review.difference_key` 是**逻辑外键，DB 层无引用完整性**。这是有意的解耦代价——加 FK 就等于把裁决绑回产物的生命周期。

**集成测试（Gate-0 第 11 条）**：审核 20 条 → 修正 1 个单价 → 重跑 → 19 条状态原样保留，1 条置 `NEEDS_CONFIRMATION` 且备注保留。

---

## 12. API

### 12.1 约定

- 前缀 `/api/v1`
- 列表统一信封 `{items: [...], total: n}`
- 前端 API base 用**相对路径 `/api`** + Vite proxy → **CORS 中间件完全不要**
  （这是唯一有返工代价的一条：硬编码 `http://localhost:8000` 后改同源代理要动每个调用点）
- 错误响应 `{error_code, message, detail?}`；**message 不得含服务器绝对路径**

### 12.2 端点（MVP-0）

```
GET    /api/v1/health
POST   /api/v1/projects                     创建项目
GET    /api/v1/projects                     列表（含差异计数、状态）
GET    /api/v1/projects/{id}
DELETE /api/v1/projects/{id}                级联删库 + 删盘
POST   /api/v1/projects/{id}/documents      上传（multipart，带 role）
DELETE /api/v1/projects/{id}/documents/{did}
POST   /api/v1/projects/{id}/compare        运行检查（同步）
GET    /api/v1/projects/{id}/differences    筛选：severity/type/sku/review_status/role
GET    /api/v1/differences/{id}/evidence
PUT    /api/v1/projects/{id}/reviews/{difference_key}   审核裁决
POST   /api/v1/projects/{id}/corrections    人工修正
GET    /api/v1/projects/{id}/report.html    自包含 HTML 报告
```

### 12.3 `review_status` 冻结词表

`OPEN` | `CONFIRMED_DIFFERENCE` | `ACCEPTED_DIFFERENCE` | `NEEDS_CONFIRMATION` | `IGNORED` | `RESOLVED`

---

## 13. 前端

### 13.1 界面语言（原计划全篇未规定 —— 会产出英文界面给中国业务员）

**界面、Excel、HTML 报告为中文单语。禁止引入任何 i18n 框架（含 react-i18next）。**

API / DB / golden 只用英文枚举标识符；前后端各一份「枚举 → 中文」映射表。

`explanation` 由 `explanation_key + explanation_params` 在展示层渲染成中文，**不存拼好的句子**。**golden tests 不得对 explanation 文本做字符串比对**（否则措辞微调红一片）。

### 13.2 路由（MVP-0 两个）

1. `/projects` —— 项目列表：创建、历史、删除、状态与差异计数
2. `/projects/:id/differences` —— 单页承载：
   - 三槽位上传（Quotation 标「可选」，按钮 disabled 条件 `count >= 2`）
   - 覆盖横幅「缺席角色 = 未检查，不等于无差异」
   - 总览计数芯片**兼作筛选器**（CRITICAL / WARNING / REVIEW / INFO / 待确认）
   - 差异表：按风险等级、差异类型、SKU、审核状态、文档角色筛选
   - 证据抽屉：并排显示各角色证据（文件名/角色/工作表/单元格/原文/标准化值/方法/置信度）
   - 审核操作 + 备注
   - 导出 HTML

MVP-1 再拆出解析确认页。

---

## 14. LLM 边界（MVP-0 不实现，形状定死）

```python
class LLMFieldCandidate(BaseModel):
    field_name: str
    block_id: str          # 指向 ParsedDocument 里已有的文本块
    confidence: Decimal
```

**适配器只能「选」不能「写」**：命中后由后端从自己的 `ParsedDocument` 按 `block_id` 取原文，再交确定性 normalizer。**凭空造值在结构上不可能。**

这一个设计同时解决三个问题：提示注入（模型输出只能是已有块的引用）、LLM 输出没有 Evidence（block_id 天然带定位）、「LLM 只能提出候选不得决定正确值」。

其余约束：
- 默认 `NullProvider`（被调用即抛）
- **严禁以「环境变量里有 key」作为启用条件**，必须显式配置开关
- CI / conftest 清空所有模型环境变量，不得注入任何密钥
- 密钥不进日志、不进数据库
- golden 加断言：**每条 Difference 引用的 `extracted_field.extraction_method` 必须在 `{alias, layout, user_confirmed}` 白名单内**——这条是「LLM 没有偷偷变成必需依赖」的机器证明

---

## 15. 安全

### 15.1 保留原第十五节全部十八条

扩展名 + MIME 双重检查、文件大小限制、随机内部文件名、原始文件名仅作元数据、防路径穿越、禁止用户控制服务器路径、上传目录非静态可执行、不执行宏、不执行 PDF 脚本、密钥不入日志、错误不泄露绝对路径、日志不记完整订单内容、删除项目安全删除文件、无遥测、默认不调外部模型、外部调用前提示数据边界、处理超时与页数行数上限、恶意文件失败测试。

### 15.2 新增四条（原计划全部未覆盖）

1. **Excel 公式注入**：导出报告时所有**文档来源字符串**走同一个 helper，`cell.value = s` 后强制 `cell.data_type = "s"`。
   openpyxl 对 `=` 开头字符串自动置公式类型是**默认行为**。比安全场景更硬的理由：客户报价单里 `line_total` 写成 `=D5*E5` 极常见，作为证据文本写进报告后会被 Excel 按报告自己的网格**重新求值**，在声称「引用原文」的位置静默显示一个错数字。
   **不要**加 quotePrefix，**不要**枚举 `+ - @ TAB`。

2. **HTML 报告**：Jinja2 显式 `autoescape=True`（**默认是 False**）；`<head>` 内嵌
   `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">`；
   **报告零 JS**（折叠只用 `<details>`）。

3. **绑定地址**：`docker-compose.yml` 端口写 `"${HOST_BIND:-127.0.0.1}:8000:8000"`（容器内 uvicorn 仍须 `--host 0.0.0.0`，边界在宿主映射）。Windows 原生启动同样默认 `127.0.0.1`。
   `.env.example` 注释逐字写明：**「改成 0.0.0.0 会让同一局域网内任何人无需密码打开你的订单数据」**。
   **禁止由此引入登录 / Token / HTTPS / 限流**——回环绑定就是本 MVP 替代鉴权的廉价手段。

4. **CORS**：用相对路径 + Vite proxy 免掉。若确需，只允许显式列表，**禁止 `"*"`**。

### 15.3 「安全删除」措辞修正

在 SQLite + 普通文件系统 + SSD 上**无法真正安全擦除**。README 与 security.md 一律表述为「删除项目会删除数据库记录与磁盘上的原始文件」，**不得宣称「安全擦除」「不可恢复」**。

---

## 16. Fixtures 与测试

### 16.1 生成策略（修订自原第十九节阶段 1）

原计划要求阶段 1 先建 20 组 fixtures 和失败测试。**必须改**：比较规则未定义时批量生成会造成整批 `expected.json` 返工。理由不是省工作量，是避免返工。

- **阶段 2**：手写 **2 组** fixture 打通全链路
- **阶段 3**：规则稳定后生成其余 **10 组**（MVP-0 共 12 组），含 3 组版面变体（抬头+合并标题 / 纯中文表头 / 表在第二个 sheet，**复用其他组的 expected.json**）
- MVP-1 补齐至 20 组（含 PDF 与混合格式）

生成器固定随机种子，**二次生成 sha256 必须一致**。

### 16.2 MVP-0 的 12 组

1. 三份完全一致（**CRITICAL 误报必须为 0**）
2. PO 数量变化
3. PI 单价错误
4. 币种不一致
5. Incoterm 不一致
6. Incoterm 地点不一致
7. 付款比例不一致
8. PO 新增 SKU
9. PI 遗漏 SKU
10. 行顺序不同
11. 同一 SKU 重复行（验证 AMBIGUOUS_MATCH 且 0 条 VALUE_CONFLICT）
12. 行金额 / 总金额计算错误

外加 **2 份文件（PO+PI）** 变体一组（由生成器 `omit_roles` 参数产出）。

### 16.3 断言策略（修订自原第十七节）

原方案若按「差异总数完全相等」断言，任何规则微调都会让 12 组同时红，实施者的自然反应是改期望值——**正是原第二十四节禁止的行为**。

```
必须命中的关键差异集合  → 硬断言（缺一即失败）
非植入 CRITICAL 误报数  → 硬上限 = 0
差异总数                → 快照，允许显式更新（更新必须在 commit message 说明理由）
```

### 16.4 确定性

- 对外输出按 `(scope, subject_key, field_name, difference_type)` 稳定排序
- golden 序列化走 `to_golden()` 白名单函数：剔除 id / 时间戳，Decimal 一律 `format(d, 'f')`
- golden 子集跑两遍：一次默认、一次 `PYTHONHASHSEED=1`
- **golden 比较必须顺序敏感，禁止 `sorted(actual) == sorted(expected)` 绕过**

### 16.5 自产 fixture 免责声明（必须逐字写进 limitations.md 与 validation-report.md）

> 本项目全部 fixture 由 `backend/tools/fixtures/build.py` 程序化自产，生成器与提取器共享同一套别名表与列布局假设。**Golden 通过率只证明确定性比较逻辑与解析管线的自洽与稳定，不代表对真实客户文件的提取准确率。** 真实准确率在接入真实语料前未知，且不得对外宣称。

不写这一句，整张验收门表就是循环论证。

**不要**把「真实单据 holdout 验证」放进 MVP 验收关键路径（会阻塞在人工输入上）。它作为「下一步建议」固定写入最终报告。

### 16.6 解析器脏样本单测

**手写 3 个脏 XLSX** 作为解析器单测输入（不给 12 组语义 fixtures 加版面扰动参数，那是另一个子项目）：

- A：前 5 行公司抬头 + 合并大标题
- B：双行表头 + 单位行
- C：中段空行 + 尾部小计/运费/合计 + 右侧备注列

只断言 `header_row`、`column_map`、`LineItem` 条数（**小计行不得进 LineItem**）。

---

## 17. 验证证据

原第二十三节要求列出「每条命令、退出码」和 20 组 × 6 列指标，而 `docs/validation-report.md` 从未说明由谁生成 → 默认路径是**事后追述**，产出「看起来非常可信且无法被反驳」的数字。

规格：

- 写 `scripts/verify.sh`（`verify.ps1` 只做 3 行转发，不维护两套）
- `run()` 函数把每步 stdout/stderr 和 `${PIPESTATUS[0]}` **追加进** `docs/validation-report.md`
- **失败不中断**，末尾按 `$FAILED` 返回单一退出码
- 12 组表格由 pytest `pytest_sessionfinish` 钩子（约 15 行）写 `docs/golden-report.md`
- **最终报告的「验证证据」与「测试数据结果」两节，必须是这两个文件的原样粘贴。报告中出现任何未在这两个文件中出现的命令或数字，视为交付失败。**

---

## 18. 硬约束清单（写进 CLAUDE.md，逐条可机械检查）

1. 界面、HTML 报告为中文单语；不引入任何 i18n 框架；API / DB / golden 只用英文枚举标识符
2. 默认绑定 `127.0.0.1`；MVP 无任何身份认证，禁止默认对外暴露
3. 导出报告中所有文档来源文本强制 `cell.data_type = "s"`；HTML 模板 `autoescape=True` + CSP meta + 零 JS
4. **fixture #1「完全一致」的 CRITICAL 误报必须为 0**
5. 对外输出与 golden 序列化前必须稳定排序；golden 比较顺序敏感，禁止忽略顺序
6. `PRAGMA foreign_keys=ON` 必须显式挂 connect 事件
7. 域内模块禁止出现 `float(`，用 AST 扫描测试强制，不靠 code review
8. `app.comparison` / `app.matching` / `app.exports` 禁止 import ORM 模型（import 级边界测试）
9. 表头匹配只做归一化后精确字典查找；rapidfuzz 仅限行项目第三级候选
10. XLSX 必须两次 `load_workbook`，永不开 `read_only`；`formula` 与 `cached_value` 分开存
11. PDF（MVP-1）三级降级全失败必须显式失败，不得返回 0 条行项目却算解析成功
12. `docs/comparison-rules.md` 由 FieldSpec 注册表生成，禁止手写
13. 最终报告的验证证据必须是 `verify` 与 pytest 产物的原样粘贴
14. `limitations.md` 必须逐字包含 §16.5 自产 fixture 免责声明
15. LLM 启用必须靠显式配置开关，严禁以「环境变量里有 key」作为启用条件
16. `pytest` 输出零 skip；MVP-1 测试以标记反选且必须打印反选数量
17. matching 层不做求和、不做拆合推断、不做套装展开
18. `difference` 表不含 `review_status` / `review_note`；`normalized_value` 永不被用户写

---

## 19. 阶段计划

| 阶段 | 内容 | 验收信号 |
|---|---|---|
| **0 环境** | 装 Python 3.12（winget，msstore 源在中国大陆超时，须 `--source winget`）；git init；目录骨架；`.gitignore`；`CLAUDE.md` | 真实 `python -V` / `pip -V` stdout 进 validation-report |
| **1 身份与契约** | 三个身份函数 + 单测；FieldSpec 注册表；四态 Verdict；`DocumentParser` Protocol + `ParsedTable`；schema（含 `PRAGMA foreign_keys=ON`） | 身份函数对「行序不同」「同 SKU 重复」稳定；`comparison-rules.md` 由注册表生成后 `git diff` 为空 |
| **2 XLSX 纵向闭环** | 双次加载解析 → 标准化 → 表头打分 → 一级匹配 → N 元比较 → Evidence → SQLite；手写 2 组 fixture | 2 组端到端跑通；3 个脏 XLSX 单测绿 |
| **3 批量 fixtures** | 生成其余 10 组 + 2 文件变体 | 12 组 golden 全绿；#1 组 CRITICAL 误报 = 0；固定种子二次生成 sha256 一致 |
| **4 API + 审核 + 重跑** | `/api/v1` 全套；`user_correction` / `difference_review` / 前提失效 | 「审核 20 条 → 改 1 个单价 → 重跑」：19 条保留，1 条 NEEDS_CONFIRMATION 且备注保留 |
| **5 前端 + HTML 报告** | 两个路由；中文单语；覆盖横幅 | 2 份与 3 份两条路径各跑一次；HTML 断网 + 后端停机可打开，无外部 `http(s)://` |
| **6 安全 + 删除 + 文档** | 恶意输入拒绝；输出编码；回环绑定；删项目清库清盘；全部文档 | 恶意输入测试绿；orphan 计数 = 0 |
| **▶ Gate-0** | **可演示、可合法停止** | §2.4 的 18 条逐条报告 + Gate-1 未完成项及被反选测试名 + 零 skip 证明 |
| 7–9（MVP-1） | PDF 整线（技术栈需补 **reportlab/fpdf2** 生成 PDF fixture——原第四节只有只读的 pdfplumber，第十六节却要求程序化生成 PDF，这个洞原计划没记账）；匹配二三级 + 解析确认页 + Excel 导出；Docker + Playwright + Alembic + LLM | Gate-1 = 原第二十二节 20 条全量 |

---

## 20. 保留不动的部分（明确保护，禁止在「优化」中被简化掉）

- **原第二十四节全部十条执行原则**，尤其「无法判断时显式失败」「低误匹配优先于高匹配率」「不通过隐藏错误、减少测试或修改测试预期来制造通过结果」
- **原第二十二节末尾的禁止性措辞**——加了两张门表之后更需要它，同时约束 Gate-0 与 Gate-1
- **原第三节「坚决不实现」完整清单** + 「记录到 future-scope.md」
- **原第十节标准化规则整节**——全文性价比最高，格式无关
- **原第十三节 Evidence 设计**，特别是「不得因为高亮尚未实现而省略证据数据模型」
- **原第十一节「优先目标是降低错误匹配，而不是追求所有行都被匹配」**
- **原第十六节「程序化生成 + 固定随机种子 + 不依赖真实客户文件」**
- **「本工具只能辅助核对，不得自动判断哪份文件正确」**——这句直接否掉「多行求和后比较」「自动选最可能正确的候选」等一切看起来更聪明的捷径
