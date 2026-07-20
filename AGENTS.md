# AGENTS.md — jeethink-rpa 模块约束

> 本文件供 AI 编码助手阅读,仅约束 **jeethink-rpa** 模块(Python RPA 工程)。
> 与仓库根的 `AGENTS.md`(Java/Vue/uni-app 三端)完全独立,互不干涉。
> 改动代码前先读完本文;与本文冲突的需求,以**用户当次指令**为准。

## 1. 模块定位

jeethink-rpa 是一个**独立的 Python RPA 工程**(Python 3.14 + FastAPI + nodriver),
做房产询价的浏览器自动化采集。已接入贝壳(ke)、安居客(ajk)、链家(lj)、房天下(fang)、乐有家(lyj) 共 5 个平台,按多平台可扩展设计。

- 入口服务:`app/scripts/api_server.py`
- 平台扩展指南:`docs/平台扩展对接文档.md`
- 业务说明:`README.md`

## 2. 技术栈与 API 约定

- Python 3.14,nodriver(反检测浏览器库,**非 selenium/playwright**)。
- 分层:`api → runtime → service → platform adapter → parser/algorithm`。
- 平台适配器统一继承 `app/platforms/base.py:PlatformAdapter`。
- 最终取值走 `app/core/algorithm.py`，**纯函数，所有平台共用**，两套算法可切换：
  - `decide(quote_avg, deal_avg)` — 默认「成交+在售」综合决策（4 条分支）
  - `decide_quote_only(quote_avg)` — 「纯在售」，只看在售均价打折输出
- 多城市支持:`app/platforms/city_map.py` 维护 5 平台 × 广东 21 城 URL 前缀映射,
  各 adapter `collect()` / `reset_to_start_page()` 接收 `city` 参数,
  薄壳在采集前调 `check_city_support()` + `ensure_city_navigated()` 确保城市正确。

## 3. ★ 业务流程不可擅改(最高约束)

> 这是本模块最重要的约束,优先级高于一切技术优化建议。

**业务流程是定死的。没有用户的明确指令,AI 不得擅自:**
- 增删采集步骤(如自作主张加循环检测、删掉某步)
- 改变步骤顺序
- 修改 `service.py` / `core/models.py` / `runtime.py` / `api.py`
- 改变 `decide()` / `decide_quote_only()` 的决策规则或阈值（新增算法函数不算擅改，但需用户明确指令）

**平台差异 ≠ 改流程。** 某平台因特性"略过"某步(如安居客无成交→不点详情),
是平台适配,不是流程变更。代码注释里必须写清楚"为什么略过"。

判定标准:
- 看到"被风控/被拦"就想加重试循环 → ❌ 擅改流程
- 某平台没有某数据源所以跳过该步采集 → ✅ 平台适配(需注释说明)

## 4. 对接新平台的标准流程

严格按 `docs/平台扩展对接文档.md` 执行,核心步骤:

1. **MVP 先行**:在 `app/scripts/` 下用**单个测试脚本**(如 `ajk_mvp_test.py`)逐步验证,
   不一次写完整采集。每步验证通过再往下。
2. **不每步新建脚本**:整个 MVP 验证过程在**同一个脚本**里迭代,
   不要每一步新建一个脚本文件(运维负担大)。
3. **HTML 先核对再写解析**:解析 DOM 前必须核对真实 dump 出来的 HTML,
   **不许盲写选择器/正则**。拿不到 HTML 就让人工 dump 或用 `--debug` 导出。
4. **nodriver API 用法**:
   - `Tab.evaluate(expression)` 执行的是 **JS 表达式**,箭头函数必须用 **IIFE** `(() => {...})()` 立即调用
   - `Element` **没有** `select_all`(那是 `Tab` 的),Element 用 `query_selector_all`
   - `Element.apply(js_function)` 会自动调用箭头函数并传入元素,**不需要** IIFE
   - `evaluate` 要拿返回值传 `return_by_value=True`
5. **正式落地四件套**(MVP 验证通过后):
   - `app/platforms/<code>_constants.py` — 平台固有常量(首页 URL、档位等)
   - `app/parsers/<code>.py` — HTML 解析(纯函数,从结果页/成交页提取数据,可独立单测)
   - `app/platforms/adapters/<code>.py` — 真实采集逻辑(浏览器操作,MVP 验证过的函数移植过来;解析调 `parsers`)
   - `app/platforms/<code>.py` — 薄壳适配器,委托给 adapter
6. **注册两处**:`app/platforms/__init__.py` 导出 + `app/registry.py` 追加。
7. **不改核心层**:`core/models` / `core/algorithm` / `service` / `runtime` / `api` 一行不改。
8. **算法模式可选**:`InquiryRequest.algorithm_mode` 支持 `"default"`（成交+在售）和 `"quote_only"`（纯在售），
   由 API 入参控制，默认 `"default"`。新平台采集流程与现有一致，无需因算法模式不同而改动。

## 5. 平台特性差异记录

各平台已确认的差异,AI 对接时需知晓:

