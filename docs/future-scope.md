# Future Scope —— 被推迟的需求

> [`SPEC.md`](SPEC.md) §1.2 规定：**超出范围的需求 → 记入本文件，不在本版实现。**
>
> 本文是一份**账本**，不是路线图。记在这里只表示「我们知道它、我们明确决定这一版不做」，
> **不构成任何时间承诺**。
>
> 每一条都尽量写清「为什么推迟」——一份只有清单没有理由的 future-scope，
> 三个月后会被当成待办列表照单全收。

---

## 1. 第一版坚决不实现（SPEC §1.2，逐字生效）

以下清单来自 SPEC §1.2「不是什么」，**是产品边界，不是排期问题**。

### 1.1 输入格式

| 项 | 推迟到 | 理由 |
|---|---|---|
| 扫描 PDF OCR | 未排期（**不在 MVP-1**） | OCR 引入非确定性与置信度传播，需要一整套新的降级策略。落地约束见 [`architecture.md`](architecture.md) §13.2 |
| 照片识别 | 未排期 | 同上，且透视校正、光照处理是另一个领域 |
| `DOC` / `DOCX` / `XLS` | 未排期 | 真实外贸单据里占比低，收益不抵一套新解析栈的成本 |

> **文本层 PDF** 不在此列——它属 MVP-1，见 §3。

### 1.2 系统集成

| 项 | 推迟到 | 理由 |
|---|---|---|
| WhatsApp 接入 | 未排期 | 需要账号体系与消息存储，与「单机无认证」的安全模型直接冲突 |
| 邮箱接入 | 未排期 | 同上，且引入凭据管理 |
| Chatwoot 接入 | 未排期 | 同上 |
| ERP 接入 | 未排期 | 每家 ERP 的接口都不同，属定制项目而非产品功能 |
| CRM 接入 | 未排期 | 同上 |

### 1.3 自动化动作

| 项 | 推迟到 | 理由 |
|---|---|---|
| 自动改单 | **永不** | 直接违反「不得自动修改任何单据」。这是产品定义，不是能力问题 |
| 自动发信 | **永不** | 同上 |
| 自动选择正确文件 | **永不** | 直接违反「不得自动判断哪份文件正确」。这一条同时否掉了「多行求和后比较」「自动选最可能正确的候选」等一切看起来更聪明的捷径 |
| 自动批准订单 | **永不** | 同上 |

标「永不」的四条，请**不要**在后续版本里以「用户强烈要求」为由重新讨论。
它们是本工具能被信任的前提：一个可能自己改单的核对工具，用户必须复核它的每一个动作，
那还不如不用。

### 1.4 业务范围

| 项 | 推迟到 | 理由 |
|---|---|---|
| 订单管理（工作流、状态机、催办） | 未排期 | 那是另一个产品 |
| 多租户 | 未排期 | 与单机无认证模型冲突，见 §5 |
| 权限体系 | 未排期 | 同上 |
| 支付 | 未排期 | 不在范围内 |
| HS 编码 | 未排期 | 属贸易合规结论，SPEC §1.2 明确排除 |
| 出口合规筛查 | 未排期 | 同上 |
| 信用证审核 | 未排期 | 同上 |
| 生产部署 | 未排期 | 见 §5。当前安全模型（回环绑定替代鉴权）不允许部署 |

---

## 2. SPEC §4.1 推迟的文档级字段

以下字段**不进 MVP**，逐字来自 SPEC §4.1：

> 装运港、目的港、分批装运、转运、溢短装比例、有效期、原产地、品牌/商标、唛头、
> 毛净重体积、HS code、MOQ、保险、佣金、银行账户。

共 **15 项**：

| # | 字段 | 备注 |
|---|---|---|
| 1 | 装运港（Port of Loading） | |
| 2 | 目的港（Port of Discharge） | 与已实现的 `destination`（最终收货地/交货地）**语义不同**，不得混用 |
| 3 | 分批装运（Partial Shipment） | 允许 / 不允许 |
| 4 | 转运（Transshipment） | 允许 / 不允许 |
| 5 | 溢短装比例（More or Less Clause） | 如 ±5% |
| 6 | 有效期（Validity） | 报价有效期 |
| 7 | 原产地（Country of Origin） | |
| 8 | 品牌 / 商标（Brand / Trademark） | |
| 9 | 唛头（Shipping Marks） | 常为多行自由文本 |
| 10 | 毛净重体积（G.W. / N.W. / CBM） | 三个量，需要单位处理 |
| 11 | HS code | **只做提取与比对，不做合规判断**——合规结论在 §1.4 里被永久排除 |
| 12 | MOQ（最小起订量） | |
| 13 | 保险（Insurance） | 投保方、险别、投保金额比例 |
| 14 | 佣金（Commission） | |
| 15 | 银行账户（Bank Details） | **敏感信息**，落地时需要单独评估是否写入报告 |

