# -*- coding: utf-8 -*-
"""房产询价 RPA 业务模块：浏览器运行时、平台状态机与多平台并行采集。

负责：nodriver/Chrome 生命周期、平台会话与状态机、进程内任务队列与
保活、风控恢复，把各平台并行采集结果汇总为原始 RPACollectionResult。
不负责：询价任务弱持久化与崩溃恢复（app.inquiry）、价格算法
（app.algorithm）、小区主数据（app.community_data）；平台 adapter
不直写业务数据库。

内部：runtime.py 服务运行时（浏览器/状态/队列）| service.py 平台
并行采集 | registry.py 平台注册表 | core/ 配置、采集模型与状态定义
| platforms/ 各平台适配（shell 薄壳 + collector + parser + 常量）
| utils/ 通用工具
"""