| 平台 | code | 面积筛选 | 分页 | 成交记录 | 小区均价 | 详情页 |
|---|---|---|---|---|---|---|
| 贝壳 | ke | 预设档位 a1-a7 | 有,翻页 | 详情页有 | 详情页有,采 | 必须点 |
| 安居客 | ajk | 自定义输入框填值 | 无,单页全展示 | **无** | 结果页社区卡片(从业者认为有水分) | 不用点 |
| 链家 | lj | 更多选项→自定义输入 | 有,翻页 | 详情→成交列表翻页 | 不取 | 必须点 |
| 房天下 | fang | 自定义输入框填值 | 有,翻页 | 详情→小区成交 tab | 不取 | Ctrl+点击 |
| 乐有家 | lyj | 自定义输入框填值 | 有,翻页 | **无** | 结果页社区信息卡 | 不用点 |

### 安居客特殊处理(已落地,勿改)
- **无成交记录**:业务上把**挂牌均价顶替 `deal_prices`**,让 `decide()` 正常走对比逻辑。
  代码在 `ajk` adapter `_do_collect`,注释已标明。
- **无分页**:滚动到底即可(`_scroll_to_bottom`)。
- **不点详情**:挂牌均价在结果页社区卡片就有(`parse_community_avg_price`)。

### 乐有家特殊处理(已落地,勿改)
- 与安居客同理:**无成交记录**,业务上用**小区均价顶替 `deal_prices`**。
- 搜索走 URL 参数(`/esf/?c={小区名}`),不走输入框回车。

### 多城市支持(已落地)
- API 入参 `city` 为**必填**(城市, 小区, 面积三要素)。
- `app/platforms/city_map.py` 维护显式映射表(各平台 URL 前缀命名规则不统一,不能规则推导)。
- 各平台城市覆盖数:**ajk 21/21、fang 21/21、ke 12/21、lj 10/21、lyj 9/21**。
- 平台不支持城市时:跳过询价只做保活刷新,返回 `NO_DATA`;全部平台都不支持时 note="不支持该城市"。
- 城市切换:薄壳 `collect()` 中先 `ensure_city_navigated()` 检查域名,不同城才导航,避免错误城市搜索。

## 6. 编码风格

- 每个文件头部 `# -*- coding: utf-8 -*-` + 简短 docstring。
- 日志用 `logging.getLogger(__name__)`,关键步骤打 info,异常打 warning/error 带上下文。
- 函数前缀约定:模块内部用 `_` 前缀(如 `_human_click`),对外标准接口不加(如 `collect`/`probe_ready`)。
- 真人节奏:nodriver 操作间用 `asyncio.sleep` 加随机间隔,模拟真人,降低风控触发。
- 调试 HTML 导出走 `app/utils/debug_utils.py:dump_html`,默认不导出,`--debug` 或 `RPA_DEBUG=1` 开启。

## 7. 验证要求

改动后必须:
1. `python -m pytest tests/ -v` 全绿(算法/service/api/parsers 不回归)
2. 新增平台后 `python -c "from app.registry import build_default_adapters; ..."` 验证注册正常
3. MVP 脚本能跑通完整链路,人工核对采集数据合理

## 8. 文件职责速查

| 文件 | 职责 | 改动频率 |
|---|---|---|
| `app/core/algorithm.py` | 最终取值决策(纯函数，两套算法: decide/decide_quote_only) | 极低,业务规则锁定 |
| `app/service.py` | 平台调度+汇总 | 低 |
| `app/runtime.py` | 浏览器/队列/保活/状态机 | 低 |
| `app/api.py` | FastAPI 接口 | 低 |
| `app/core/models.py` | 数据模型(平台无关) | 低 |
| `app/parsers/<code>.py` | 各平台 HTML 解析(纯函数,独立单测) | 跟随各平台页面变化 |
| `app/platforms/base.py` | 平台适配器基类+通用函数(风控/点击/面积筛选/城市检查/城市导航/空页检测) | 低,通用能力沉淀 |
| `app/platforms/city_map.py` | 跨平台城市映射表(5平台×广东21城URL前缀) | 新城市/新平台接入时 |
| `app/platforms/adapters/ke.py` | 贝壳采集 | 跟随贝壳页面变化 |
| `app/platforms/adapters/ajk.py` | 安居客采集 | 跟随安居客页面变化 |
| `app/platforms/adapters/lj.py` | 链家采集 | 跟随链家页面变化 |
| `app/platforms/adapters/fang.py` | 房天下采集 | 跟随房天下页面变化 |
| `app/platforms/adapters/lyj.py` | 乐有家采集 | 跟随乐有家页面变化 |
| `app/platforms/<code>.py` | 平台薄壳适配器 | 新平台接入时 |
| `app/platforms/<code>_constants.py` | 平台固有常量 | 新平台接入时 |
| `app/scripts/<code>_mvp_test.py` | MVP 验证脚本 | 对接期间,验证完保留 |
| `docs/平台扩展对接文档.md` | 对接指南 | 新平台流程有变时 |
