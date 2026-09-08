# jeethink-rpa —— 二手房找房比价服务

> 基于 FastAPI + nodriver 的多平台二手房找房比价服务：浏览器常驻、人工登录确认、
> 串行比价任务、多平台并行采集、加权落点中位数估值的房产数据模块。

专项文档：[ARCHITECTURE 架构与运行时状态](ARCHITECTURE.md)（**改动代码前必读**） ·
[平台扩展对接](docs/平台扩展对接文档.md) ·
[API 接口详情](docs/API接口文档.md) ·
[小区主数据与房源记录](docs/小区基础数据模块.md) ·
[AGENTS 编码约束](AGENTS.md)

## 目录

- [1. 项目定位与特性](#1-项目定位与特性)
- [2. 已接入平台及差异](#2-已接入平台及差异)
- [3. 快速开始](#3-快速开始)
- [4. 业务链路与取值规则](#4-业务链路与取值规则)
- [5. API 约定](#5-api-约定)
- [6. 日志、调试与排错](#6-日志调试与排错)
- [7. 多城市支持](#7-多城市支持)
- [8. 当前约束](#8-当前约束)

> 系统架构、运行时状态机、并发与风控协议、比价链路与崩溃恢复见
> [ARCHITECTURE.md](ARCHITECTURE.md)（**改动代码前必读**）。

## 1. 项目定位与特性

当前版本重点解决以下问题：

- 浏览器常驻，不为每次比价重新冷启动。
- 由人工先完成登录，确认就绪后才允许接单。
- 后台接收比价请求，并串行执行采集流程。
- 支持**多城市比价**：API 入参含 `city`（城市名），各平台按城市映射表导航到对应城市首页后再采集。
- 采集完成后主动 POST 回调通知客户端（客户端无需轮询）；GET 查询保留作兜底并受限流。
- 平台被风控或登录失效时，服务状态可明确降级。
- 调试模式下可导出关键 HTML，方便定位页面结构变化。
- 日志按自然日切分，适合 7x24 值守机运行。
- 编排层在确认 `community_id` 后弱持久化完整比价任务，进程崩溃后重启自动恢复未完成任务。
- 算法参数（无成交折扣）支持运行时动态更新，弱持久化重启不丢失。
- 热数据短路：同一小区 ≥3 个平台（`RPA_HOT_DATA_MIN_PLATFORMS`）在热窗口内
  （默认 48h，`RPA_HOT_DATA_MAX_AGE_HOURS`）更新过数据时，这些平台跳过 RPA
  直接用库内数据合成结果，其余平台照常采集；没有在架数据的平台不算热。

## 2. 已接入平台及差异

| 平台 | code | 采集内容 | 翻页方式 | 成交记录 | 小区均价 | 广东城市覆盖 |
|------|------|---------|---------|---------|---------|-------------|
| 贝壳 | ke | 在售快照 | URL 直达翻页 | 无 | 无 | 12/21 |
| 安居客 | ajk | 在售快照 | URL 直达翻页 | 无 | 列表页社区卡片 | 21/21 |
| 链家 | lj | 在售 + 成交明细 | URL 直达翻页 | 成交页 URL 直达 | 无 | 10/21 |
| 房天下 | fang | 在售 + 成交明细 | URL 直达翻页 | 成交页 URL 直达 | 无 | 21/21 |
| 乐有家 | lyj | 在售快照 | URL 直达翻页 | 无 | 列表页信息卡 | 9/21 |

5 个网页平台全部覆盖的城市（9 个）：广州、深圳、珠海、佛山、东莞、中山、惠州、江门、清远。

### 平台差异说明

- **采集形态**：全部平台为"URL 白名单直达 + HTML 解析"，无搜索、无点击、无模拟人机交互；翻页为分页 URL 直达。
- **面积口径**：RPA 不做页面面积筛选，请求面积由算法层用于在售严格筛选/弱参考（`selection.py`）与成交收敛（`deal_screening.py`）。
- **小区归属**：所有平台直达挂牌页后先校验目标小区归属；分页平台逐页过滤，只累计目标小区房源。第 1 页非空但全部无关时返回 `NO_DATA`，第 2 页起非空但全部无关时立即停止后续翻页并保留此前有效数据；返回前再次校验，确保房源明细与在售价格来自同一批数据。
- **安居客**：在售列表 URL 直达（含分页 URL），挂牌均价取自列表页社区卡片，仅作溯源。
- **乐有家**：同安居客，小区均价取自结果页信息卡，仅作溯源。
- **链家 / 房天下**：成交页 URL 直达（白名单 `deal_page_url`），成交明细交算法层按平台口径收敛（面积容差 + 近半年）。

## 3. 快速开始

### 3.1 安装依赖

```bash
pip install -r requirements.txt
```

### 3.2 启动服务

必须用 `-m` 模块方式、在项目根目录执行（脚本 import 的是 `app.*` 包）：

```bash
python -m scripts.api_server --debug --manual-login
```

| 参数 | 作用 |
|---|---|
| `--debug`（兼容旧名 `--excel`） | 开启 RPA 调试模式，导出关键页面 HTML 到 `debug/` 目录 |
| `--manual-login` | 平台未就绪时在终端提示回车，人工完成登录后按回车继续。首次/登录失效时建议带上 |
| `--no-recovery` | 测试模式：启动时清空 `persist/inquiries` 残留快照并跳过崩溃恢复（正式值守不要加） |
| `--port` | API 监听端口，默认 8000；启动前先探测端口占用，被占直接报错退出（避免半启动） |

启动过程：为每个已注册平台拉起独立常驻 Chrome → 打开各平台常驻页（初始 `WAIT_LOGIN`）→
终端提示人工登录 → 回车后批量确认各平台就绪 → 全部 `READY` 后编排层恢复崩溃前残留快照
→ 恢复入队完成后开始接单。未就绪时收到比价请求返回 `503 SERVICE_NOT_READY`。

带 `--manual-login` 时一次回车对应一个完整确认批次；确认与保活互斥，不会出现
"保活抢先改状态、确认跳过平台"的竞态。

### 3.3 就绪确认

```bash
curl http://127.0.0.1:8000/health/live     # 200=进程存活
curl http://127.0.0.1:8000/health/ready    # 200=所有平台就绪且比价恢复完成；503=未就绪（data 带快照）
curl http://127.0.0.1:8000/admin/status    # 详细快照：serviceStatusCode、各平台 statusCode/message、currentTaskId
curl -X POST http://127.0.0.1:8000/admin/platforms/ke/confirm-ready   # 人工确认某平台就绪
```

### 3.4 单平台 MVP 测试

各平台 MVP 已瘦身为工程薄调用（采集主流程与工程完全同源），用于单平台核对：

```bash
python -m scripts.collect_community_data.rpa.ke_mvp_test --community-id 170 --debug
```

### 3.5 批量比价客户端（模拟客户端）

`scripts/batch_inquiry_evaluation.py` 是纯 HTTP 客户端：读比价清单（示例见
`example/二手房心理预期价格示例.xlsx`，其中 `评估单价` 列即你的**目标价**/心理价），
逐条 `POST /inquiries` 并轮询 `GET /inquiries/{taskId}`，最后写对比 Excel。
**必须等 `/health/ready` 返回 200 后再跑**：

```bash
# 冒烟：只跑清单前 1 条
python -m scripts.batch_inquiry_evaluation --limit 1

# 小批量（输入表可用 --input 换；默认 example/二手房心理预期价格示例.xlsx，30 行）
python -m scripts.batch_inquiry_evaluation --limit 5
```

- 两种模式：表含 `评估单价` 列 → 比价偏差分析（`results/评估对比_*.xlsx`）；
  无该列 → 纯批量比价（`results/分析_*.xlsx`）。
- 必需列：`行政区`、`小区名称`、`面积㎡`；可选：`评估单价`（目标价）、`city`（逐行优先于 `--city`）。
- 运行中现象属正常：偶发 429（客户端 6s 轮询 vs 服务端 10s 限流，脚本自动处理）；
  命中验证码/登录时客户端阻塞等待，人工在服务端浏览器处理后回车，客户端自动重试当前条。

## 4. 业务链路与取值规则

### 4.1 通用主流程

各平台按差异微调，主流程：

1. 启动浏览器并打开各平台二手房首页。
2. 人工在前台完成各平台登录。
3. 通过 API `POST /admin/platforms/{code}/confirm-ready` 或终端回车确认就绪。
4. 接收比价请求：`city`、`administrativeDistrict`、`communityName`、`area`。
5. 比价编排层查询人工维护的小区主数据：未找到/非住宅/多期直接返回；只有唯一小区才创建 RPA 任务，并以小区正式名继续采集。
6. 检查各平台是否支持该城市：不支持的平台跳过比价只做保活刷新。
7. 城市导航：浏览器不在目标城市域名下时先导航到目标城市首页。
8. 刷新常驻页面，执行轻量保活。
9. 按白名单 URL 直达小区挂牌页，解析后校验快照与目标小区归属（不符整页弃用）。
10. 抓取主结果区，过滤推荐/广告区块，并立即按目标小区过滤房源快照。
11. 如有分页，按页 URL 直达累加：第 1 页非空但全部无关返回 `NO_DATA`；第 2 页起整页无关立即停止，混合页只保留匹配快照；空页走独立空页检测。
12. 返回平台结果前再次过滤，并从同一批快照生成 `listing_snapshots` 与 `quote_prices`。
13. 如平台有成交页，独立直达采集全量真实成交。
14. 成交案例按算法层口径（面积容差 + 近半年）筛选后计算成交均价。
15. 按业务规则计算最终单价。
16. 返回结果，页面回到待命状态。

> 如果所有平台都不支持该城市，直接返回 `NO_DATA`，note 为"不支持该城市"。

核心返回字段：

```json
{ "quoteAvg": 85635.00, "dealAvg": 71086.50, "finalPrice": 71086.50 }
```

### 4.2 成交均价口径

- **贝壳**：成交按请求面积 ±5㎡ 筛选（日期不筛）。
- **链家 / 房天下**：严格面积区间 ±5㎡ + 近半年（6 个月）。
- **安居客 / 乐有家**：无成交明细，平台挂牌均价仅作溯源，不参与成交平均。

各平台口径统一收敛在 `app/algorithm/deal_screening.py` 的 `DEAL_SCREENING_RULES`。

### 4.3 最终取值：加权落点中位数

代码位置：`app/algorithm/weighted_median.py:WeightedMedianAlgorithm`。系统固定使用这一套算法。

- 每个平台总权重相等，平台内每条有效在售价格按数量分配权重。
- 以相对中位数 ±10% 识别密集价格峰；只有无法与其他报价组成价格簇的单点才作为孤立噪声排除。
- 多个已成簇的价格带都保留为候选，即使它们与最高频峰的出现次数差距较大。
- 同一平台内先按稳定房源编号或完整字段去重；跨平台不比较标题，小区全称/无歧义简称、面积、单价、总价必须精确一致（双方均有户型时户型也必须一致）。
- **单峰**：`final_price = quote_avg × weightedMedianDiscount`，分支 `WEIGHTED_MEDIAN`。
- **多峰**：取最低价格峰中位数直接返回，不打折，分支 `WEIGHTED_MEDIAN_MULTI`；`candidates` 保留全部峰值供审计。
- **存在真实目标面积成交价**：挂牌峰值不打折，与成交价等权平均，分支 `WEIGHTED_MEDIAN_COMBINED`（豪宅段按支撑数/价差规则合并）。均价顶替值不参与。
- 50/50 双峰且无法形成明确主要区间时，不人为计算两个区间之间的中间价。
- 面积弱参考：严格 ±1㎡ 无可用价格峰时，最多放宽 `RPA_WEAK_AREA_MAX_TOLERANCE`（默认 ±20㎡）扩展候选，结果带 `WEAK_AREA_REFERENCE` 说明字段。
- 折扣 `weightedMedianDiscount` 默认 0.9，可通过 API 动态调整并弱持久化。

## 5. API 约定

完整字段与示例见 [docs/API接口文档.md](docs/API接口文档.md)。接口总览：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/live` | 存活检查 |
| GET | `/health/ready` | 就绪检查（平台就绪 + 比价恢复完成） |
| GET | `/admin/status` | 服务状态（始终 200） |
| POST | `/admin/platforms/{code}/confirm-ready` | 确认平台就绪 |
| POST | `/inquiries` | 创建比价任务（202 受理；小区未找到/类型不支持/多期返回业务结果） |
| GET | `/inquiries/{taskId}` | 查询任务结果（兜底，同 taskId 最小间隔 10s，超频 429） |
| GET/PUT | `/admin/algorithm/weighted-median-discount` | 查询/更新加权落点中位数折扣（(0,1)，持久化） |

要点：

- **回调优先**：配置 `RPA_CALLBACK_URL` 后任务结束主动 `POST {CALLBACK_URL}/{taskId}`
  （失败重试 3 次），无需轮询；GET 仅兜底。
- `requestId` 是客户端请求标识，`taskId` 是服务端生成的 UUID；两者会在受理响应、查询结果和回调中同时返回。
- `GET /inquiries/{taskId}` 完成后返回 `quoteAvg/dealAvg/finalPrice/success/
  statusCode/branchCode/branch(/note/candidates/弱参考字段)`；`NO_DATA` 也是
  `COMPLETED`（HTTP 200 + `success=false`）。
- 未就绪创建比价返回 `503 SERVICE_NOT_READY`。

## 6. 日志、调试与排错

日志输出到控制台与 `logs/YYYYMMDD-info.log`、`logs/YYYYMMDD-error.log`
（WARNING 及以上），按自然日切分。内容覆盖：查询三要素、城市支持检查、城市切换、
房源摘要、均价与最终取值、异常风控、参数变更。

调试模式（`--debug`、兼容 `--excel`，或 `RPA_DEBUG=1`）把关键页面 HTML 导出到
`debug/`：定位页面结构变化、风控跳转、翻页 DOM。

排错入口：

| 现象 | 首先检查 |
|---|---|
| 服务启动失败 | `api_server.py`、Chrome/nodriver 启动日志 |
| 回车后平台状态不一致 | `runtime.py` 确认循环、保活日志、`/admin/status` |
| 验证码被识别为登录失效 | 平台 `detect_block` marker（基于真实 dump 校准）、`PlatformHealthEvent` |
| 任务异常但平台被降级 | `_apply_platform_results()` 版本保护 |
| 平台有数据但返回无数据 | 小区匹配、入口白名单、`prepare_listing_data()` |
| 价格偏差异常 | `app/algorithm/` 与平台原始结果明细 |

```powershell
Invoke-WebRequest http://127.0.0.1:8000/admin/status -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/health/ready -UseBasicParsing
```

## 7. 多城市支持

系统支持广东省 21 个地级市比价，API 入参 `city` 必填。各平台 URL 前缀命名规则不统一
（缩写/全拼混用），必须维护 `app/rpa/platforms/city_map.py` 的显式映射表
（`CITY_MAP[code][city] = prefix`，来源为城市选择页 dump）。新平台接入时同步登记
`CITY_MAP` 与 `_URL_PATTERNS`，详见[平台扩展对接文档](docs/平台扩展对接文档.md)。

## 8. 当前约束

- 运行环境以 Windows 值守机为前提；浏览器使用 Chrome（`config.BROWSER_PATH`）。
- 平台需要人工前置登录；命中人机验证时仍需人工介入。
- 任务串行执行；每个网页平台独立浏览器实例，采集时多平台并行（`asyncio.gather`）。
- 编排层汇总所有 `SUCCESS` 平台的原始数据，再走加权落点中位数算法计算最终价。
- 各平台均有独立纯函数解析器（`platforms/<code>/parser.py`），与 collector 的
  浏览器操作分离。
- **多城市支持**：`city` 为必填字段，覆盖广东省 21 个地级市；不支持某城市的平台自动
  跳过比价只做保活刷新，全部平台都不支持时返回 `NO_DATA`。城市切换导航在薄壳层完成。

这些约束是有意为之，优先保证稳定可用，而不是过早做复杂并发或多浏览器编排。
