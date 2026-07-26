# 字段比较规则

> **本文件由 `app/domain/fields.py` 的 FieldSpec 注册表自动生成（硬约束 #12）。禁止手写。**
> 重新生成（在 `backend/` 下）：`python -m tools.gen_docs`

严重度按**链路阶段**查表，不按字段（SPEC §9.4）：

| 阶段 | 含义 |
|---|---|
| Q→PO | 报价单 → 客户采购订单：买方下单行为，砍价/改量是正常业务 |
| PO→PI | 客户采购订单 → 形式发票：**卖方确认环节，错了直接损失** |
| Q→PI | 报价单 → 形式发票：跳过 PO 的旁证，参考价值中等 |
| 文档内 | 单份文档内部的恒等式校验（数量×单价=金额、Σ行金额=总金额） |

---

## 行项目字段

### `internal_sku` — 内部型号

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=INFO / PO→PI=CRITICAL / Q→PI=REVIEW
- **容差**：归一化后精确相等（大小写/全角半角统一，不删前导零）
- **不确定情况**：无
- **示例**：AB-100 与 ab-100 视为同一 SKU；0012 与 12 不是同一 SKU
- **别名**：sku、item no、item no.、item code、item#、model、model no、model no.、art no、art. no、article no、product code、product no、part no、ref no、型号、货号、产品编号、编号、我司型号、内部型号、产品型号

### `customer_part_number` — 客户料号

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无法映射到内部 SKU 时产出 WARNING，不强行匹配
- **示例**：—
- **别名**：customer item、customer item no、client sku、buyer code、customer part no、customer part no.、customer code、buyer item no、客户料号、客户型号、客户编号、客户货号

### `description` — 产品描述

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=REVIEW / Q→PI=REVIEW
- **容差**：折叠空白后比较；不等时产出 REVIEW（疑似同义），不产出 CRITICAL
- **不确定情况**：描述相近但不确定是否同一产品 -> REVIEW，绝不自动判定等价
- **示例**：—
- **别名**：description、product description、item description、commodity、product name、goods、品名、产品名称、商品名称、货物描述、描述

### `specification` — 规格

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：specification、spec、specs、size、dimension、dimensions、规格、尺寸、规格型号

### `color` — 颜色

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：color、colour、颜色、色号、色别

### `quantity` — 数量

- **归一化**：DECIMAL
- **比较方式**：`quantity_with_unit`
- **默认风险等级**：Q→PO=INFO / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：Decimal 精确相等（数量不设容差）
- **不确定情况**：单位不同 -> INCOMPARABLE，绝不跨单位换算
- **示例**：Q→PO 数量变化是买方下单行为（INFO）；PO→PI 数量不同是发货风险（CRITICAL）
- **别名**：qty、qty.、quantity、order qty、ordered quantity、q'ty、qnty、数量、订购数量、订货数量、订单数量

### `unit` — 单位

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=REVIEW / Q→PI=REVIEW
- **容差**：同义单位归一后相等（PCS=PC=PIECE）
- **不确定情况**：SETS 与 PCS、CTNS 与 PCS 不能自动换算 -> INCOMPARABLE
- **示例**：—
- **别名**：unit、units、uom、u/m、单位、计量单位

### `unit_price` — 单价

- **归一化**：MONEY
- **比较方式**：`money_quantized`
- **默认风险等级**：Q→PO=INFO / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：按币种精度量化后精确相等（跨文档先量化再分桶）
- **不确定情况**：无
- **示例**：USD 保留 2 位；JPY 保留 0 位。3333×0.1275=424.9575 量化为 424.96
- **别名**：unit price、u/p、price per pc、price/pc、price each、单价、单位价格、单件价格

### `currency` — 币种

- **归一化**：CURRENCY
- **比较方式**：`currency_code`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=CRITICAL
- **容差**：精确相等
- **不确定情况**：单独的 $ 标记歧义，不擅自认定为 USD -> REVIEW
- **示例**：—
- **别名**：currency、curr、币种、货币

### `line_total` — 行金额

- **归一化**：MONEY
- **比较方式**：`money_quantized`
- **默认风险等级**：Q→PO=INFO / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：文档内 quantity×unit_price 校验允许 1 个币种最小单位容差
- **不确定情况**：无
- **示例**：—
- **别名**：amount、total、total price、total amount、line total、extended price、sub total、subtotal、金额、总额、小计、总价、合计金额

### `packaging_quantity` — 装箱数量

- **归一化**：DECIMAL
- **比较方式**：`decimal_quantized`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：packaging quantity、packing qty、qty/ctn、pcs/ctn、pc/ctn、装箱数量、每箱数量、装箱量、每箱只数

### `carton_count` — 箱数

- **归一化**：DECIMAL
- **比较方式**：`decimal_quantized`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：ctns、ctn、cartons、carton、carton qty、no of cartons、箱数、纸箱数、总箱数

### `remarks` — 备注

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：remark、remarks、note、notes、备注、说明

---

## 文档级字段

### `document_number` — 单据号

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=INFO / PO→PI=INFO / Q→PI=INFO
- **容差**：三份单据的编号本就不同，默认 INFO，仅作展示
- **不确定情况**：无
- **示例**：—
- **别名**：quotation no、quotation no.、po no、po no.、pi no、invoice no、order no、contract no、ref no.、报价单号、订单号、合同号、发票号、单号