### 2.1 为什么可以安全推迟

`extracted_field` 是 **EAV 结构**（`field_name` 是字符串列），别名走注册表，严重度走配置。

> v2 加任意文档级字段 = 一条别名 + 一条比较规则配置，**零数据库迁移、零接口变更**。

具体到本仓库：在 `backend/app/domain/fields.py` 的 `_DOCUMENT_FIELDS` 里追加一个
`FieldSpec`，`docs/comparison-rules.md` 由 `python -m tools.gen_docs` 重新生成，
其余各层一行不改。

**这就是这 15 项能被推迟而不产生技术债的硬理由。** 如果字段是硬编码的表列，
推迟就等于欠债；现在不是。

### 2.2 已在 MVP-0 实现的文档级字段（对照）

`document_number`、`document_date`、`buyer_name`、`seller_name`、`currency`、
`incoterm`、`incoterm_named_place`、`incoterm_version`、`payment_terms`、
`delivery_terms`、`destination`、`shipping_method`、`grand_total`、`remarks`。

### 2.3 一条已生效的规格修订

**`delivery_date` + `delivery_window` 已合并为 `delivery_terms`。**

原设计只能表达绝对日期，但真实报价单与 PI 上的交期九成写成
「30 days after receipt of deposit」「收到定金及确认样后 45 天」。

只存绝对日期会导致两种结果，都很糟：

- 每份报价单产出一条 `MISSING_VALUE` 假警报，或
- 把「Ship by 2026-09-15」与「30 days after deposit」直接比字符串，产出 `CRITICAL` 假警报

现在的处理与 `payment_terms` 一致：优先结构化 `lead_time_days` / `delivery_trigger` /
`absolute_date` / `raw_text`；无法可靠结构化时保留原文并标记待确认；
**两侧表述不同类时输出 `REVIEW`（需人工换算），不得输出 `VALUE_CONFLICT` / `CRITICAL`。**

---

## 3. MVP-1 范围（形状已预留，本版不实现）

以下**已经在 MVP-1 的计划内**，接口形状已定死，落地是追加而非重写。

### 3.1 PDF 整线

| 子项 | 已预留的形状 |
|---|---|
| pdfplumber 三级降级 L1 `lines/lines` → L2 `text/lines` → L3 `text/text` | `DocumentParser` Protocol；在 `pipeline.PARSERS` 追加 `PdfParser()` 即可，下游五层不改 |
| 文本层闸门（`cid_glyph_ratio` / `undecoded_ratio` > 2% 即拒） | `ParseReasonCode.UNSUPPORTED_TEXT_LAYER` 已声明 |
| bbox 归一化 0–1、原点左上 | `CoordinateSpace.PDF_PT_TOPLEFT`、`CellRef.bbox`、`ParsedPage` 已存在（MVP-0 恒为空） |
| PDF.js 高亮 | `EvidenceSourceType.PDF_TEXT` 已声明 |
| 生成 PDF fixture 需补 `reportlab` 或 `fpdf2` | pdfplumber 是只读的，这个洞在原计划里没记账 |

三条硬约束（落地时不得放宽）：

- **全部用 pdfplumber 内置策略，禁止自研行列聚类**
- **三级全失败必须显式失败**，不得返回 0 条行项目却算解析成功
- **不要**做 `printable_ratio` 白名单闸门——会误杀含 `×`、`°`、`±`、`é`、全角字母的正常文件

### 3.2 匹配二三级

