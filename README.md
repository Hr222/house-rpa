## 目录索引

- [1. 项目定位](#1-项目定位)
- [2. 已接入平台及差异](#2-已接入平台及差异)
- [3. 业务链路](#3-业务链路)
- [4. 业务取值规则](#4-业务取值规则)
- [5. 架构分层](#5-架构分层)
- [系统架构与运行时状态](docs/系统架构与运行时状态.md)
- [6. 目录说明](#6-目录说明)
- [7. 核心模块说明](#7-核心模块说明)
- [8. 配置与常量边界](#8-配置与常量边界)
- [9. 启动流程](#9-启动流程)
- [10. 运行方式](#10-运行方式)
- [11. API 约定](#11-api-约定)
- [12. 崩溃恢复与弱持久化](#12-崩溃恢复与弱持久化)
- [13. 日志与调试](#13-日志与调试)
- [14. 当前约束](#14-当前约束)

## 1. 项目定位

当前版本重点解决以下问题：

- 浏览器常驻，不为每次询价重新冷启动。
- 由人工先完成登录，确认就绪后才允许接单。
- 后台接收询价请求，并串行执行采集流程。
- 支持**多城市询价**：API 入参含 `city`（城市名），各平台按城市映射表导航到对应城市首页后再搜索。
- 采集完成后主动 POST 回调通知客户端（客户端无需轮询）；GET 查询保留作兜底并受限流。
- 平台被风控或登录失效时，服务状态可明确降级。
- 调试模式下可导出关键 HTML，方便定位页面结构变化。
- 日志按自然日切分，适合 7x24 值守机运行。
- 任务入队时持久化，进程崩溃后重启自动恢复未完成任务。
- 算法参数（无成交折扣）支持运行时动态更新，弱持久化重启不丢失。

## 2. 已接入平台及差异

| 平台 | code | 面积筛选 | 分页 | 成交记录 | 小区均价 | 详情页 | 广东城市覆盖 |
|------|------|---------|------|---------|---------|--------|-------------|
| 贝壳 | ke | 动态读取档位+点击链接 | 有，翻页 | 详情页有 | 详情页有 | 必须点 | 12/21 |
| 安居客 | ajk | 动态读取档位+点击链接 | 无，滚动到底 | **无**（挂牌均价顶替） | 结果页社区卡片 | 不点 | 21/21 |
| 链家 | lj | 动态读取档位+点击链接 | 有，翻页 | 详情→成交列表翻页 | 不取 | 必须点 | 10/21 |
| 房天下 | fang | 动态读取档位+点击链接 | 有，翻页 | 详情→小区成交 tab | 不取 | Ctrl+点击 | 21/21 |
| 乐有家 | lyj | 动态读取档位+点击链接 | 有，翻页 | **无**（小区均价顶替） | 结果页社区信息卡 | 不点 | 9/21 |
| 行舟深房 | xzsfbj | 接口按面积请求，返回后 ±1㎡复核 | 有，接口翻页 | 接口记录，严格面积 ±1㎡ + 近半年 | 在售接口 | 不适用 | 1/21 |

5 平台全部覆盖的城市（9 个）：广州、深圳、珠海、佛山、东莞、中山、惠州、江门、清远。

### 平台差异说明

- **面积筛选**：所有平台已统一为"动态读取页面 HTML 档位 + 点击对应链接"的方式，
  由 `base.py` 的 `click_area_segment` 提供通用逻辑，各平台只需实现 `parsers.parse_area_segments`。
- **小区数据过滤**：所有平台搜索后先校验目标小区；分页平台逐页过滤，只累计目标小区房源。第 1 页非空但全部无关时返回 `NO_DATA`，第 2 页起非空但全部无关时立即停止后续翻页并保留此前有效数据；返回前再次校验，确保房源明细与在售价格来自同一批数据。
- **安居客**：无成交记录，业务上用挂牌均价顶替 `deal_prices`；无分页，滚动到底即可；不点详情页。
- **乐有家**：同安居客，无成交记录，小区均价顶替 `deal_prices`；搜索走 URL 参数。
- **链家**：贝壳子公司，DOM 高度相似；成交筛选用严格区间 + 近半年（与贝壳 ±20% 容差不同）。
- **房天下**：成交筛选用严格区间 + 近半年；详情入口只在第一页，Ctrl+点击后台打开。
- **行舟深房**：接口成交记录按实际面积 `±1㎡` 且近半年筛选；日期缺失或无法识别的记录不参与成交统计。多期住宅在平台适配器内合并后再进入统一算法。

## 3. 业务链路

通用主流程（各平台按差异微调）：

1. 启动浏览器并打开各平台二手房首页。
2. 人工在前台完成各平台登录。
3. 通过 API `POST /admin/platforms/{code}/confirm-ready` 或终端回车确认就绪。
4. 接收询价请求：`city`（城市名）、`administrativeDistrict`（行政区）、`communityName`（小区名）、`area`（精确面积）。
5. 检查各平台是否支持该城市：不支持的平台跳过询价只做保活刷新；支持的继续。
6. 城市导航：如果当前浏览器不在目标城市域名下，先导航到目标城市首页。
7. 刷新常驻页面，执行轻量保活。
8. 搜索目标小区，并校验搜索结果里至少存在一条目标小区快照。
9. 结果页按面积筛选。
10. 抓取主结果区，过滤推荐/广告区块，并立即按目标小区过滤房源快照。
11. 如有分页，按真实点击页码采集：第 1 页非空但全部无关时返回 `NO_DATA`；第 2 页起非空但全部无关时立即停止，混合页只保留匹配房源并继续；空页走独立空页检测。
12. 返回平台结果前再次过滤，并从同一批快照生成 `listing_snapshots` 与 `quote_prices`。
13. 如需详情页，打开小区详情。
14. 抓取小区均价和成交案例（平台有则采，无则跳过或顶替）。
15. 对成交案例按面积筛选后计算成交均价。
16. 按业务规则计算最终单价。
17. 返回结果，页面回到待命状态。

> 如果所有平台都不支持该城市，直接返回 `NO_DATA`，note 为"不支持该城市"。

核心返回字段：

```json
{
  "quoteAvg": 85635.00,
  "dealAvg": 71086.50,
  "finalPrice": 71086.50
}
```

## 4. 业务取值规则

### 在售均价

从抓到的在售单价列表中取平均值。

### 成交均价

各平台成交筛选规则不同：

- **贝壳**：对成交案例按请求面积上下浮动 `20%` 筛选后取均价。
- **链家 / 房天下**：严格面积区间 + 近半年（6 个月）筛选后取均价。
- **行舟深房**：实际面积 `±1㎡` + 近半年（6 个月）筛选后取均价；接口返回的历史记录不参与统计。
- **安居客 / 乐有家**：无成交记录，用平台挂牌均价顶替 `deal_prices`。

### 最终取值：挂牌价与成交价汇总

代码位置：`app/core/algorithm.py:WeightedMedianAlgorithm`。系统固定使用这一套算法，不再通过请求参数切换算法。

代码位置：`app/core/algorithm.py:WeightedMedianAlgorithm`

- 每个平台总权重相等，平台内每条有效在售价格按数量分配权重。
- 以相对中位数 ±10% 识别各自密集的价格峰；只有无法与其他报价组成价格簇的单点才作为孤立噪声排除。
- 多个已成簇的价格带都保留为候选，即使它们与最高频峰的出现次数差距较大。
- 每个价格峰以成员中位数作为候选 `quote_avg`。
- 同一平台内先按稳定房源编号或完整字段去重；无编号且有一方缺户型时，小区全称/简称、面积、单价、总价均精确一致也合并，并保留标题更完整的一条。跨平台不比较标题：小区全称/无歧义简称、面积、单价、总价必须精确一致；双方均有户型时，户型也必须一致，任一方缺户型时不以户型阻止合并。生产算法与日志分析使用同一套规则。
- 多峰时选择最低价格峰的中位数直接返回，不乘在售折扣；单峰仍按原规则打折。
- 最终单价 = `quote_avg × weightedMedianDiscount`，branch 为 `WEIGHTED_MEDIAN`。
- 存在真实目标面积成交价时：单条成交价直接使用，多条成交价按同样的落点规则取值；挂牌结果与成交结果最后做等权平均，branch 为 `WEIGHTED_MEDIAN_COMBINED`。
- 安居客、乐有家的 `deal_prices` 是无成交时的兼容性均价顶替，不作为真实成交价参与平均；没有真实目标面积成交价时沿用原挂牌结果。
- 50/50 双峰且无法形成明确主要区间时，不人为计算两个区间之间的中间价。

## 5. 架构分层

整体分为 5 层：

1. **API 层** — `app/api.py`
   FastAPI 入口，接收 HTTP 请求，对外暴露健康检查、状态查询、询价接口、参数管理。

2. **Runtime 层** — `app/runtime.py`
   管理浏览器实例、平台会话、任务队列、服务状态、保活流程、崩溃恢复。

3. **Service 层** — `app/service.py`
   调度多个平台适配器，汇总平台结果并计算最终报价。

4. **Platform Adapter 层** — `app/platforms/`
   每个平台两件套：薄壳适配器（`platforms/<code>.py`）+ 采集逻辑（`platforms/adapters/<code>.py`）。

5. **Parser / Algorithm 层** — `app/parsers/` + `app/core/algorithm.py`
   页面解析和纯算法决策，不承担浏览器控制。

```
外部请求方 → FastAPI (api.py)
  → RPARuntime (runtime.py)
    → RPAInquiryService (service.py)
      → PlatformAdapter (platforms/ke.py 等)
        → Adapter (platforms/adapters/ke.py 等)
          → Parser (parsers/ke.py) + Algorithm (core/algorithm.py)
```

## 6. 目录说明

```text
jeethink-rpa/
├─ app/
│  ├─ core/
│  │  ├─ config.py          # 运行配置 + 弱持久化参数管理
│  │  ├─ models.py          # 数据模型（平台无关）
│  │  ├─ algorithm.py       # 最终取值决策（纯函数）
│  │  └─ price_utils.py     # 价格格式化工具
│  ├─ platforms/
│  │  ├─ base.py            # 平台适配器抽象基类
│  │  ├─ city_map.py        # 跨平台城市映射表（网页平台 + 行舟深房深圳支持）
│  │  ├─ __init__.py        # 平台导出集合
│  │  ├─ ke.py / ke_constants.py       # 贝壳：薄壳适配器 + 常量
│  │  ├─ ajk.py / ajk_constants.py     # 安居客：薄壳适配器 + 常量
│  │  ├─ lj.py / lj_constants.py       # 链家：薄壳适配器 + 常量
│  │  ├─ fang.py / fang_constants.py   # 房天下：薄壳适配器 + 常量
│  │  ├─ lyj.py / lyj_constants.py     # 乐有家：薄壳适配器 + 常量
│  │  ├─ xzsfbj.py / xzsfbj_constants.py # 行舟深房：WMPF 接口薄壳 + 常量
│  │  └─ adapters/
│  │     ├─ ke.py           # 贝壳真实采集逻辑
│  │     ├─ ajk.py          # 安居客真实采集逻辑
│  │     ├─ lj.py           # 链家真实采集逻辑
│  │     ├─ fang.py         # 房天下真实采集逻辑
│  │     ├─ lyj.py          # 乐有家真实采集逻辑
│  │     └─ xzsfbj.py       # 行舟深房 WMPF/HTTP 采集逻辑
│  ├─ parsers/
│  │  ├─ ke.py              # 贝壳 HTML 解析器
│  │  ├─ ajk.py             # 安居客 HTML 解析器
│  │  ├─ lj.py              # 链家 HTML 解析器
│  │  ├─ fang.py            # 房天下 HTML 解析器
│  │  ├─ lyj.py             # 乐有家 HTML 解析器
│  │  └─ xzsfbj.py          # 行舟深房 JSON 响应解析器
│  ├─ utils/
│  │  ├─ logging_utils.py   # 日志配置（按日切分）
│  │  ├─ debug_utils.py     # 调试 HTML 导出
│  │  ├─ task_store.py      # 任务持久化（崩溃恢复兜底）
│  │  ├─ callback.py        # 结果回调推送（主动通知客户端）
│  │  └─ window_control.py  # Windows 浏览器置前控制
│  ├─ scripts/
│  │  ├─ api_server.py      # 服务启动入口
│  │  ├─ ke_mvp_test.py     # 贝壳 MVP 测试
│  │  ├─ ajk_mvp_test.py    # 安居客 MVP 测试
│  │  ├─ lj_mvp_test.py     # 链家 MVP 测试
│  │  ├─ fang_mvp_test.py   # 房天下 MVP 测试
│  │  ├─ lyj_mvp_test.py    # 乐有家 MVP 测试
│  │  └─ xzsfbj_mvp_test.py # 行舟深房 MVP 测试（接口平台回归工具）
│  ├─ api.py                # FastAPI 路由定义
│  ├─ runtime.py            # 服务运行时
│  ├─ service.py            # 服务编排
│  └─ registry.py           # 平台注册
├─ tests/                   # 单元测试
├─ docs/                    # 对接文档
├─ third_party/
│  └─ zhong_wmpf_bridge/    # 行舟深房 WMPF 调试桥源码与配置（Node 依赖本机安装）
├─ requirements.txt
└─ README.md
```

`third_party/zhong_wmpf_bridge/` 是行舟深房接口采集所需的第三方桥接源码，包含桥接入口、
Frida 配置、`package.json`、`package-lock.json`、上游许可证和来源说明。运行
`scripts\setup_xzsfbj_wmpf_bridge.ps1` 后生成的 `node_modules/` 属于本机依赖，已被 Git
忽略，不应提交；迁移到新机器时按“行舟深房环境准备”重新安装即可。默认路径可通过
`XZSFBJ_WMPF_BRIDGE_DIR` 覆盖。

## 7. 核心模块说明

### `app/core/config.py`

运行配置中心，包含：

- 调试开关（`DEBUG_MODE`）
- 浏览器路径、API 监听地址
- 风控参数（保活间隔、详情页停留时间等）
- 算法参数：`get_weighted_median_discount()` / `set_weighted_median_discount()` — 加权落点中位数折扣，支持弱持久化

### `app/core/models.py`

平台无关的数据模型：

- `InquiryRequest` — 询价请求
- `PlatformResult` — 单平台采集结果（含在售列表、成交列表、房源快照）
- `InquiryResult` — 最终询价结果（含决策分支、最终价格）
- `ListingSnapshot` / `DealRecord` — 房源摘要 / 成交记录

### `app/core/algorithm.py`

纯函数，无 IO，所有平台共用。算法策略接口和注册表继续保留，当前只注册加权落点中位数算法：
- `aggregate_weighted_median_quote(...)` — 按平台等权寻找主要在售价格落点并计算加权中位数
- `evaluate_algorithm(AlgorithmInput(...))` — 固定使用加权落点中位数并返回最终价格和结果分支

### `app/api.py`

FastAPI 入口。接口清单：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/live` | 存活检查 |
| GET | `/health/ready` | 就绪检查 |
| GET | `/admin/status` | 服务状态 |
| POST | `/admin/platforms/{code}/confirm-ready` | 确认平台就绪 |
| POST | `/inquiries` | 创建询价任务 |
| GET | `/inquiries/{taskId}` | 查询任务结果 |
| GET | `/admin/algorithm/weighted-median-discount` | 查询加权落点中位数折扣 |
| PUT | `/admin/algorithm/weighted-median-discount` | 更新加权落点中位数折扣 |

### `app/runtime.py`

服务运行时核心。职责：

- 启动常驻浏览器。
- 打开各平台常驻页面。
- 维护平台状态（`PlatformRuntimeState`）。
- 平台健康状态与单次询价结果分离管理：验证码/风控进入 `WAIT_MANUAL_VERIFY`，登录失效进入 `WAIT_LOGIN`，普通任务 `ERROR` 不覆盖平台健康状态。
- 任务结果回写带平台状态版本保护，旧任务不能覆盖人工确认或保活产生的新状态。
- 人工回车确认批次与平台保活互斥，避免保活在批量确认过程中抢先改写状态，导致部分平台被跳过。
- 管理任务队列（`asyncio.Queue`，串行消费）。
- 定时保活循环（默认 120s）。
- 崩溃恢复：全部平台首次就绪后，从 `persist/` 恢复未完成任务（只一次）。
- 需要人工处理时尝试将浏览器置前。

### `app/service.py`

平台调度与结果汇总层。

- `build_inquiry_result()` — 汇总所有 `SUCCESS` 平台的在售数据，固定调用加权落点中位数算法计算最终价。
- `RPAInquiryService` — 管理各平台 session，执行 `run_inquiry()`。

### `app/platforms/base.py`

平台适配器抽象基类 `PlatformAdapter`。每个平台必须实现：

- `open_session(browser)` → `PlatformSession`
- `collect(browser, session, request)` → `PlatformResult`
- `check_ready(session)` → `(bool, str)`
- `detect_block(url, html)` → `(bool, str)`
- `keepalive(session)` → `(bool, str)`（有默认实现）

基类已实现的城市相关方法（薄壳 `collect()` 中统一调用）：

- `check_city_support(city, request_id)` → 不支持时返回 `NO_DATA` 结果（含支持城市列表），支持时返回 `None`。
- `ensure_city_navigated(session, city)` → 检查当前页面域名是否匹配目标城市，不匹配则导航到目标城市首页，匹配则跳过。

基类同时提供各平台共用的模块级函数，adapter 直接 import 复用：

| 函数 | 用途 |
|------|------|
| `human_linger(page, page_no)` | 翻页后模拟真人停留 |
| `wait_for_manual_unblock()` | 风控/登录拦截时等待人工处理 |
| `detect_common_block(url, html)` | 统一检测公共 URL/HTML 风控标识 |
| `detect_block_with_common(detect_func, url, html)` | 平台专属风控规则优先，未命中时使用 base.py 公共兜底 |
| `wait_and_reload_after_block(tab, detect_func, label)` | 详情/成交页风控统一处理：平台规则 + 公共规则检测→等人回车→重取，最多 2 次 |
| `_human_click(page, element, label)` | 真人节奏点击（JS 优先，随机间隔） |
| `safe_select_and_click(page, selector, ...)` | 安全选择+点击：找不到元素时 dump + 风控检测 + 恢复后重试 |
| `check_empty_listing_page(page_no, count, consecutive_empty, total_pages, platform)` | 翻页空页检测：首页空→error+停止，连续2页空→warning+停止（4 个翻页平台共用） |
| `click_area_segment(page, area, parse_func, code)` | 动态读取面积档位并点击匹配项 |
| `is_generic_captcha_page(html)` | 通用验证码页兜底检测（跨平台共性） |
| `short_circuit_result(name, status, reason, ...)` | 统一构造短路返回（NO_DATA 等），消除各平台重复模板 |
| `community_name_match(request_name, captured_name)` | 比较请求小区名与抓取快照的结构化小区名；不使用整页 HTML、标题或搜索词 |
| `has_matching_community_snapshots(snapshots, community_name)` | 判断搜索结果里是否至少命中一条目标小区快照，用于首轮校验 |
| `filter_snapshots_by_community(snapshots, community_name)` | 只按 `ListingSnapshot.community_name` 过滤抓取数据，供逐页过滤和返回前防御校验共用 |
| `prepare_listing_data(snapshots, community_name)` | 过滤目标小区快照，并从同一批快照生成 `quote_prices`，保证明细与价格同源 |

### `app/platforms/<code>.py` + `adapters/<code>.py`

平台适配器两件套：

- 薄壳适配器（`platforms/<code>.py`）：实现 `PlatformAdapter` 接口，委托给 adapter。
  `collect()` 流程：`check_city_support` → `ensure_city_navigated` → `adapter.collect(city=...)` → 复位回首页。
- 采集逻辑（`platforms/adapters/<code>.py`）：搜索、筛选、分页、解析、风控检测等真实逻辑。
  `collect()` 和 `reset_to_start_page()` 均接收 `city` 参数。

### `app/platforms/city_map.py`

跨平台城市映射表，维护网页平台的城市 URL 前缀，并维护行舟深房仅支持深圳的能力边界。

- `CITY_MAP[platform_code][city_name] = url_prefix` — 显式映射（不能用规则推导，各平台命名规则不统一）
- `get_start_url(platform_code, city)` → 完整起始 URL，不支持时 raise `ValueError`
- `get_city_prefix(platform_code, city)` → URL 前缀，不支持时返回 `None`
- `is_city_supported(platform_code, city)` → 是否支持

### `app/parsers/<code>.py`

每个平台一个独立 HTML 解析器，与 adapter 的浏览器操作分离。adapter 通过 `from app.parsers import <code> as parsers` 调用。

- `parsers/ke.py` — 贝壳（BeautifulSoup + 正则兜底）：在售记录/摘要、详情链接、小区均价、成交记录、面积档位解析、面积 ±20% 筛选
- `parsers/ajk.py` — 安居客：在售快照、挂牌均价（顶替成交）、面积档位解析
- `parsers/lj.py` — 链家：在售快照、成交记录、面积档位解析、严格面积区间+近半年筛选
- `parsers/fang.py` — 房天下：在售快照、成交表格、面积档位解析、严格面积区间+近半年筛选
- `parsers/lyj.py` — 乐有家：在售快照、小区均价（顶替成交）、面积档位解析
- `parsers/xzsfbj.py` — 行舟深房：小区索引、接口在售/成交响应、住宅期数匹配、面积 ±1㎡ + 近半年筛选

### `app/utils/`

| 文件 | 职责 |
|------|------|
| `logging_utils.py` | 日志：控制台 + 文件，按自然日切换 |
| `debug_utils.py` | 调试 HTML 导出，`--debug` 或 `RPA_DEBUG=1` 开启 |
| `task_store.py` | 任务持久化：入队写 JSON，完成删，崩溃恢复 |
| `callback.py` | 结果回调推送：任务结束主动 POST 给客户端（带重试） |
| `window_control.py` | Windows 浏览器置前（Win32 API） |

## 8. 配置与常量边界

### 运行配置（`app/core/config.py`）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DEBUG_MODE` | `False` | 调试开关（`RPA_DEBUG=1`） |
| `BROWSER_PATH` | Chrome 默认路径 | 浏览器可执行文件 |
| `API_HOST` / `API_PORT` | `127.0.0.1:8000` | API 监听 |
| `DETAIL_TAB_LINGER_SECONDS` | `15` | 详情页停留时间 |
| `REQUEST_TIMEOUT` | `30` | 请求超时（秒） |
| `PLATFORM_KEEPALIVE_INTERVAL` | `120` | 保活间隔（秒） |
| `HEARTBEAT_INTERVAL` | `20` | WebSocket 心跳间隔（秒） |
| `PAGE_LINGER_SECONDS` | `3.5` | 结果页滚动停留 |
| `CALLBACK_URL` | `None` | 结果回调基址（`RPA_CALLBACK_URL`）。配置后任务结束主动 POST 推送，为空则不推送，客户端走 GET 兜底 |
| `GET_INQUIRY_MIN_INTERVAL` | `10` | GET 查询限流：同一 taskId 两次查询最小间隔秒数（`RPA_GET_MIN_INTERVAL`） |
| `get_weighted_median_discount()` | `0.9` | 加权落点中位数折扣（可运行时更新，弱持久化） |

### 平台常量

各平台独立常量文件（`platforms/<code>_constants.py`）：

- `START_URL` — 平台默认城市首页 URL（仅用于 `open_session` 初始打开和保活刷新；多城市采集时由 `city_map.get_start_url()` 动态获取目标城市 URL）
- `AREA_SEGMENTS` — 面积档位映射（仅贝壳保留，已不再用于实际采集；所有平台已统一改为动态读取页面 HTML 档位）

## 9. 启动流程

1. 启动浏览器。
2. 打开各平台常驻页面。
3. 浏览器置前，等待人工登录。
4. 人工完成登录后，通过 API 或终端回车确认平台就绪。
5. 全部平台就绪后，服务状态切换为 `READY`。
6. 恢复崩溃前残留的未完成任务。
7. 开始接收 `/inquiries` 请求。

未就绪时收到询价请求，返回 `503 SERVICE_NOT_READY`。

平台状态边界：

- `PlatformHealthStatus` 表示平台当前是否可继续采集。
- `PlatformResultStatus` 表示单次询价结果，`ERROR` 只记录本次任务异常。
- 验证码、人机验证和公共风控标识统一使用 `WAIT_MANUAL_VERIFY`。
- 登录页或登录态失效统一使用 `LOGIN_EXPIRED`，运行时映射为 `WAIT_LOGIN`。
- 人工确认、保活和任务回写通过运行时状态版本协调，旧任务结果不能覆盖新状态。
- 人工确认批次期间暂停并发平台保活；就绪检查和保活共用互斥控制，保证一次回车完整处理当前待确认平台。

## 10. 运行方式

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python -m app.scripts.api_server
```

常用参数（所有脚本统一）：

- `--debug` — 开启调试模式，导出关键页面 HTML 到 `excel/` 目录（兼容旧参数 `--excel`）
- `--manual-login` — 启用终端回车确认登录：平台未就绪时提示回车，人工完成登录后继续

```bash
# 调试 + 人工登录确认
python -m app.scripts.api_server --debug --manual-login
```

### 单平台 MVP 测试

```bash
python -m app.scripts.ke_mvp_test --debug --manual-login       # 贝壳
python -m app.scripts.ajk_mvp_test --debug --manual-login      # 安居客
python -m app.scripts.lj_mvp_test --debug --manual-login       # 链家
python -m app.scripts.fang_mvp_test --debug --manual-login     # 房天下
python -m app.scripts.lyj_mvp_test --debug --manual-login      # 乐有家
```

### 行舟深房（xzsfbj）环境准备

> 行舟深房已接入默认平台列表，首期仅支持深圳；正式服务仍需按下方步骤准备本机微信小程序数据。

行舟深房通过 Windows 微信小程序的 WMPF 调试桥获取内存 token，再调用其结构化接口。
小区匹配只保留住宅候选，明确标注为商铺、写字楼、办公、酒店、宿舍或厂房的索引条目会被排除；
“大厦”“中心”等字样本身不足以判定为非住宅。
新机器首次使用前，按以下顺序准备：

1. 复制 `.env.example` 为 `.env`，填写 `XZSFBJ_AES_KEY`（行舟深房
   `regionId` 的 AES-ECB 密钥，必须是 16/24/32 字节）。真实密钥只保存在本机 `.env`，
   不要提交到 Git。

2. 安装 64 位 Node.js，并安装 Python 依赖：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. 安装项目内置 WMPF 桥的 Node 依赖。该步骤会下载 Frida 原生绑定，必须成功生成
   `third_party\zhong_wmpf_bridge\node_modules\frida\build\frida_binding.node`：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_xzsfbj_wmpf_bridge.ps1
   ```

   若该文件不存在，勿运行 MVP；检查网络是否能访问 Frida 的预编译绑定下载地址后重试。
   `node_modules` 是本机依赖，不提交到 Git。

4. 登录 Windows 微信，首次打开一次「行舟深房」并等待页面加载完成。小程序会自动创建
   本地小区索引 `xqData.json`；无需从其它机器复制或提交该文件。
   默认会从当前用户的微信目录自动查找；若微信使用了非标准数据目录，可设置
   `XZSFBJ_XQ_DATA_PATH` 指向本机生成的 `xqData.json`，桥目录也可用
   `XZSFBJ_WMPF_BRIDGE_DIR` 覆盖。

5. 启动服务或 MVP。首次实际采集前，桥接就绪后从微信会话重新打开小程序并进入任意
   小区的成交记录；程序会在内存中捕获 token，日志仅显示脱敏片段、长度和指纹，随后
   自动关闭 WMPF 桥，微信小程序保持打开。token 每 30 个小区或 1 小时刷新一次。

   正式服务使用 `--manual-login` 启动时，行舟深房会在终端明确提示“无需网页登录”，
   只需确认本地依赖后按回车；真正采集该平台时才会提示打开小程序并自动捕获 token。

   ```powershell
   .\.venv\Scripts\python.exe app\scripts\xzsfbj_mvp_test.py `
     --community "月亮湾花园" --area 91.5 --debug
   ```

`--debug` 仅导出脱敏接口 JSON 到 `debug/`，该目录已被 Git 忽略。正式服务默认不导出
调试响应；MVP 和正式 adapter 都不使用系统代理、证书注入或落盘 token。接口触发风控时，
程序沿用统一人工回车确认流程，确认后重新捕获 token 并继续当前小区。

### 接单测试

服务就绪后，用根目录 `test_inquiry.py` 发一次真实询价，观察浏览器采集并轮询结果：

```bash
python test_inquiry.py
```

## 11. API 约定

### 创建询价任务

`POST /inquiries`

请求体：

```json
{
  "city": "深圳",
  "administrativeDistrict": "南山区",
  "communityName": "绿景虹湾",
  "area": 89.5,
  "requestId": "demo-001"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `city` | string | 是 | 城市名（如 深圳、广州、东莞） |
| `administrativeDistrict` | string | 是 | 行政区（如 南山区）；用于行舟深房同名小区消歧 |
| `communityName` | string | 是 | 小区名称 |
| `area` | number | 是 | 精确面积（㎡） |
| `requestId` | string | 否 | 自定义任务 ID，不传则自动生成 |

返回：

```json
{
  "code": "ACCEPTED",
  "message": "询价任务已受理",
  "data": {
    "taskId": "demo-001",
    "status": "排队中",
    "statusCode": "QUEUED"
  }
}
```

### 结果回调（主机制）

配置 `RPA_CALLBACK_URL` 后，服务在每次询价任务结束（成功或失败）时，主动 `POST` 推送结果到 `{RPA_CALLBACK_URL}/{taskId}`，客户端**无需轮询**。未配置时则不推送。

请求 body 示例（成功）：

```json
{
  "taskId": "demo-001",
  "statusCode": "COMPLETED",
  "status": "已完成",
  "success": true,
  "quoteAvg": 85635.00,
  "dealAvg": 71086.50,
  "finalPrice": 71086.50,
  "branchCode": "WEIGHTED_MEDIAN",
  "branch": "主要价格落点中位数折扣"
}
```

请求 body 示例（失败）：

```json
{
  "taskId": "demo-001",
  "statusCode": "FAILED",
  "status": "失败",
  "success": false,
  "error": "采集异常原因"
}
```

推送可靠性：HTTP 非 2xx 或网络异常会重试（默认 3 次，递增延迟），全部失败仅记日志，不影响任务结果落库。

### 查询询价结果（兜底，受限流约束）

`GET /inquiries/{taskId}`

作为回调的兜底手段，客户端可偶尔查一次。为防高强度轮询，同一 `taskId` 两次查询最小间隔 `RPA_GET_MIN_INTERVAL`（默认 10 秒，见 §8），间隔内重复查询返回 `429`：

```json
{
  "code": "TOO_MANY_REQUESTS",
  "message": "查询过于频繁，请在 10 秒后重试",
  "data": { "taskId": "demo-001", "retryAfter": 10 }
}
```

完成后返回的 `data` 核心结构：

```json
{
  "quoteAvg": 85635.00,
  "dealAvg": 71086.50,
  "finalPrice": 71086.50
}
```

字段说明：

- `quoteAvg`：在售均价（元/平）。
- `dealAvg`：成交均价（元/平）。各平台筛选规则不同，见 [4. 业务取值规则](#4-业务取值规则)。
- `finalPrice`：最终建议单价（元/平）。

### 查询和更新加权落点中位数折扣

`GET /admin/algorithm/weighted-median-discount`

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "weightedMedianDiscount": 0.9,
    "isDefault": true
  }
}
```

`PUT /admin/algorithm/weighted-median-discount`

请求体：

```json
{
  "weightedMedianDiscount": 0.85
}
```

返回：

```json
{
  "code": "OK",
  "message": "参数已更新",
  "data": { "weightedMedianDiscount": 0.85 }
}
```

- 值必须在 `(0, 1)` 区间，否则返回 400。
- 更新后立即持久化到 `persist/runtime.json`，重启后自动恢复。

### 服务未就绪

```json
{
  "code": "SERVICE_NOT_READY",
  "message": "RPA 服务尚未就绪",
  "data": {
    "serviceStatusCode": "WAIT_LOGIN",
    "serviceStatus": "等待登录"
  }
}
```

## 12. 崩溃恢复与弱持久化

### 任务持久化

- 每个询价任务入队时写一个 `persist/{taskId}.json`，内容为 `InquiryRequest` 的序列化。
- 任务执行完成（成功或失败）后立即删除对应文件。
- 进程崩溃重启后，当**全部平台首次确认就绪**时（`_refresh_service_status` 检测到 all READY），
  自动遍历 `persist/*.json` 恢复所有残留任务重新入队（`_restored` 标志保证只恢复一次）。
  恢复先于服务置 READY，保证残留任务排在就绪后接的新单之前（先来后到）。

### 算法参数持久化

- `weightedMedianDiscount` 通过 `PUT /admin/algorithm/weighted-median-discount` 更新时，同步写入 `persist/runtime.json`。
- 启动时自动读取，文件不存在则使用默认值（`0.9`）。
- 这是**弱持久化**：仅保证重启不丢失，不做分布式一致性等强保证。

### 持久化文件结构

```
persist/                  # 项目根目录下
├── runtime.json          # 算法参数（常驻）
└── {taskId}.json         # 任务数据（入队写，完成删）
```

## 13. 日志与调试

### 日志

日志输出到：

- 控制台
- `logs/YYYYMMDD-info.log`
- `logs/YYYYMMDD-error.log`（`WARNING` 及以上级别）

日志内容重点包括：

- 查询城市、小区与面积
- 平台城市支持检查结果（不支持的平台打印原因和可支持城市列表）
- 城市切换导航日志
- 平台抓到的房源摘要
- 在售均价 / 成交均价 / 最终取值
- 异常和风控信息
- 参数变更记录

### 调试 HTML

开启调试模式（`--debug`、兼容参数 `--excel`，或 `RPA_DEBUG=1`）后，关键页面 HTML 导出到 `excel/*.html`。

主要用于：

- 定位页面结构变化
- 分析点击失败
- 排查风控跳转
- 分析分页 DOM

## 14. 当前约束

- 运行环境以 Windows 值守机为前提。
- 浏览器使用 Chrome（`config.BROWSER_PATH`）。
- 平台需要人工前置登录。
- 命中平台人机验证时，仍需要人工介入。
- 任务串行执行；每个平台分配独立浏览器实例，采集时多平台并行（`asyncio.gather`）。
- 服务层汇总所有 `SUCCESS` 平台的在售数据，再走加权落点中位数算法计算最终价。
- 各平台均有独立 HTML 解析器（`parsers/<code>.py`），与 adapter 的浏览器操作分离。
- **多城市支持**：API 入参 `city` 为必填字段，当前覆盖广东省 21 个地级市。各平台城市覆盖数不同（见 [§2](#2-已接入平台及差异)），不支持某城市的平台自动跳过询价只做保活刷新，全部平台都不支持时返回 `NO_DATA`。城市切换导航在薄壳层完成（`base.py:ensure_city_navigated`），adapter 内 `reset_to_start_page` 只做同城刷新。

这些约束是有意为之，优先保证稳定可用，而不是过早做复杂并发或多浏览器编排。
