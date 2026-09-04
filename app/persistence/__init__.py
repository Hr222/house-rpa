# -*- coding: utf-8 -*-
"""通用持久化基础设施：SQLite 连接与事务的生命周期管理。

只提供 sqlite_connection 等与业务无关的底层封装；业务库的表结构与
查询由各业务模块自持（如 app.community_data.database、
app.property_records.database），不在这里。
"""

from app.persistence.sqlite import sqlite_connection

__all__ = ["sqlite_connection"]
