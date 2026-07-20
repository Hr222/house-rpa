# jeethink-rpa

面向房产询价场景的 RPA 服务工程。已接入贝壳、安居客、链家、房天下、乐有家 5 个房产平台，支持广东省多城市询价，整体按"多平台可扩展"思路设计。

项目目标不是一次性脚本，而是一套可长期驻留、可人工介入、可接收外部询价请求的 RPA 工程。

## 目录索引

- [1. 项目定位](#1-项目定位)
- [2. 已接入平台及差异](#2-已接入平台及差异)
- [3. 业务链路](#3-业务链路)
- [4. 业务取值规则](#4-业务取值规则)
- [5. 架构分层](#5-架构分层)
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

5 平台全部覆盖的城市（9 个）：广州、深圳、珠海、佛山、东莞、中山、惠州、江门、清远。

### 平台差异说明

- **面积筛选**：所有平台已统一为"动态读取页面 HTML 档位 + 点击对应链接"的方式，
  由 `base.py` 的 `click_area_segment` 提供通用逻辑，各平台只需实现 `parsers.parse_area_segments`。
- **安居客**：无成交记录，业务上用挂牌均价顶替 `deal_prices`；无分页，滚动到底即可；不点详情页。
- **乐有家**：同安居客，无成交记录，小区均价顶替 `deal_prices`；搜索走 URL 参数。
- **链家**：贝壳子公司，DOM 高度相似；成交筛选用严格区间 + 近半年（与贝壳 ±20% 容差不同）。
- **房天下**：成交筛选用严格区间 + 近半年；详情入口只在第一页，Ctrl+点击后台打开。

## 3. 业务链路

通用主流程（各平台按差异微调）：

1. 启动浏览器并打开各平台二手房首页。
2. 人工在前台完成各平台登录。
3. 通过 API `POST /admin/platforms/{code}/confirm-ready` 或终端回车确认就绪。
4. 接收询价请求：`city`（城市名）、`communityName`（小区名）、`area`（精确面积）。
5. 检查各平台是否支持该城市：不支持的平台跳过询价只做保活刷新；支持的继续。
6. 城市导航：如果当前浏览器不在目标城市域名下，先导航到目标城市首页。
7. 刷新常驻页面，执行轻量保活。
8. 搜索目标小区。
9. 结果页按面积筛选。
10. 抓取主结果区在售单价，过滤推荐/广告区块。
11. 如有分页，按真实点击页码方式翻页并采集。
12. 如需详情页，打开小区详情。
13. 抓取小区均价和成交案例（平台有则采，无则跳过或顶替）。
14. 对成交案例按面积筛选后计算成交均价。
15. 按业务规则计算最终单价。
16. 返回结果，页面回到待命状态。

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
- **安居客 / 乐有家**：无成交记录，用平台挂牌均价顶替 `deal_prices`。

### 最终取值

代码位置：`app/core/algorithm.py:decide()`

- 若 `quoteAvg` 和 `dealAvg` 都存在：
  - 先计算差值比例：`|quoteAvg - dealAvg| / dealAvg`
  - 若差值比例 `<= 10%`，取较低值。（`TAKE_LOWER`）
  - 若差值比例 `> 10%`，只取 `dealAvg`。（`DEAL_ONLY`）
- 若没有 `dealAvg`，取 `quoteAvg * noDealDiscount`（默认 0.9）。（`QUOTE_DISCOUNT`）
- 若没有 `quoteAvg` 但有 `dealAvg`，直接取 `dealAvg`。（`DEAL_ONLY`）
- 都没有：`FAILED`。

其中 `noDealDiscount` 可通过 API 动态调整（见 [11. API 约定](#11-api-约定)）。

### 纯在售算法（`quote_only` 模式）

通过 API 请求体 `"algorithmMode": "quote_only"` 切换到此模式。

代码位置：`app/core/algorithm.py:decide_quote_only()`

- 聚合所有平台在售均价 → `quote_avg`
- 最终单价 = `quote_avg × quoteOnlyDiscount`（默认 0.9）
- branch：`QUOTE_ONLY`
- 无在售数据：`NO_DATA`

其中 `quoteOnlyDiscount` 可通过 API 动态调整（见 [11. API 约定](#11-api-约定)）。

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
│  │  ├─ city_map.py        # 跨平台城市映射表（5 平台 × 广东 21 城）
│  │  ├─ __init__.py        # 平台导出集合
│  │  ├─ ke.py / ke_constants.py       # 贝壳：薄壳适配器 + 常量
│  │  ├─ ajk.py / ajk_constants.py     # 安居客：薄壳适配器 + 常量
│  │  ├─ lj.py / lj_constants.py       # 链家：薄壳适配器 + 常量
│  │  ├─ fang.py / fang_constants.py   # 房天下：薄壳适配器 + 常量
│  │  ├─ lyj.py / lyj_constants.py     # 乐有家：薄壳适配器 + 常量
│  │  └─ adapters/
│  │     ├─ ke.py           # 贝壳真实采集逻辑
│  │     ├─ ajk.py          # 安居客真实采集逻辑
│  │     ├─ lj.py           # 链家真实采集逻辑
│  │     ├─ fang.py         # 房天下真实采集逻辑
│  │     └─ lyj.py          # 乐有家真实采集逻辑
│  ├─ parsers/
│  │  ├─ ke.py              # 贝壳 HTML 解析器
│  │  ├─ ajk.py             # 安居客 HTML 解析器
│  │  ├─ lj.py              # 链家 HTML 解析器
│  │  ├─ fang.py            # 房天下 HTML 解析器
│  │  └─ lyj.py             # 乐有家 HTML 解析器
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
│  │  └─ lyj_mvp_test.py    # 乐有家 MVP 测试
│  ├─ api.py                # FastAPI 路由定义
│  ├─ runtime.py            # 服务运行时
│  ├─ service.py            # 服务编排
│  └─ registry.py           # 平台注册
├─ tests/                   # 单元测试
├─ docs/                    # 对接文档
├─ requirements.txt
└─ README.md
```

## 7. 核心模块说明

### `app/core/config.py`

运行配置中心，包含：

- 调试开关（`DEBUG_MODE`）
- 浏览器路径、API 监听地址
- 风控参数（保活间隔、详情页停留时间等）
- 算法参数：
  - `DEAL_DIFF_THRESHOLD = 0.10` — 差值阈值
  - `get_no_deal_discount()` / `set_no_deal_discount()` — 无成交折扣，支持弱持久化

### `app/core/models.py`

平台无关的数据模型：

- `InquiryRequest` — 询价请求
- `PlatformResult` — 单平台采集结果（含在售列表、成交列表、房源快照）
- `InquiryResult` — 最终询价结果（含决策分支、最终价格）
- `ListingSnapshot` / `DealRecord` — 房源摘要 / 成交记录

### `app/core/algorithm.py`

纯函数，无 IO，所有平台共用。两套算法可通过 `algorithmMode` 切换：
- `decide(quote_avg, deal_avg, diff_threshold, no_deal_discount)` — 4 条决策分支，默认算法
- `decide_quote_only(quote_avg, quote_discount)` — 纯在售算法，仅在售均价打折输出

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
| GET | `/admin/algorithm/no-deal-discount` | 查询无成交折扣 |
| PUT | `/admin/algorithm/no-deal-discount` | 更新无成交折扣 |
| GET | `/admin/algorithm/quote-only-discount` | 查询纯在售折扣 |
| PUT | `/admin/algorithm/quote-only-discount` | 更新纯在售折扣 |

### `app/runtime.py`

服务运行时核心。职责：

- 启动常驻浏览器。
- 打开各平台常驻页面。
- 维护平台状态（`PlatformRuntimeState`）。
- 管理任务队列（`asyncio.Queue`，串行消费）。
- 定时保活循环（默认 120s）。
- 崩溃恢复：全部平台首次就绪后，从 `persist/` 恢复未完成任务（只一次）。
- 需要人工处理时尝试将浏览器置前。

### `app/service.py`

平台调度与结果汇总层。

- `build_inquiry_result()` — 把所有 `SUCCESS` 平台的在售均价、成交单价跨平台累加平均后，根据 `algorithm_mode` 选择调用 `decide()` 或 `decide_quote_only()` 算最终价。
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
| `wait_and_reload_after_block(tab, detect_func, label)` | 详情/成交页风控统一处理：检测→等人回车→重取，最多 2 次 |
| `_human_click(page, element, label)` | 真人节奏点击（JS 优先，随机间隔） |
| `safe_select_and_click(page, selector, ...)` | 安全选择+点击：找不到元素时 dump + 风控检测 + 恢复后重试 |
| `check_empty_listing_page(page_no, count, consecutive_empty, total_pages, platform)` | 翻页空页检测：首页空→error+停止，连续2页空→warning+停止（4 个翻页平台共用） |
| `click_area_segment(page, area, parse_func, code)` | 动态读取面积档位并点击匹配项 |
| `is_generic_captcha_page(html)` | 通用验证码页兜底检测（跨平台共性） |
| `short_circuit_result(name, status, reason, ...)` | 统一构造短路返回（NO_DATA 等），消除各平台重复模板 |
| `community_name_match(request_name, page_name)` | 小区名匹配（容忍分期括号 + 命名差异） |

### `app/platforms/<code>.py` + `adapters/<code>.py`

平台适配器两件套：

- 薄壳适配器（`platforms/<code>.py`）：实现 `PlatformAdapter` 接口，委托给 adapter。
  `collect()` 流程：`check_city_support` → `ensure_city_navigated` → `adapter.collect(city=...)` → 复位回首页。
- 采集逻辑（`platforms/adapters/<code>.py`）：搜索、筛选、分页、解析、风控检测等真实逻辑。
  `collect()` 和 `reset_to_start_page()` 均接收 `city` 参数。

### `app/platforms/city_map.py`

跨平台城市映射表，维护 5 平台 × 广东 21 个地级市的 URL 前缀。

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
| `DEAL_DIFF_THRESHOLD` | `0.10` | 差值阈值 |
| `get_no_deal_discount()` | `0.9` | 无成交折扣（可运行时更新，弱持久化） |
| `get_quote_only_discount()` | `0.9` | 纯在售折扣（可运行时更新，弱持久化） |

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

- `--debug` — 开启调试模式，导出关键页面 HTML 到 `debug/` 目录
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
  "communityName": "绿景虹湾",
  "area": 89.5,
  "requestId": "demo-001",
  "algorithmMode": "default"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `city` | string | 是 | 城市名（如 深圳、广州、东莞） |
| `communityName` | string | 是 | 小区名称 |
| `area` | number | 是 | 精确面积（㎡） |
| `algorithmMode` | string | 否 | 算法模式，`"default"`（成交+在售）或 `"quote_only"`（纯在售），默认 `"default"` |
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
  "branchCode": "TAKE_LOWER",
  "branch": "差异在阈值内，取较低值"
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

### 查询无成交折扣

`GET /admin/algorithm/no-deal-discount`

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "noDealDiscount": 0.9,
    "isDefault": true
  }
}
```

### 更新无成交折扣

`PUT /admin/algorithm/no-deal-discount`

请求体：

```json
{
  "noDealDiscount": 0.85
}
```

返回：

```json
{
  "code": "OK",
  "message": "参数已更新",
  "data": { "noDealDiscount": 0.85 }
}
```

- 值必须在 `(0, 1)` 区间，否则返回 400。
- 更新后立即持久化到 `persist/runtime.json`，重启后自动恢复。

### 查询纯在售折扣

`GET /admin/algorithm/quote-only-discount`

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "quoteOnlyDiscount": 0.9,
    "isDefault": true
  }
}
```

### 更新纯在售折扣

`PUT /admin/algorithm/quote-only-discount`

请求体：

```json
{
  "quoteOnlyDiscount": 0.85
}
```

返回：

```json
{
  "code": "OK",
  "message": "参数已更新",
  "data": { "quoteOnlyDiscount": 0.85 }
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

- `noDealDiscount` / `quoteOnlyDiscount` 通过 `PUT /admin/algorithm/...` 更新时，同步写入 `persist/runtime.json`。
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

日志内容重点包括：

- 查询城市、小区与面积
- 平台城市支持检查结果（不支持的平台打印原因和可支持城市列表）
- 城市切换导航日志
- 平台抓到的房源摘要
- 在售均价 / 成交均价 / 最终取值
- 异常和风控信息
- 参数变更记录

### 调试 HTML

开启调试模式（`--debug` 或 `RPA_DEBUG=1`）后，关键页面 HTML 导出到 `debug/*.html`。

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
- 服务层把所有 `SUCCESS` 平台的在售均价、成交单价**跨平台累加平均**后，再走 `decide()` 算最终价（不再是取第一个 `SUCCESS` 平台）。
- 各平台均有独立 HTML 解析器（`parsers/<code>.py`），与 adapter 的浏览器操作分离。
- **多城市支持**：API 入参 `city` 为必填字段，当前覆盖广东省 21 个地级市。各平台城市覆盖数不同（见 [§2](#2-已接入平台及差异)），不支持某城市的平台自动跳过询价只做保活刷新，全部平台都不支持时返回 `NO_DATA`。城市切换导航在薄壳层完成（`base.py:ensure_city_navigated`），adapter 内 `reset_to_start_page` 只做同城刷新。

这些约束是有意为之，优先保证稳定可用，而不是过早做复杂并发或多浏览器编排。
