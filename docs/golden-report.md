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

---

## 逐组明细

### `currency_mismatch`

PI 币种写成 EUR，与 Q/PO 的 USD 不一致：金额结构上无法比较

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | currency | VALUE_CONFLICT | CRITICAL | ✅ |
| DOCUMENT | `PROJECT` | grand_total | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-100` | line_total | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-100` | unit_price | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-200` | line_total | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-200` | unit_price | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-300` | line_total | INCOMPARABLE | REVIEW | ✅ |
| LINE_ITEM | `SKU:AB-300` | unit_price | INCOMPARABLE | REVIEW | ✅ |

### `duplicate_sku`

PO 把 AB-100 拆成 600+400 两行：只能产出 AMBIGUOUS_MATCH 交人工

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| LINE_ITEM | `SKU:AB-100` | — | AMBIGUOUS_MATCH | REVIEW | ✅ |

### `identical`

三份单据内容逐字一致：期望零差异，CRITICAL 误报必须为 0

本组期望零差异，实际产出 **0 条**。

### `incoterm_mismatch`

PI 的贸易术语写成 CIF，与 Q/PO 的 FOB 不一致

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | incoterm | VALUE_CONFLICT | CRITICAL | ✅ |

### `incoterm_place_mismatch`

贸易术语同为 FOB 但地点不同：Q/PO 是 Ningbo，PI 是 Shanghai

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | incoterm_named_place | VALUE_CONFLICT | CRITICAL | ✅ |

### `layout_chinese_headers`

版面变体：纯中文标签与表头；语义同 payment_terms_mismatch

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | payment_terms | VALUE_CONFLICT | CRITICAL | ✅ |

### `layout_merged_title`

版面变体：公司抬头 + 合并大标题；语义同 identical，期望仍是零差异

本组期望零差异，实际产出 **0 条**。

### `layout_second_sheet`

版面变体：订单表在第二个 sheet；语义同 pi_unit_price_wrong

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| CALCULATION | `PROFORMA_INVOICE#sku:AB-200#1` | line_total | CALCULATION_ERROR | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-200` | unit_price | VALUE_CONFLICT | CRITICAL | ✅ |

### `line_total_wrong`

PI 的 AB-200 行金额写成 1300.00（正确值 1200.00），总金额未同步

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| CALCULATION | `PROFORMA_INVOICE` | grand_total | CALCULATION_ERROR | REVIEW | ✅ |
| CALCULATION | `PROFORMA_INVOICE#sku:AB-200#1` | line_total | CALCULATION_ERROR | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-200` | line_total | VALUE_CONFLICT | CRITICAL | ✅ |

### `payment_terms_mismatch`

付款比例不一致：Q/PO 为 30/70，PI 为 50/50

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | payment_terms | VALUE_CONFLICT | CRITICAL | ✅ |

### `pi_missing_sku`

PI 漏掉了已下单的 AB-300（漏货）

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | grand_total | VALUE_CONFLICT | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-300` | internal_sku | UNMATCHED_LINE_ITEM | CRITICAL | ✅ |

### `pi_unit_price_wrong`

PI 把 AB-200 单价改成 2.50 却没改行金额（真实世界最常见的一种错）

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| CALCULATION | `PROFORMA_INVOICE#sku:AB-200#1` | line_total | CALCULATION_ERROR | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-200` | unit_price | VALUE_CONFLICT | CRITICAL | ✅ |

### `po_extra_sku`

PO 比 Q/PI 多了一个 AB-400（客户临时加订）

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | grand_total | VALUE_CONFLICT | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-400` | internal_sku | UNMATCHED_LINE_ITEM | CRITICAL | ✅ |

### `po_quantity_changed`

客户 PO 把 AB-100 从 1000 改成 1200（PO 自身金额已同步）

| 范围 | 主体 | 字段 | 差异类型 | 风险等级 | 植入 |
|---|---|---|---|---|---|
| DOCUMENT | `PROJECT` | grand_total | VALUE_CONFLICT | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-100` | line_total | VALUE_CONFLICT | CRITICAL | ✅ |
| LINE_ITEM | `SKU:AB-100` | quantity | VALUE_CONFLICT | CRITICAL | ✅ |

### `row_order_shuffled`

三份内容相同但行顺序不同：顺序不该产生任何差异

本组期望零差异，实际产出 **0 条**。

### `two_docs_only`

只上传 PO + PI 两份且内容一致：缺席的报价单不得产生任何差异

本组期望零差异，实际产出 **0 条**。