| 子项 | 已预留的形状 |
|---|---|
| 客户料号映射（`MappingRule` 表） | `MatchMethod.CUSTOMER_PART_MAP`；`line_key` 已支持 `cpn:` 前缀 |
| rapidfuzz 候选匹配 | `MatchMethod.FUZZY_CANDIDATE`、`group_key_fuzzy()`、`SelectionState.CANDIDATE` |
| 人工消歧 | `SelectionState.USER_SELECTED` / `USER_REJECTED`、`MatchMethod.USER_MANUAL`、`group_key_sku_disambiguated()` |

⚠️ **rapidfuzz 仅限行项目第三级候选匹配。表头匹配环节永久禁止使用模糊匹配**——
裸别名 `price` 会命中 `Total Price`、`数量` 会命中 `箱数量`，结果是每行都报假的计算错误。

### 3.3 其余工程项

| 子项 | 说明 |
|---|---|
| 其余 fixtures（补齐至 20 组语义用例，含 PDF 与混合格式） | 当前 12 组语义 + 1 组两文件变体 + 3 组版面变体 |
| 解析确认页 | 让用户在比较前检查提取结果 |
| Excel 导出 | **落地时必须实现公式注入防护**：所有文档来源字符串强制 `cell.data_type = "s"`。不加 quotePrefix，不枚举 `+ - @ TAB` |
| Docker | 端口写 `"${HOST_BIND:-127.0.0.1}:8000:8000"`；容器内 uvicorn 用 `--host 0.0.0.0`，**边界在宿主映射** |
| Playwright 端到端 | |
| Alembic baseline | 当前用 `create_all()`，**schema 变更没有升级路径** |
| LLM 适配器 | 见 §4 |
| `extracted_field` / `line_item` 落库（含 `raw_cells`） | 当前每次从原始文件重新解析 |

---

## 4. LLM：形状已定死，实现推迟

MVP-0 **不实现任何 LLM 调用**。说得更准确一点：`backend/app/` 下**没有任何 LLM 代码**——
没有 provider、没有 `NullProvider`、没有启用开关，`GET /api/v1/health` 的
`llm_enabled` 是路由里写死的 `False`。**已经落地的只有一样东西**：
`parsers/xlsx.py` 产出的 `TextBlock.block_id`（形如 `Sheet1!B6`），
它是适配器将来唯一被允许引用的东西。

下面这个 Pydantic 模型是 SPEC §14 定死的**形状**，**目前不在代码里**，
落地时按它写（形状先定死，是因为它决定适配器的形状，事后加要重构）：

```python
# SPEC §14 的形状 —— 尚未在 backend/app/ 里实现
class LLMFieldCandidate(BaseModel):
    field_name: str
    block_id: str          # 只能指向 ParsedDocument.blocks 里已有的文本块
    confidence: Decimal
```

**适配器只能「选」不能「写」**：命中后由后端按 `block_id` 从自己的 `ParsedDocument`
取原文，再交确定性 normalizer。**凭空造值在结构上不可能。**

落地时必须同时满足（每一条都不是可选项）：

- 默认 `NullProvider`（被调用即抛）
- **启用必须靠显式配置开关。严禁以「环境变量里有 key」作为启用条件**
- CI / conftest 清空所有模型环境变量，不得注入任何密钥
  （`backend/tests/conftest.py` 当前**不动任何环境变量**——MVP-0 没有会读密钥的代码，
  所以现在没有可清的东西；这一条要和适配器同一次提交落地）
- 密钥不进日志、不进数据库
- 调用前必须提示数据边界（SPEC §15.1 第 16 条，当前因无外部调用而未实现）
- golden 断言：每条 Difference 引用的 `extraction_method` 必须在
  `{alias, layout, user_confirmed}` 白名单内——这是「LLM 没有偷偷变成必需依赖」的机器证明
  （已实现：`tests/test_guards.py::TestExtractionMethodWhitelist`）

---

## 5. 企业化与多人使用

**当前设计是单机、单用户、无认证，靠只监听 `127.0.0.1` 保护。**
以下需求都不是「加个开关」，而是一次实质的架构扩展：

| 需求 | 涉及的改动 |
|---|---|
| 身份认证 | 用户表、会话、密码策略。**SPEC §15.2 明确禁止在 MVP 阶段引入登录 / Token / HTTPS / 限流** |
| 权限体系 | 角色、资源级授权 |
| 多租户数据隔离 | 全表加租户维度，全查询加过滤，一处漏掉就是跨租户泄露 |
| 审计日志 | 谁在什么时候看了 / 改了哪条 |
| HTTPS | 证书管理 |
| 备份与恢复 | 与「删除项目会删除数据库记录与磁盘上的原始文件」的承诺存在张力，需要显式设计 |

