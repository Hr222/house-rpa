# -*- coding: utf-8 -*-
"""跨模块的询价业务编排：小区确认、任务生命周期与结果聚合。

负责：小区身份确认并持有 ConfirmedCommunityContext、询价任务的创建
与弱持久化（persist/inquiries/{taskId}.json）及崩溃恢复。taskId 由服务端生成，
客户端 requestId 作为关联标识随快照保留。原始 RPA
采集结果交给算法聚合成最终询价结果。
不负责：浏览器与平台采集（app.rpa）、房源入库
（app.property_records）、小区主数据维护（app.community_data）。

内部：orchestrator.py 小区确认与 RPA 任务创建编排 |
task_manager.py 任务生命周期 | task_store.py 任务弱持久化 |
aggregation.py 采集数据到最终结果 | completion.py 采集与 HTTP 结果
完成编排 | models.py 编排层结果模型
"""
