-- 小区基础数据模块 SQLite schema。
--
-- community_groups 表表示不区分期数的小区组；communities 表表示具体的小区
-- 或期数记录。小区别名保存在 aliases_json 中，由 community 模块负责解析。

-- ============================================================================
-- 小区组：同一正式小区及其各期记录的归属容器
-- ============================================================================
CREATE TABLE IF NOT EXISTS community_groups (
    -- 小区组在数据库内的唯一编号。
    community_group_id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 城市，例如“深圳”。
    city TEXT NOT NULL,

    -- 行政区，例如“罗湖区”。
    administrative_district TEXT NOT NULL,

    -- 用于查询和去重的标准化小区组名称。
    normalized_name TEXT NOT NULL,

    -- 对外展示的小区组名称。
    display_name TEXT NOT NULL,

    -- 记录创建时间，使用 ISO-8601 文本保存。
    created_at TEXT NOT NULL,

    -- 记录最近更新时间，使用 ISO-8601 文本保存。
    updated_at TEXT NOT NULL,

    -- 同一城市、行政区和标准化名称只能有一个小区组。
    UNIQUE(city, administrative_district, normalized_name)
);

-- ============================================================================
-- 小区具体记录：正式名称、期数、别名、坐标和建成年份
-- ============================================================================
CREATE TABLE IF NOT EXISTS communities (
    -- 小区具体记录在数据库内的唯一编号，也是其它模块关联的小区 ID。
    community_id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 所属小区组 ID。
    community_group_id INTEGER NOT NULL,

    -- 城市，例如“深圳”。
    city TEXT NOT NULL,

    -- 行政区，例如“罗湖区”。
    administrative_district TEXT NOT NULL,

    -- 片区名称；它与行政区不是同一个概念，可以为空。
    district TEXT,

    -- 该条记录的正式小区名称，可带期数。
    name TEXT NOT NULL,

    -- 用于名称匹配和去重的标准化名称。
    normalized_name TEXT NOT NULL,

    -- 从正式名称中拆出的期数，例如“一期”，没有期数时为空。
    phase TEXT,

    -- 标准化期数键，用于区分同一小区的不同期数。
    phase_key TEXT NOT NULL,

    -- Excel rename 别名，JSON 数组格式保存。
    aliases_json TEXT NOT NULL DEFAULT '[]',

    -- 建成年份。
    build_year INTEGER,

    -- 住宅类型标识：住宅/公寓/城中村/非住宅/不可用；NULL 表示未核实。
    -- 对外两个小区查询接口默认只返回“住宅”。
    estate_type TEXT,

    -- 用于腾讯地图地理编码的地址。
    address TEXT NOT NULL,

    -- 腾讯地图返回的经度。
    longitude REAL,

    -- 腾讯地图返回的纬度。
    latitude REAL,

    -- 坐标系标识，当前使用 GCJ-02。
    coordinate_system TEXT,

    -- 地理编码状态，例如 PENDING、SUCCESS、FAILED。
    geocode_status TEXT NOT NULL DEFAULT 'PENDING',

    -- 地理编码结果等级。
    geocode_level INTEGER,

    -- 地理编码结果可靠性等级。
    geocode_reliability INTEGER,

    -- 地理编码失败时的错误信息。
    geocode_message TEXT,

    -- 备注信息。
    remark TEXT,

    -- 记录创建时间，使用 ISO-8601 文本保存。
    created_at TEXT NOT NULL,

    -- 记录最近更新时间，使用 ISO-8601 文本保存。
    updated_at TEXT NOT NULL,

    -- 小区组删除时保护关联记录，并确保 community_group_id 有效。
    FOREIGN KEY (community_group_id)
        REFERENCES community_groups(community_group_id),

    -- 同一城市、行政区、标准化名称和期数只能有一条记录。
    UNIQUE(city, administrative_district, normalized_name, phase_key)
);

-- 按城市和行政区查询小区。
CREATE INDEX IF NOT EXISTS idx_communities_city_district
    ON communities(city, administrative_district);

-- 按小区组展开各期记录。
CREATE INDEX IF NOT EXISTS idx_communities_group
    ON communities(community_group_id);

-- 按城市和坐标筛选附近小区。
CREATE INDEX IF NOT EXISTS idx_communities_coordinates
    ON communities(city, longitude, latitude);
