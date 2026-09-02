-- 存量库迁移：communities 增加住宅类型标识列。
-- 对已初始化的库执行一次即可；新库由 community_data_schema.sql 直接创建。
-- 幂等性说明：SQLite 的 ALTER TABLE 不支持 IF NOT EXISTS，
-- 重复执行会报 duplicate column name，可先查 PRAGMA table_info(communities)。
ALTER TABLE communities ADD COLUMN estate_type TEXT;
