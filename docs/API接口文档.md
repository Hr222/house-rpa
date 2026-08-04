# jeethink-rpa API 接口文档

> 基础地址：`http://127.0.0.1:8000`（默认，可通过 `API_HOST` / `API_PORT` 配置）

## 目录

- [接口总览](#接口总览)
- [健康检查](#健康检查)
  - [`GET /health/live`](#get-healthlive)
  - [`GET /health/ready`](#get-healthready)
- [管理接口](#管理接口)
  - [`GET /admin/status`](#get-adminstatus)
  - [`POST /admin/platforms/{code}/confirm-ready`](#post-adminplatformscodeconfirm-ready)
- [询价接口](#询价接口)
  - [`POST /inquiries` — 创建询价任务](#post-inquiries--创建询价任务)
  - [`GET /inquiries/{taskId}` — 查询任务结果](#get-inquiriestaskid--查询任务结果兜底)
- [结果回调](#结果回调)
- [算法参数](#算法参数)
  - [`GET /admin/algorithm/weighted-median-discount`](#get-adminalgorithmweighted-median-discount)
  - [`PUT /admin/algorithm/weighted-median-discount`](#put-adminalgorithmweighted-median-discount)
- [任务状态码](#任务状态码)
- [业务决策分支](#业务决策分支)

---

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/live` | 存活检查 |
| GET | `/health/ready` | 就绪检查 |
| GET | `/admin/status` | 服务状态 |
| POST | `/admin/platforms/{code}/confirm-ready` | 确认平台就绪 |
| POST | `/inquiries` | 创建询价任务 |
| GET | `/inquiries/{taskId}` | 查询任务结果（兜底，受限流） |
| GET | `/admin/algorithm/weighted-median-discount` | 查询加权落点中位数折扣 |
| PUT | `/admin/algorithm/weighted-median-discount` | 更新加权落点中位数折扣 |

---

## 健康检查

### `GET /health/live`

服务进程存活检查，始终返回 200。

**响应 200：**

```json
{
  "code": "OK",
  "message": "服务进程运行中",
  "data": { "status": "存活" }
}
```

### `GET /health/ready`

服务就绪检查。全部平台确认就绪后返回 200，否则 503。

**响应 200（已就绪）：**

```json
{
  "code": "OK",
  "message": "服务已就绪",
  "data": {
    "serviceStatusCode": "READY",
    "serviceStatus": "已就绪",
    "message": "ready",
    "currentTaskId": null,
    "queueSize": 0,
    "platforms": [
      {
        "code": "ke",
        "name": "贝壳",
        "startUrl": "https://sz.ke.com/ershoufang/",
        "statusCode": "READY",
        "status": "已就绪",
        "message": "平台已就绪",
        "lastReadyAt": "2026-07-16T14:30:00",
        "lastKeepaliveAt": "2026-07-16T14:32:00"
      }
    ]
  }
}
```

**响应 503（未就绪）：**

```json
{
  "code": "SERVICE_NOT_READY",
  "message": "RPA 服务尚未就绪",
  "data": {
    "serviceStatusCode": "WAIT_LOGIN",
    "serviceStatus": "等待登录",
    "message": "等待各平台人工完成登录…",
    "platforms": [...]
  }
}
```

---

## 管理接口

### `GET /admin/status`

查看服务运行状态，与 `/health/ready` 格式一致但始终返回 200（就绪或未就绪都返回）。

### `POST /admin/platforms/{code}/confirm-ready`

人工完成某平台登录后，确认该平台已就绪。

| 参数 | 位置 | 说明 |
|------|------|------|
| `code` | path | 平台代码：`ke` / `ajk` / `lj` / `fang` / `lyj` |

**响应 200：**

```json
{
  "code": "OK",
  "message": "平台状态已更新",
  "data": {
    "code": "ke",
    "name": "贝壳",
    "statusCode": "READY",
    "status": "已就绪"
  }
}
```

**响应 404：** 未找到对应平台

---

## 询价接口

### `POST /inquiries` — 创建询价任务

发起一次房产询价。服务将自动在 5 个平台上采集数据，取最终报价。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `communityName` | string | ✅ | 小区名称 |
| `administrativeDistrict` | string | ✅ | 行政区（如 `南山区`）；用于行舟深房同名小区消歧 |
| `area` | float | ✅ | 精确面积（㎡），如 `89.5`。系统自动匹配各平台面积档位 |
| `city` | string | ✅ | 城市名（如 `深圳`、`广州`、`东莞`） |
| `requestId` | string | | 请求标识，用于幂等；不填则由服务生成 `taskId` |

**请求示例：**

```json
{
  "city": "深圳",
  "administrativeDistrict": "南山区",
  "communityName": "绿景虹湾",
  "area": 89.5,
  "requestId": "order-001"
}
```

**响应 202（已受理）：**

```json
{
  "code": "ACCEPTED",
  "message": "询价任务已受理",
  "data": {
    "taskId": "order-001",
    "status": "排队中",
    "statusCode": "QUEUED"
  }
}
```

**响应 503（服务未就绪）：**

```json
{
  "code": "SERVICE_NOT_READY",
  "message": "RPA 服务尚未就绪",
  "data": { ... }
}
```

---

### `GET /inquiries/{taskId}` — 查询任务结果（兜底）

查询指定询价任务的执行结果。**建议优先使用回调推送**（见下方 [结果回调](#结果回调)），此接口仅作兜底。

| 参数 | 位置 | 说明 |
|------|------|------|
| `taskId` | path | 任务 ID |

**限流规则：** 同一 `taskId` 两次查询最小间隔 10 秒（可通过 `RPA_GET_MIN_INTERVAL` 配置），超频返回 429。

**响应 200（已完成）：**

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "quoteAvg": 85635.00,
    "dealAvg": 71086.50,
    "finalPrice": 71086.50,
    "success": true,
    "statusCode": "COMPLETED",
    "branchCode": "WEIGHTED_MEDIAN",
    "branch": "主要价格落点中位数折扣"
  }
}
```

| 字段 | 说明 |
|------|------|
| `quoteAvg` | 主要价格落点中位数（元/㎡） |
| `dealAvg` | 兼容字段，当前算法不参与决策 |
| `finalPrice` | 最终建议单价（元/㎡） |
| `success` | 本次询价是否得到可用结果；`NO_DATA` 时为 `false` |
| `statusCode` | 任务状态码；完成态固定为 `COMPLETED` |
| `branchCode` | 决策分支：`WEIGHTED_MEDIAN` / `WEIGHTED_MEDIAN_MULTI` / `NO_DATA` / `NO_MATCHING_AREA` / `FAILED` |
| `branch` | 分支说明中文文案；未登记的分支才回退为分支码 |
| `note` | 可选，补充说明；例如所有平台都不支持该城市时返回 `"不支持该城市"` |
| `referenceCode` | 可选；最终选中的价格峰实际使用面积弱参考时为 `WEAK_AREA_REFERENCE` |
| `referenceAreaTolerance` | 可选；弱参考实际使用的请求面积对称容差，单位㎡ |
| `referenceAreaMin` / `referenceAreaMax` | 可选；弱参考实际使用的面积范围，单位㎡ |
| `referenceListingCount` | 可选；进入弱参考的房源数量，单条严格命中时也会计为 1 |

弱参考不是新的状态码或决策分支。公开询价响应不会返回 `platformResults`；平台级弱参考字段仅保留在运行时内部结果、操作日志和 Excel 分析数据中。最终公开结果只有在选中的价格峰确实包含该平台补充数据时才输出顶层弱参考字段。最大面积容差默认 `20㎡`，可通过环境配置 `RPA_WEAK_AREA_MAX_TOLERANCE` 调整，当前暂不提供 API 修改入口。

**响应 200（已完成但无数据）：**

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "quoteAvg": null,
    "dealAvg": null,
    "finalPrice": null,
    "success": false,
    "statusCode": "COMPLETED",
    "branchCode": "NO_DATA",
    "branch": "无可用数据",
    "note": "不支持该城市"
  }
}
```

说明：`NO_DATA` 也是**已完成**状态，因此 HTTP 仍返回 `200`；客户端应结合 `success=false` 和 `branchCode=NO_DATA` 判断“任务结束但无可用报价”。

**响应 200（进行中）：**

```json
{
  "code": "OK",
  "message": "查询成功",
  "data": {
    "taskId": "order-001",
    "status": "收集中",
    "statusCode": "RUNNING"
  }
}
```

**响应 429（过于频繁）：**

```json
{
  "code": "TOO_MANY_REQUESTS",
  "message": "查询过于频繁，请在 10 秒后重试",
  "data": {
    "taskId": "order-001",
    "retryAfter": 10
  }
}
```

**响应 404：** 未找到对应任务

---

## 多峰结果

当识别到多个频率接近的价格峰时，任务仍视为成功完成，
服务选择最低价格峰的中位数作为 `quoteAvg` 和 `finalPrice`，且不打折；结果中的 `candidates` 仍保留全部峰值用于审计展示：

| 字段 | 说明 |
|------|------|
| `quotePrice` | 该价格峰的房源落点中位数（元/㎡） |
| `finalPrice` | 单峰为按在售折扣计算后的价格；多峰为该峰中位数直接返回的价格（元/㎡） |
| `count` | 落入该价格峰的房源数量 |
| `frequency` | 该峰占全部有效房源的比例 |
| `minPrice` / `maxPrice` | 该价格峰的落点范围 |

多峰时服务会直接选择最低价格峰的中位数作为 `quoteAvg` 和 `finalPrice`，不再打折；`candidates` 仍保留全部价格峰供审计展示。客户端不应再次平均或强行合并候选。

## 结果回调

配置环境变量 `RPA_CALLBACK_URL` 后，任务完成（成功或失败）时服务主动 `POST` 推送结果到 `{CALLBACK_URL}/{taskId}`。

> 这是**主机制**，客户端无需轮询 `GET /inquiries/{taskId}`。

**成功回调：**

```json
{
  "taskId": "order-001",
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

**失败回调：**

```json
{
  "taskId": "order-001",
  "statusCode": "FAILED",
  "status": "失败",
  "success": false,
  "error": "采集异常原因"
}
```

**可靠性：** 推送失败自动重试（默认 3 次，递增延迟），全部失败仅记日志不影响结果落库。

---

## 算法参数

### `GET /admin/algorithm/weighted-median-discount` — 查询加权落点中位数折扣

单峰结果将主要价格落点中位数乘以该折扣作为最终价，多峰结果直接返回最低价格峰中位数。

**响应：**

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

| 字段 | 说明 |
|------|------|
| `weightedMedianDiscount` | 当前加权落点中位数折扣值，默认 `0.9` |
| `isDefault` | 是否为默认值（未被人为修改过） |

### `PUT /admin/algorithm/weighted-median-discount` — 更新加权落点中位数折扣

运行时动态调整折扣系数，立即生效并持久化，重启后自动恢复。

**请求体：**

```json
{
  "weightedMedianDiscount": 0.85
}
```

| 约束 | 值 |
|------|-----|
| 有效范围 | `(0, 1)`，不包含 0 和 1 |

**响应：**

```json
{
  "code": "OK",
  "message": "参数已更新",
  "data": { "weightedMedianDiscount": 0.85 }
}
```

**响应 400：** 值不在 `(0, 1)` 区间

---

## 任务状态码

| statusCode | 说明 |
|------------|------|
| `QUEUED` | 排队中 |
| `RUNNING` | 收集中（浏览器正在操作） |
| `COMPLETED` | 已完成 |
| `FAILED` | 执行失败 |

## 业务决策分支

| branchCode | 条件 | 说明 |
|------------|------|------|
| `WEIGHTED_MEDIAN` | 存在明确主要价格落点 | 主要价格峰中位数 × `weightedMedianDiscount` |
| `WEIGHTED_MEDIAN_MULTI` | 存在多个频率接近的价格峰 | 取最低价格峰中位数直接返回，不打折；同时保留 `candidates` |
| `NO_DATA` | 全平台无可用在售数据，或该城市所有平台都不支持 | 任务已完成，但无可用报价 |
| `NO_MATCHING_AREA` | 全平台均未命中请求面积 | 任务已完成，但无匹配面积房源 |
| `FAILED` | 无在售也无成交 | 无法计算 |