⚠️ **在补齐上述内容之前，请不要用「把绑定地址改成 `0.0.0.0`」的方式实现多人共用。**
那样做的效果是：同一局域网内的任何人，无需任何密码就能读取、导出、删除你的全部订单数据。

如果确实需要局域网访问，正确做法是在前面加一层**带认证的反向代理**。

---

## 6. 在实现过程中新增的推迟项

以下不在原始计划里，是实现过程中发现并明确决定推迟的。**记在这里是为了它们不被遗忘。**

| 项 | 现状 | 推迟理由 |
|---|---|---|
| **解析处理超时** | `ParseLimits.timeout_seconds = 30` 字段存在但无代码使用 | **落地位置已经定了：请求层（独立进程 / worker），不在解析器内部。** 解析器内部实现不通：真正耗时的 `load_workbook` 不可中断，而往可中断的行遍历里插墙钟判断会把系统时钟读进解析路径，与 Gate-0 第 15 条「同一输入两次解析结果一致」直接冲突——一个会让确定性验收失效的超时比没有超时更糟。字段留着是因为 `README.md` §14 第 10 条、[`security.md`](security.md) §4.2、[`limitations.md`](limitations.md) §6.2 都指向它。单机场景下已有行数 / 体积 / 压缩比多重上限做间接缓解。**这是一个已知的拒绝服务风险** |
| **golden 16 组的跨进程确定性** | `tools/determinism.py` 只跑一组自己现场构造的 3 文档场景 | `docs/golden-report.md` 与 `pytest_sessionfinish` 钩子**已实现**（本行原先记的是它们不存在，已过时）。真正还缺的是：那 16 组 fixtures 只在单进程内被验过两次，换哈希种子后的逐字节一致性没有证据。把 determinism 的语料换成 fixtures/ 全量即可闭合 |
| **上传解析在请求线程内同步进行** | 无异步队列 | 与上一条相关：上传请求的耗时上界只由静态上限间接约束。单机单用户场景下接受；见 [`security.md`](security.md) §4.3 |
| **`unmapped_headers` 展示** | 数据已收集，界面未展示 | 需要界面设计（放在哪、怎么不打扰）。**这是当前最主要漏报来源的唯一可见线索**，优先级应高于多数 MVP-1 项 |
| **`weak_identity_count` 展示** | 未实现 | 同上，属界面工作 |
| **孤儿裁决清理入口** | 未实现 | 同上 |
| **依赖锁文件与供应链加固** | 无锁文件、无哈希校验、无 SBOM | 单机场景风险有限；企业环境必须补 |
| **单位换算规则表** | 刻意不做 | 没有企业级换算规则时，`1 SET = ? PCS` 是猜的。**要做就必须由企业显式配置，绝不内置默认换算** |
| **严重度表的按企业定制** | 当前是全局常量表 | 需要配置存储与界面。现阶段改 `backend/app/domain/fields.py` 即可，属改配置不改逻辑 |
| **真实单据 holdout 验证** | 未做 | **刻意不放进 MVP 验收关键路径**——它会阻塞在人工输入上。作为「下一步建议」固定写入最终报告，见 [`limitations.md`](limitations.md) §1 |

---

## 7. 如何往本文件里加东西

遇到超出范围的需求时：

1. **不要**在本版实现它
2. 在上面对应的小节加一行，写清**是什么**、**推迟到哪**、**为什么**
3. 如果它是「永不实现」（违反产品边界），明确标注，并写清违反了哪一条边界
4. 如果它需要预留形状（枚举值、字段、接口），**形状现在就加**，实现推迟

第 4 条是本项目「砍实现、保形状」策略的核心：形状便宜、事后加贵；
实现昂贵、事后加便宜。

---

## 8. 相关文档

- [`SPEC.md`](SPEC.md) §1.2、§2.3、§4.1 —— 范围边界的权威定义
- [`limitations.md`](limitations.md) —— 已经在做的部分里还差什么
- [`architecture.md`](architecture.md) §13 —— 各扩展点的预留形状与落地方式
- [`security.md`](security.md) —— 安全相关的推迟项与其风险
