# 前端 — 外贸订单差异雷达 MVP-0

规格权威来源：`../docs/SPEC.md`（§12 API 契约、§13 前端要求、§1.3 两文件场景）。
项目级红线：`../CLAUDE.md`。**API 的真实形状以 `../backend/app/api/schemas.py` 为准。**

## 命令

```
npm ci          # 或 npm install
npm run dev     # http://localhost:5173，/api 自动代理到 127.0.0.1:8000
npm run build
npm run lint
npm run typecheck
npm test
```

四条门禁命令（`lint` / `typecheck` / `test` / `build`）必须全绿。

跑起来需要后端在 127.0.0.1:8000（见 `../CLAUDE.md`）：

```
cd ../backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## npm registry

本次安装用的是 **npm 默认源 `https://registry.npmjs.org/`**（实测 269 个包约 2 分钟，可以接受）。

中国大陆网络下默认源经常慢到不可用。如果 `npm install` 超过 90 秒没有进展，换镜像：

```
npm config set registry https://registry.npmmirror.com
```

（实测同一次元数据请求：npmjs 约 9.7 秒，npmmirror 约 1.3 秒。）

## 技术栈

Vite + React 19 + TypeScript（strict）+ @tanstack/react-query + react-router-dom
+ Vitest + @testing-library/react。

**刻意不引入**：UI 组件库、CSS 框架、状态管理库、i18n 框架、图表库、msw。
样式是一个手写的 `src/styles.css`。

## 目录

```
src/
  api/client.ts        唯一的 HTTP 出口。API base = 相对路径 /api
  api/queries.ts       react-query 封装，组件不直接碰 fetch
  api/types.ts         线上类型，逐字段对应 backend/app/api/schemas.py
  labels/enums.ts      「英文枚举 -> 中文」映射表
  labels/identifiers.ts 角色名单 / line_key / group_key / 主体列的本地化
  labels/explanations.ts explanation_key + params -> 中文句子
  components/          横幅、上传槽位、芯片、差异表、证据、审核
  pages/               ProjectsPage（/projects）、WorkbenchPage（/projects/:id）
tests/                 Vitest + Testing Library，手写 fetch mock，不依赖真实后端
```

## 路由

| 路径 | 页面 |
|---|---|
| `/projects` | 项目列表：创建、状态、各严重度计数、待处理数、删除（二次确认） |
| `/projects/:projectId` | 差异工作台：上传 / 运行检查 / 覆盖横幅 / 总览芯片 / 差异表 / 审核 / 导出 |
| `/projects/:projectId/differences` | 同上（SPEC §13.2 写的是这个路径，保留为别名） |

## 落在代码里的硬约束

改动前请先读完这一节，每一条都有对应的测试或 lint 规则守着。

1. **界面中文单语，禁止 i18n 框架。** API / DB / golden 只用英文枚举标识符，
   前端维护 `src/labels/` 一份映射表。所有查表函数遇到未登记的标识符
   **回退成原始标识符**而不是抛异常——露出一个英文词很难看，静默丢一条差异要命得多。

2. **API base 是相对路径 `/api`**，dev 阶段由 `vite.config.ts` 的 proxy 转发到
   `http://127.0.0.1:8000`。整个 `src/` 里不出现任何后端 origin，生产同源部署时
   一行不用改。守卫有两层：eslint 的 `no-restricted-syntax` 规则 +
   `tests/apiBase.test.ts` 对 `src/` 全文的扫描。
   `vite.config.ts` 是唯一豁免的文件。

3. **覆盖横幅**（`components/CoverageBanner.tsx`）：`compared_roles` 少于 3 个时，
   差异表上方强制显示「缺席角色 = 未检查，不等于无差异」并列出缺席角色。
   不可折叠、不可关闭。这是集合驱动比较（`|R| >= 2` 即可跑）引入的新失效模式：
   只传了 PO 和 PI 的人很容易把「零差异」读成「报价单也没问题」。