### `document_date` — 单据日期

- **归一化**：DATE
- **比较方式**：`date_iso`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=REVIEW / Q→PI=REVIEW
- **容差**：精确相等
- **不确定情况**：08/09/2026 类日月歧义 -> REVIEW，不擅自确定
- **示例**：—
- **别名**：date、issue date、日期、开单日期、报价日期、订单日期

### `buyer_name` — 买方

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=WARNING / Q→PI=REVIEW
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：buyer、customer、messrs、买方、客户、客户名称、购货单位

### `seller_name` — 卖方

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=WARNING / Q→PI=REVIEW
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：seller、supplier、vendor、卖方、供应商、供货单位

### `currency` — 币种

- **归一化**：CURRENCY
- **比较方式**：`currency_code`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=CRITICAL
- **容差**：精确相等
- **不确定情况**：单独的 $ 标记歧义 -> REVIEW
- **示例**：—
- **别名**：currency、币种、货币、结算币种

### `incoterm` — 贸易术语

- **归一化**：ENUM
- **比较方式**：`incoterm_triple`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：**三段全等才算相等**（term + named_place + version）
- **不确定情况**：无
- **示例**：FOB Shanghai 与 FOB Ningbo 必须判为不等
- **别名**：incoterm、incoterms、trade term、trade terms、price term、price terms、价格条款、贸易条款、成交方式、贸易术语

### `incoterm_named_place` — 贸易术语地点

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：由 incoterm 拆分得到，不单独设别名
- **不确定情况**：与 destination 语义互斥，**禁止跨字段比较**
- **示例**：—
- **别名**：（不通过别名提取）

### `incoterm_version` — 贸易术语版本

- **归一化**：TEXT
- **比较方式**：`text_exact`
- **默认风险等级**：Q→PO=INFO / PO→PI=REVIEW / Q→PI=INFO
- **容差**：由 incoterm 拆分得到；一方缺失时不参与比较
- **不确定情况**：无
- **示例**：—
- **别名**：（不通过别名提取）

### `payment_terms` — 付款条件

- **归一化**：STRUCTURED
- **比较方式**：`payment_structured`
- **默认风险等级**：Q→PO=WARNING / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：结构化成功时比 deposit/balance 比例与账期；失败则保留原文
- **不确定情况**：任一方无法结构化 -> REVIEW（待确认），**不产出 CRITICAL**
- **示例**：—
- **别名**：payment、payment term、payment terms、terms of payment、payment condition、付款方式、付款条件、支付方式、结算方式

### `delivery_terms` — 交期

- **归一化**：STRUCTURED
- **比较方式**：`delivery_terms`
- **默认风险等级**：Q→PO=REVIEW / PO→PI=WARNING / Q→PI=REVIEW
- **容差**：结构化 lead_time_days + trigger；失败则保留原文并标待确认
- **不确定情况**：**两侧表述不同类（一方相对条款、一方绝对日期）-> REVIEW，不得输出 VALUE_CONFLICT / CRITICAL**
- **示例**：PO 写 Ship by 2026-09-15、PI 写 30 days after deposit -> REVIEW
- **别名**：delivery、delivery time、delivery date、lead time、shipment date、time of shipment、delivery term、delivery terms、交货期、交期、装运期、交货时间、交货日期

### `destination` — 目的地

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：定义为最终收货地/交货地，与 incoterm_named_place 语义互斥
- **示例**：—
- **别名**：destination、目的地、收货地、交货地点、目的国

### `shipping_method` — 运输方式

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：shipping method、shipment、mode of transport、transport、运输方式、装运方式、运输

### `grand_total` — 总金额

- **归一化**：MONEY
- **比较方式**：`money_quantized`
- **默认风险等级**：Q→PO=INFO / PO→PI=CRITICAL / Q→PI=WARNING
- **容差**：与 sum(line_total) 的校验允许 1 个币种最小单位容差
- **不确定情况**：存在未解释差额时产出 CALCULATION_ERROR/**REVIEW**（可能来自运费/折扣/税费），不判 CRITICAL
- **示例**：—
- **别名**：grand total、total amount、total value、total、合计、总计、总金额、价格总计、总价

### `remarks` — 备注

- **归一化**：TEXT
- **比较方式**：`text_semantic`
- **默认风险等级**：Q→PO=INFO / PO→PI=WARNING / Q→PI=INFO
- **容差**：精确相等
- **不确定情况**：无
- **示例**：—
- **别名**：remark、remarks、note、notes、备注、说明、其他要求

---

## SKU 存在性有向表

| 出现于 | 严重度 | 业务含义 |
|---|---|---|
| 仅 Q | INFO | 报价项未被采纳，正常 |
| 仅 PO | CRITICAL | 客户下单了，我方两份单据都没有 |
| 仅 PI | CRITICAL | PI 上凭空多出一项 |
| Q + PO | CRITICAL | 已下单但 PI 漏货 |
| PO + PI | REVIEW | 未经报价的成交项 |
| Q + PI | CRITICAL | 客户没订却出现在 PI |

