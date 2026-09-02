-- 房源记录模块 SQLite schema。
--
-- 本文件只保存外部平台展示的成交事实和挂牌当前状态，不保存本系统的
-- 估价结果、最终取值、挂牌均价顶替值或其它询价过程数据。
--
-- community_id 由 community 模块先解析和校验。本模块使用独立 SQLite
-- 数据库，因此不建立跨数据库外键；写入层必须拒绝不存在或不匹配的 ID。

-- ============================================================================
-- 成交记录
-- ============================================================================

CREATE TABLE IF NOT EXISTS deal_records (
    -- 成交记录在本模块内的唯一编号。
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- community 模块返回的正式小区记录 ID。
    community_id INTEGER NOT NULL,

    -- 冗余保存成交发生地，便于独立查询和导出；归属仍以 community_id 为准。
    city TEXT NOT NULL,
    administrative_district TEXT NOT NULL,

    -- 成交来源平台代码：lj=链家，fang=房天下。
    source_platform TEXT NOT NULL
        CHECK (source_platform IN ('lj', 'fang')),

    -- 来源平台页面展示的小区名称，用于追溯别名或期数名称。
    source_community_name TEXT NOT NULL,

    -- 标准化成交日期，格式为 YYYY-MM-DD。
    deal_date TEXT NOT NULL,

    -- 标准化建筑面积，单位为平方米。
    area_sqm REAL NOT NULL
        CHECK (area_sqm > 0),

    -- 标准化成交总价，单位为元；例如 370 万保存为 3700000。
    total_price_yuan REAL NOT NULL
        CHECK (total_price_yuan > 0),

    -- 标准化成交单价，单位为元/平方米。
    unit_price_yuan REAL NOT NULL
        CHECK (unit_price_yuan > 0),

    -- 同一来源、同一小区、五项成交事实完全一致时只保留一条。
    -- 链家和房天下的 source_platform 不同，因此相同成交仍各保留一条来源记录。
    UNIQUE (
        source_platform,
        community_id,
        deal_date,
        area_sqm,
        total_price_yuan,
        unit_price_yuan
    )
);

CREATE INDEX IF NOT EXISTS idx_deal_records_community
    ON deal_records (community_id, deal_date);

CREATE INDEX IF NOT EXISTS idx_deal_records_source_community
    ON deal_records (source_platform, community_id, deal_date);

-- ============================================================================
-- 小区平台页面入口
-- ============================================================================

CREATE TABLE IF NOT EXISTS community_platform_pages (
    -- 小区平台页面入口在本模块内的唯一编号。
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- community 模块返回的正式小区记录 ID。
    community_id INTEGER NOT NULL,

    -- 网页平台代码：ke=贝壳，ajk=安居客，fang=房天下，lj=链家，lyj=乐有家。
    -- 行舟深房为接口平台，不使用网页入口。
    source_platform TEXT NOT NULL
        CHECK (source_platform IN ('ke', 'ajk', 'fang', 'lj', 'lyj')),

    -- 来源平台页面展示的小区名称，用于直达页面后的归属校验与追溯。
    -- 旧挂牌入口迁移时可能缺失，下次真实采集后补齐。
    source_community_name TEXT,

    -- 小区级挂牌列表入口，用于下次直达并更新挂牌；不是单套房源详情地址。
    listing_page_url TEXT,

    -- 小区级成交列表入口，用于下次直达并更新成交；不是单条成交记录地址。
    deal_page_url TEXT,

    -- 同一小区在同一平台只保留一组入口，且至少有一个可用入口。
    UNIQUE (community_id, source_platform),
    CHECK (listing_page_url IS NOT NULL OR deal_page_url IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_community_platform_pages_community
    ON community_platform_pages (community_id);

-- 挂牌记录（当前状态）
-- ============================================================================

CREATE TABLE IF NOT EXISTS listing_records (
    -- 挂牌记录在本模块内的唯一编号。
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- community 模块返回的正式小区记录 ID。
    community_id INTEGER NOT NULL,

    -- 冗余保存挂牌所在地，便于独立查询和导出；归属仍以 community_id 为准。
    city TEXT NOT NULL,
    administrative_district TEXT NOT NULL,

    -- 挂牌来源平台代码：ke=贝壳，ajk=安居客，fang=房天下，
    -- lj=链家，lyj=乐有家。
    source_platform TEXT NOT NULL
        CHECK (source_platform IN ('ke', 'ajk', 'fang', 'lj', 'lyj')),

    -- 单套房源详情页地址；外部更新唯一依据与 source_platform 组合使用。
    listing_url TEXT NOT NULL,

    -- 来源平台页面展示的小区名称，用于归属校验和追溯。
    source_community_name TEXT NOT NULL,

    -- 来源平台展示的房源营销标题。
    title TEXT,

    -- 房型，例如 3室2厅；挂牌页面缺失时允许为空。
    layout TEXT,

    -- 标准化建筑面积，单位为平方米；页面缺失时允许为空。
    area_sqm REAL
        CHECK (area_sqm IS NULL OR area_sqm > 0),

    -- 标准化挂牌总价，单位为元；页面缺失时允许为空。
    total_price_yuan REAL
        CHECK (total_price_yuan IS NULL OR total_price_yuan > 0),

    -- 标准化挂牌单价，单位为元/平方米；页面缺失时允许为空。
    unit_price_yuan REAL
        CHECK (unit_price_yuan IS NULL OR unit_price_yuan > 0),

    -- 逻辑删除标记：0=当前有效，1=已判定下架；不物理删除记录。
    is_deleted INTEGER NOT NULL DEFAULT 0
        CHECK (is_deleted IN (0, 1)),

    -- 最近一次成功读取到该房源详情页的时间，使用 ISO-8601 文本保存。
    last_seen_at TEXT NOT NULL,

    -- 首次创建该挂牌记录的时间，使用 ISO-8601 文本保存。
    created_at TEXT NOT NULL,

    -- 最近一次更新挂牌字段或状态的时间，使用 ISO-8601 文本保存。
    updated_at TEXT NOT NULL,

    -- 同一平台同一详情地址重复采集时更新，不重复新增。
    UNIQUE (source_platform, listing_url)
);

CREATE INDEX IF NOT EXISTS idx_listing_records_community_active
    ON listing_records (community_id, is_deleted);

CREATE INDEX IF NOT EXISTS idx_listing_records_source_community
    ON listing_records (source_platform, community_id, is_deleted);

-- 挂牌价格日志（每次成功写入当前记录后的价格快照）
-- ============================================================================

CREATE TABLE IF NOT EXISTS listing_record_logs (
    -- 日志在本模块内的唯一编号。
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 对应 listing_records.id；同一套房源的日志通过该字段串联。
    listing_record_id INTEGER NOT NULL,

    -- 本次价格快照的采集时间，使用 ISO-8601 文本保存。
    observed_at TEXT NOT NULL,

    -- 本次采集后确认有效的挂牌总价，单位为元。
    total_price_yuan REAL
        CHECK (total_price_yuan IS NULL OR total_price_yuan > 0),

    -- 本次采集后确认有效的挂牌单价，单位为元/平方米。
    unit_price_yuan REAL
        CHECK (unit_price_yuan IS NULL OR unit_price_yuan > 0),

    -- 日志只保存价格快照，不保存标题、户型、面积或涨幅计算结果。
    FOREIGN KEY (listing_record_id)
        REFERENCES listing_records(id)
);

-- 按房源和采集时间读取价格变化。
CREATE INDEX IF NOT EXISTS idx_listing_record_logs_record_time
    ON listing_record_logs (listing_record_id, observed_at, id);
