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
  - [`GET /admin/algorithm/no-deal-discount`](#get-adminalgorithmno-deal-discount)
  - [`PUT /admin/algorithm/no-deal-discount`](#put-adminalgorithmno-deal-discount)
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
| GET | `/admin/algorithm/no-deal-discount` | 查询无成交折扣 |
| PUT | `/admin/algorithm/no-deal-discount` | 更新无成交折扣 |

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
| `area` | float | ✅ | 精确面积（㎡），如 `89.5`。系统自动匹配各平台面积档位 |
| `city` | string | | 城市，默认 `"深圳"` |
| `requestId` | string | | 请求标识，用于幂等；不填则由服务生成 `taskId` |

**请求示例：**

```json
{
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
    "branchCode": "TAKE_LOWER",
    "branch": "差异在阈值内，取较低值"
  }
}
```

| 字段 | 说明 |
|------|------|
| `quoteAvg` | 在售均价（元/㎡），所有成功平台累加平均 |
| `dealAvg` | 成交均价（元/㎡），所有成功平台累加平均 |
| `finalPrice` | 最终建议单价（元/㎡） |
| `branchCode` | 决策分支：`TAKE_LOWER` / `DEAL_ONLY` / `QUOTE_DISCOUNT` / `FAILED` |

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
  "branchCode": "TAKE_LOWER",
  "branch": "差异在阈值内，取较低值"
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

### `GET /admin/algorithm/no-deal-discount` — 查询无成交折扣

当所有平台都没有成交记录时，在售均价乘以该折扣作为最终价。

**响应：**

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

| 字段 | 说明 |
|------|------|
| `noDealDiscount` | 当前折扣值，默认 `0.9` |
| `isDefault` | 是否为默认值（未被人为修改过） |

### `PUT /admin/algorithm/no-deal-discount` — 更新无成交折扣

运行时动态调整折扣系数，立即生效并持久化，重启后自动恢复。

**请求体：**

```json
{
  "noDealDiscount": 0.85
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
  "data": { "noDealDiscount": 0.85 }
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
| `TAKE_LOWER` | 在售均价与成交均价差值 ≤ 10% | 取两者中较低值 |
| `DEAL_ONLY` | 差值 > 10%，或只有成交价 | 只取成交均价 |
| `QUOTE_DISCOUNT` | 无成交数据 | 在售均价 × `noDealDiscount` |
| `FAILED` | 无在售也无成交 | 无法计算 |