4. **免责声明常驻可见**（`components/Disclaimer.tsx`）：放在布局 header 里，
   不做成一次性提示条。

5. **运行检查的门槛是 2 份，不是 3 份**（`MIN_DOCUMENTS_TO_COMPARE`）。
   报价单标「可选」。只有 `parse_status ∈ {OK, NEEDS_REVIEW}` 的文档计入份数，
   口径与后端 `_project_out` 的 `usable` 一致。

6. **explanation 在前端渲染**：后端只给 `explanation_key` + `explanation_params`。
   三条兜底缺一不可：
   - 未知 key -> 「（未登记的说明模板 X）参数…」，差异照常出现在表里；
   - 缺参数 -> 占位「（未提供）」；
   - **未登记的参数名 -> 一个字符都不动**。

   最后一条是 `labels/identifiers.ts` 存在的全部理由。参数里混着三类东西：引擎拼的
   角色标识符、引擎拼的中文句子、**单据原文**。无脑 `replace('QUOTATION', '报价单')`
   会把备注 `AS PER QUOTATION Q2026-001`（外贸单据里极常见）改成
   `AS PER 报价单 Q2026-001`，而界面恰恰宣称自己在引用原文。
   因此按参数名分派策略，与 `backend/app/exports/html.py` 的 `PARAM_LOCALIZERS` 一一对应。

7. **值必须标出来源**（`components/ValueList.tsx`）：`source = USER_CORRECTION` 的值
   旁边标「人工修正」，并**同时显示 `parser_value`（机器原读数）与修正理由**。
   报告要发给老板和客户，「这个数是机器读的还是人填的」必须精确到字段。

8. **删除项目的措辞**只能是「将删除数据库记录与磁盘上的原始文件」，
   **不得**出现「安全擦除」「不可恢复」（`DELETE_PROJECT_WARNING`，有测试断言）。

9. **严重度不靠颜色单独承载**：形状记号（▲◆■●）+ 中文文字 + 颜色三重编码，
   调色板用「红 - 橙 - 蓝 - 灰」而不是红绿对立。整页灰度打印依然分得清轻重。
   不做任何动画。差异表 `table-layout: fixed`，列宽显式给定，1366×768 无横向滚动。

## 设计取舍

- **差异筛选放在前端做**，一次拉全量。后端同样支持 `severity/type/sku/review_status/role`
  查询参数，但芯片筛选要「点一下立刻变」，而且总览计数必须始终是全量的——
  服务端筛选会让计数随筛选漂移，那正是业务员最容易误判「没差异了」的地方。
- **审核状态用「草稿 + 保存」而不是 onChange 直接提交**：审核是有后果的动作，
  误触下拉框不该立刻写库。未改动时保存按钮禁用。
- **证据默认收起且按钮切换真实挂载**，不用 `<details>`。用 `<details>` 的话内容始终
  在 DOM 里，「展开后才可见」这条测试会假通过。
- **主体列显示中文可读串**（`AB-200`、`形式发票·AB-200`），原始身份串留在 `title` 里。
  与后端报告的 `_subject_label` 同一套规则，界面和报告必须说同一句话。

## 测试

```
npm test
```

52 个测试，6 个文件。fetch 用手写 mock（`tests/testUtils.tsx`），未登记的请求直接抛错，
这样「悄悄多打了一个接口」在测试里立刻可见。夹具形状抄自
`backend/tests/test_api_integration.py` 的真实响应。

覆盖的关键行为：上传成功 / 解析失败带错误码 / 上传被拒绝、运行检查按钮的 2 份门槛、
CRITICAL 芯片筛选、SKU 与角色筛选、证据展开后可见单元格地址与原文、审核 PUT 的 URL
与 body、报告导出入口指向 `/api/v1/projects/{id}/report.html`、两文件场景的覆盖横幅、
未知 explanation key 的兜底、单据原文不被「翻译」、删除项目的措辞。
