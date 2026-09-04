# -*- coding: utf-8 -*-
"""询价日志与评估工作簿的离线分析：按当前算法重建价格结论并导出 Excel。

负责：读取历史询价日志和评估工作簿中已有的 RPA 记录，调用
app.algorithm 重建价格峰与最终取值，输出分析 Excel；由
analyze-captured-data skill 的包装脚本调用，不在在线询价链路运行。
不负责：浏览器运行时、平台采集，也不写在线询价结果。

内部：export_operation_log_excel.py 日志导出 Excel 工作簿 |
presentation.py 工作簿展示映射
"""
