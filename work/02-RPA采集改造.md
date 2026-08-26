# RPA 采集改造

> 状态：需求已确认，待实现。

## 1. 本块目标

在不改变既有平台采集顺序、风控边界和估价算法的前提下，补齐 RPA 结构化采集结果，并从房源 HTML 的 `<a href>` 解析单套房源详情地址。

平台如何获取 HTML 不在本块提前统一规定。各平台按自身页面、登录态、风控和接口特征逐一验证。

## 2. RPA 输入

RPA 接收编排层已经确认的小区上下文：

```text
community_id
city
administrative_district
canonical_name
aliases
phase
area
request_id
```

RPA 不调用 `community_data`，不创建小区，不负责期数选择。

平台定位优先使用正式名；正式名无法定位时，按上下文中的别名逐个回退。搜索结果归属仍只比较查询名称和 `ListingSnapshot.community_name`，不能使用营销标题、搜索词或整页 HTML 绕过通用匹配规则。

## 3. 挂牌快照字段

每个平台 parser 从真实 HTML 提取统一的挂牌快照：

```text
house_id（平台可提供时保留）
community_name
title
layout
area
total_price
unit_price
listing_url
```

`listing_url` 必须来源于房源 `<a href>`，经过平台地址规范化后写入快照。没有有效地址时不得根据标题、房源编号或其他字段伪造 URL。

## 4. URL 字段边界

| 字段 | 含义 | 本阶段处理 |
|---|---|---|
| `community_detail_url` | 小区详情页 | 仅初始化阶段辅助维护，运行时不作为联动依据 |
| `listing_page_url` | 小区挂牌销售列表入口 | 采集到时可随结果保存，当前不触发自动更新 |
| `listing_url` | 单套房源详情地址 | 本阶段必须解析并用于挂牌入库唯一标识 |
| `deal_page_url` | 小区成交列表入口 | 采集到时可随结果保存，用于成交记录关联 |

四类地址不能互相替代。

## 5. 平台实施方式

HTML 获取和页面操作必须按平台真实情况验证。不能预先假定五个平台都使用同一套请求方式，也不能因为优化采集而擅自删除现有采集步骤。

每个平台先用既有 MVP 脚本核对真实 HTML，再修改 parser 和 adapter。解析器保持纯函数，浏览器会话和平台风控逻辑仍留在 adapter。

## 6. 重点验收

- 五个平台的挂牌 parser 能从真实 HTML 读取有效 `listing_url`。
- `listing_url` 指向单套房源详情，不误用小区列表地址。
- 正式名、别名定位结果仍经过小区名匹配校验。
- 平台原有成交差异和风控处理不被改变。
- 结构化结果能够携带小区级挂牌页和成交页地址（平台实际提供时）。
