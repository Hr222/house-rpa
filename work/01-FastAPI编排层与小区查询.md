# FastAPI 编排层与小区查询

> 状态：需求已确认，待实现。

## 1. 本块目标

由 FastAPI 接收请求，并通过询价编排层查询人工维护的小区主数据。只有取得唯一小区后，才进入 RPA 询价流程。

本块不负责平台采集、房源入库和价格算法。

## 2. 请求链路

```text
请求方
  -> FastAPI
  -> 询价编排层
  -> community_data.resolve_communities()
  -> 根据查询结果返回或创建 RPA 任务
```

请求方提交城市、行政区、小区名称、面积和可选请求标识，不提交 `community_id`。编排层取得的 `community_id` 只在服务内部传递给 RPA 和房源入库。

## 3. 查询结果

| 结果 | FastAPI 行为 | 是否进入 RPA |
|---|---|---|
| 没有记录 | 返回小区未找到 | 否 |
| 返回多个期数 | 返回期数候选，要求请求方明确具体期数 | 否 |
| 唯一记录 | 保存小区上下文并创建询价任务 | 是 |

多个期数不能按顺序、默认一期或 `community_group_id` 自动选择。

## 4. 小区上下文

唯一记录进入 RPA 前，由编排层构造任务上下文：

```text
community_id
community_group_id
city
administrative_district
canonical_name
aliases
phase
area
request_id
```

RPA 只接收已确认上下文，不调用 `community_data`，不创建小区，也不自行选择期数。

## 5. 人工维护边界

正常请求中的 `resolve_communities()` 必须为纯查询。数据库没有小区时立即返回未找到，不创建记录，不调用地理编码。

小区新增、别名和期数维护、建成年份补充、地理编码继续保留现有底层能力，由人工维护流程显式调用。不能删除 `CommunitySeed`、`insert_or_get()`、`add_seed()` 和相关维护逻辑，只需移除请求链路中的自动新增。

## 6. 重点验收

- 未知小区不会新增数据库记录。
- 未知小区不会创建 RPA 任务。
- 多期小区不会自动进入 RPA。
- 唯一小区进入任务时携带正确的 `community_id`、正式名和别名。
- 人工新增的小区后续可以被正常查询并进入询价流程。
