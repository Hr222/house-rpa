-- 房源记录 完整备份（结构 + 全部数据 + AUTOINCREMENT 自增水位）。
--
-- 来源：persist/property_records.sqlite3 于 2026-09-02 导出，共 656 行业务数据。
-- 恢复方式：对"空库"执行本文件（语句不带 IF NOT EXISTS，等价 sqlite3 .dump）：
--   sqlite3 新库.db < 本文件   或   Python: connection.executescript(文本)
-- 数据核对：恢复后各表行数与内容应与生产库一致（导出时已做回验）。
BEGIN TRANSACTION;
CREATE TABLE community_platform_pages (
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
INSERT INTO "community_platform_pages" VALUES(1,2068,'lyj','蔚蓝海岸三期','https://shenzhen.leyoujia.com/esf?b=107',NULL);
INSERT INTO "community_platform_pages" VALUES(2,2150,'lyj','浪琴屿花园','https://shenzhen.leyoujia.com/esf?b=660',NULL);
INSERT INTO "community_platform_pages" VALUES(3,2215,'lyj','育德佳园一二期','https://shenzhen.leyoujia.com/esf?b=1349',NULL);
INSERT INTO "community_platform_pages" VALUES(4,2263,'lyj','瑞铧苑','https://shenzhen.leyoujia.com/esf?b=1784',NULL);
INSERT INTO "community_platform_pages" VALUES(5,2146,'lyj','海上世界双玺花园二期','https://shenzhen.leyoujia.com/esf?b=844559',NULL);
INSERT INTO "community_platform_pages" VALUES(6,2452,'lyj','水湾1979（4-7栋）','https://shenzhen.leyoujia.com/esf?b=37810',NULL);
INSERT INTO "community_platform_pages" VALUES(7,2466,'lyj','水湾1979（1-3栋）','https://shenzhen.leyoujia.com/esf?b=819030',NULL);
INSERT INTO "community_platform_pages" VALUES(8,2101,'lyj','万科蛇口公馆','https://shenzhen.leyoujia.com/esf?b=819413',NULL);
INSERT INTO "community_platform_pages" VALUES(9,2332,'lyj','纯海岸','https://shenzhen.leyoujia.com/esf?b=1199',NULL);
INSERT INTO "community_platform_pages" VALUES(10,2330,'lyj','滨福庭园','https://shenzhen.leyoujia.com/esf?b=15198',NULL);
INSERT INTO "community_platform_pages" VALUES(11,2381,'lyj','熙湾俊庭','https://shenzhen.leyoujia.com/esf?b=3976',NULL);
INSERT INTO "community_platform_pages" VALUES(12,5482,'lyj','前海天境花园','https://shenzhen.leyoujia.com/esf?b=850462',NULL);
INSERT INTO "community_platform_pages" VALUES(13,5667,'lyj','锦尚公馆','https://shenzhen.leyoujia.com/esf?b=888410',NULL);
INSERT INTO "community_platform_pages" VALUES(14,5526,'lyj','嵘玺家园','https://shenzhen.leyoujia.com/esf?b=859603',NULL);
INSERT INTO "community_platform_pages" VALUES(15,5525,'lyj','颐城栖湾里','https://shenzhen.leyoujia.com/esf?b=888954',NULL);
INSERT INTO "community_platform_pages" VALUES(16,2032,'lyj','大冲城市花园','https://shenzhen.leyoujia.com/esf?b=35640',NULL);
INSERT INTO "community_platform_pages" VALUES(17,2041,'lyj','华润城润府二期','https://shenzhen.leyoujia.com/esf?b=48200',NULL);
INSERT INTO "community_platform_pages" VALUES(18,2185,'lyj','红树别院','https://shenzhen.leyoujia.com/esf?b=37439',NULL);
INSERT INTO "community_platform_pages" VALUES(19,1995,'lyj','华润城润府一期','https://shenzhen.leyoujia.com/esf?b=47191',NULL);
INSERT INTO "community_platform_pages" VALUES(20,5114,'lyj','万科金域缇香二期','https://shenzhen.leyoujia.com/esf?b=834146',NULL);
INSERT INTO "community_platform_pages" VALUES(21,5106,'lyj','招商花园城一期','https://shenzhen.leyoujia.com/esf?b=39398',NULL);
INSERT INTO "community_platform_pages" VALUES(22,5121,'lyj','嘉宏湾花园二期','https://shenzhen.leyoujia.com/esf?b=834147',NULL);
INSERT INTO "community_platform_pages" VALUES(23,5650,'lyj','君成世界湾','https://shenzhen.leyoujia.com/esf?b=850250',NULL);
INSERT INTO "community_platform_pages" VALUES(24,5430,'lyj','兴围华府','https://shenzhen.leyoujia.com/esf?b=855494',NULL);
INSERT INTO "community_platform_pages" VALUES(25,5338,'lyj','海纳公馆','https://shenzhen.leyoujia.com/esf?b=858643',NULL);
INSERT INTO "community_platform_pages" VALUES(26,5919,'lyj','都市茗荟花园二期','https://shenzhen.leyoujia.com/esf?b=907866',NULL);
INSERT INTO "community_platform_pages" VALUES(27,5317,'lyj','中粮大悦城','https://shenzhen.leyoujia.com/esf?b=844759',NULL);
INSERT INTO "community_platform_pages" VALUES(28,5692,'lyj','大悦铂悦苑','https://shenzhen.leyoujia.com/esf?b=884726',NULL);
INSERT INTO "community_platform_pages" VALUES(29,5730,'lyj','珈誉时尚花园2期','https://shenzhen.leyoujia.com/esf?b=891355',NULL);
INSERT INTO "community_platform_pages" VALUES(30,5722,'lyj','万丰海岸城瀚府一期','https://shenzhen.leyoujia.com/esf?b=890038',NULL);
INSERT INTO "community_platform_pages" VALUES(31,3424,'lyj','华盛新沙荟名庭（三期）','https://shenzhen.leyoujia.com/esf?b=48845',NULL);
INSERT INTO "community_platform_pages" VALUES(32,3471,'lyj','万科翡御郡府','https://shenzhen.leyoujia.com/esf?b=40405',NULL);
INSERT INTO "community_platform_pages" VALUES(33,3397,'lyj','万科翡丽郡','https://shenzhen.leyoujia.com/esf?b=2113',NULL);
INSERT INTO "community_platform_pages" VALUES(34,3467,'lyj','万科翡逸郡园','https://shenzhen.leyoujia.com/esf?b=15840',NULL);
INSERT INTO "community_platform_pages" VALUES(35,3529,'lyj','联投东方华府(一期)','https://shenzhen.leyoujia.com/esf?b=37594',NULL);
INSERT INTO "community_platform_pages" VALUES(36,3189,'lyj','西城上筑','https://shenzhen.leyoujia.com/esf?b=91',NULL);
INSERT INTO "community_platform_pages" VALUES(37,3284,'lyj','风临洲','https://shenzhen.leyoujia.com/esf?b=682',NULL);
INSERT INTO "community_platform_pages" VALUES(38,3207,'lyj','菁英趣庭','https://shenzhen.leyoujia.com/esf?b=225',NULL);
INSERT INTO "community_platform_pages" VALUES(39,3280,'lyj','鸿荣源尚都','https://shenzhen.leyoujia.com/esf?b=48',NULL);
INSERT INTO "community_platform_pages" VALUES(40,3201,'lyj','中洲华府','https://shenzhen.leyoujia.com/esf?b=54049',NULL);
INSERT INTO "community_platform_pages" VALUES(41,3479,'lyj','宝湖居花园','https://shenzhen.leyoujia.com/esf?b=750',NULL);
INSERT INTO "community_platform_pages" VALUES(42,3384,'lyj','风采轩','https://shenzhen.leyoujia.com/esf?b=436',NULL);
INSERT INTO "community_platform_pages" VALUES(43,2913,'lyj','新世界倚山花园(三期)','https://shenzhen.leyoujia.com/esf?b=4338',NULL);
INSERT INTO "community_platform_pages" VALUES(44,2981,'lyj','上善梧桐苑','https://shenzhen.leyoujia.com/esf?b=48932',NULL);
INSERT INTO "community_platform_pages" VALUES(45,2931,'lyj','君临海域','https://shenzhen.leyoujia.com/esf?b=15396',NULL);
INSERT INTO "community_platform_pages" VALUES(46,2996,'lyj','蓝郡左岸','https://shenzhen.leyoujia.com/esf?b=1131',NULL);
INSERT INTO "community_platform_pages" VALUES(47,3011,'lyj','爱琴湾山庄','https://shenzhen.leyoujia.com/esf?b=15395',NULL);
INSERT INTO "community_platform_pages" VALUES(48,3045,'lyj','皇庭玺园','https://shenzhen.leyoujia.com/esf?b=9070',NULL);
INSERT INTO "community_platform_pages" VALUES(49,3084,'lyj','东部华庭','https://shenzhen.leyoujia.com/esf?b=15394',NULL);
INSERT INTO "community_platform_pages" VALUES(50,3033,'lyj','京基天涛轩','https://shenzhen.leyoujia.com/esf?b=46688',NULL);
INSERT INTO "community_platform_pages" VALUES(51,1345,'lyj','福源花园三期','https://shenzhen.leyoujia.com/esf?b=1302',NULL);
INSERT INTO "community_platform_pages" VALUES(52,1098,'lyj','朗庭豪园','https://shenzhen.leyoujia.com/esf?b=716',NULL);
INSERT INTO "community_platform_pages" VALUES(53,1333,'lyj','京隆苑','https://shenzhen.leyoujia.com/esf?b=656',NULL);
INSERT INTO "community_platform_pages" VALUES(54,1312,'lyj','中港城','https://shenzhen.leyoujia.com/esf?b=847',NULL);
INSERT INTO "community_platform_pages" VALUES(55,1181,'lyj','香蜜三村','https://shenzhen.leyoujia.com/esf?b=757',NULL);
INSERT INTO "community_platform_pages" VALUES(56,1586,'lyj','香荔新村','https://shenzhen.leyoujia.com/esf?b=2703',NULL);
INSERT INTO "community_platform_pages" VALUES(57,1498,'lyj','长景阁','https://shenzhen.leyoujia.com/esf?b=2468',NULL);
INSERT INTO "community_platform_pages" VALUES(58,1103,'lyj','香域中央花园','https://shenzhen.leyoujia.com/esf?b=221',NULL);
INSERT INTO "community_platform_pages" VALUES(59,1198,'lyj','香山美树苑','https://shenzhen.leyoujia.com/esf?b=387',NULL);
INSERT INTO "community_platform_pages" VALUES(60,1570,'lyj','香榭茗园','https://shenzhen.leyoujia.com/esf?b=2740',NULL);
INSERT INTO "community_platform_pages" VALUES(61,172,'lyj','绿景山庄','https://shenzhen.leyoujia.com/esf?b=468',NULL);
INSERT INTO "community_platform_pages" VALUES(62,275,'lyj','比华利山庄','https://shenzhen.leyoujia.com/esf?b=1357',NULL);
INSERT INTO "community_platform_pages" VALUES(63,314,'lyj','海关住宅','https://shenzhen.leyoujia.com/esf?b=35487',NULL);
INSERT INTO "community_platform_pages" VALUES(64,5840,'lyj','信荣汇大厦','https://shenzhen.leyoujia.com/esf?b=892867',NULL);
INSERT INTO "community_platform_pages" VALUES(65,335,'lyj','培峰苑','https://shenzhen.leyoujia.com/esf?b=42105',NULL);
INSERT INTO "community_platform_pages" VALUES(66,582,'lyj','景福花园(本体+二期同条目)','https://shenzhen.leyoujia.com/esf?b=900928',NULL);
INSERT INTO "community_platform_pages" VALUES(67,949,'lyj','景福花园(本体+二期同条目)','https://shenzhen.leyoujia.com/esf?b=900928',NULL);
INSERT INTO "community_platform_pages" VALUES(68,389,'lyj','莲塘D小区','https://shenzhen.leyoujia.com/esf?b=3002',NULL);
INSERT INTO "community_platform_pages" VALUES(69,491,'lyj','莲通楼','https://shenzhen.leyoujia.com/esf?b=3066',NULL);
INSERT INTO "community_platform_pages" VALUES(70,5863,'lyj','壹湾府','https://shenzhen.leyoujia.com/esf?b=890031',NULL);
INSERT INTO "community_platform_pages" VALUES(71,6018,'lyj','富基云珑府','https://shenzhen.leyoujia.com/esf?b=885767',NULL);
INSERT INTO "community_platform_pages" VALUES(72,5535,'lyj','珑悦理家园','https://shenzhen.leyoujia.com/esf?b=889408',NULL);
INSERT INTO "community_platform_pages" VALUES(73,4747,'lyj','苹果园','https://shenzhen.leyoujia.com/esf?b=139',NULL);
INSERT INTO "community_platform_pages" VALUES(74,4953,'lyj','风和日丽花园三期','https://shenzhen.leyoujia.com/esf?b=871',NULL);
INSERT INTO "community_platform_pages" VALUES(75,4841,'lyj','日出印象B区(二期)','https://shenzhen.leyoujia.com/esf?b=994',NULL);
INSERT INTO "community_platform_pages" VALUES(76,4789,'lyj','七里香榭花园','https://shenzhen.leyoujia.com/esf?b=66',NULL);
INSERT INTO "community_platform_pages" VALUES(77,4820,'lyj','幸福枫景','https://shenzhen.leyoujia.com/esf?b=624',NULL);
INSERT INTO "community_platform_pages" VALUES(78,4832,'lyj','世纪春城三期','https://shenzhen.leyoujia.com/esf?b=228',NULL);
INSERT INTO "community_platform_pages" VALUES(79,4509,'lyj','朗泓龙园大观二期','https://shenzhen.leyoujia.com/esf?b=48873',NULL);
INSERT INTO "community_platform_pages" VALUES(80,4190,'lyj','远洋新天地家园','https://shenzhen.leyoujia.com/esf?b=50548',NULL);
INSERT INTO "community_platform_pages" VALUES(81,4656,'lyj','远洋新天地水岸花园','https://shenzhen.leyoujia.com/esf?b=727141',NULL);
INSERT INTO "community_platform_pages" VALUES(82,5829,'lyj','金光华凤凰九里','https://shenzhen.leyoujia.com/esf?b=889822',NULL);
INSERT INTO "community_platform_pages" VALUES(83,5639,'lyj','融湖盛景花园','https://shenzhen.leyoujia.com/esf?b=885645',NULL);
INSERT INTO "community_platform_pages" VALUES(84,5870,'lyj','静安府','https://shenzhen.leyoujia.com/esf?b=888883',NULL);
INSERT INTO "community_platform_pages" VALUES(85,5739,'lyj','紫和嘉园','https://shenzhen.leyoujia.com/esf?b=889276',NULL);
INSERT INTO "community_platform_pages" VALUES(86,5629,'lyj','勤诚达誉府','https://shenzhen.leyoujia.com/esf?b=868799',NULL);
INSERT INTO "community_platform_pages" VALUES(87,6037,'lyj','龙湖青云阙轩','https://shenzhen.leyoujia.com/esf?b=889781',NULL);
INSERT INTO "community_platform_pages" VALUES(88,6069,'lyj','泰瑞府','https://shenzhen.leyoujia.com/esf?b=889940',NULL);
INSERT INTO "community_platform_pages" VALUES(89,3971,'lyj','绿景城市立方','https://shenzhen.leyoujia.com/esf?b=138',NULL);
INSERT INTO "community_platform_pages" VALUES(90,3951,'lyj','阳光天健城','https://shenzhen.leyoujia.com/esf?b=281',NULL);
INSERT INTO "community_platform_pages" VALUES(91,4078,'lyj','奥林华府','https://shenzhen.leyoujia.com/esf?b=217',NULL);
INSERT INTO "community_platform_pages" VALUES(92,4314,'lyj','园景花园','https://shenzhen.leyoujia.com/esf?b=1449',NULL);
INSERT INTO "community_platform_pages" VALUES(93,4627,'lyj','西湖新村','https://shenzhen.leyoujia.com/esf?b=10132',NULL);
INSERT INTO "community_platform_pages" VALUES(94,5648,'lyj','百合世纪广场','https://shenzhen.leyoujia.com/esf?b=890885',NULL);
INSERT INTO "community_platform_pages" VALUES(95,4160,'lyj','信义荔山御园D区','https://shenzhen.leyoujia.com/esf?b=44198',NULL);
INSERT INTO "community_platform_pages" VALUES(96,6046,'lyj','合正新悦启园','https://shenzhen.leyoujia.com/esf?b=926395',NULL);
INSERT INTO "community_platform_pages" VALUES(97,6043,'lyj','岗宏翰林汇','https://shenzhen.leyoujia.com/esf?b=891486',NULL);
INSERT INTO "community_platform_pages" VALUES(98,5808,'lyj','和健云谷','https://shenzhen.leyoujia.com/esf?b=832927',NULL);
INSERT INTO "community_platform_pages" VALUES(99,5969,'lyj','沙吓新二村','https://shenzhen.leyoujia.com/esf?b=766305',NULL);
INSERT INTO "community_platform_pages" VALUES(100,5756,'lyj','卓越和奕府二期','https://shenzhen.leyoujia.com/esf?b=870819',NULL);
INSERT INTO "community_platform_pages" VALUES(101,6067,'lyj','京基华樾','https://shenzhen.leyoujia.com/esf?b=892039',NULL);
INSERT INTO "community_platform_pages" VALUES(102,170,'lyj','松泉山庄','https://shenzhen.leyoujia.com/esf?b=556',NULL);
INSERT INTO "community_platform_pages" VALUES(103,1649,'lyj','惠祥苑','https://shenzhen.leyoujia.com/esf?b=8617',NULL);
INSERT INTO "community_platform_pages" VALUES(104,5817,'lyj','深业上林苑','https://shenzhen.leyoujia.com/esf?b=892736',NULL);
INSERT INTO "community_platform_pages" VALUES(105,3764,'lyj','前进公社二期','https://shenzhen.leyoujia.com/esf?b=14076',NULL);
INSERT INTO "community_platform_pages" VALUES(106,5724,'lyj','中粮悦章凤凰里','https://shenzhen.leyoujia.com/esf?b=889443',NULL);
INSERT INTO "community_platform_pages" VALUES(107,5670,'lyj','鸿荣源珈誉府','https://shenzhen.leyoujia.com/esf?b=927010',NULL);
INSERT INTO "community_platform_pages" VALUES(108,5564,'lyj','泰福名苑','https://shenzhen.leyoujia.com/esf?b=861860',NULL);
INSERT INTO "community_platform_pages" VALUES(109,5118,'lyj','招商花园城二期','https://shenzhen.leyoujia.com/esf?b=834148',NULL);
INSERT INTO "community_platform_pages" VALUES(110,5073,'lyj','根竹园花园','https://shenzhen.leyoujia.com/esf?b=37801',NULL);
INSERT INTO "community_platform_pages" VALUES(111,4800,'lyj','锦绣江南 一期','https://shenzhen.leyoujia.com/esf?b=8931',NULL);
INSERT INTO "community_platform_pages" VALUES(112,4831,'lyj','锦绣江南 二期','https://shenzhen.leyoujia.com/esf?b=223',NULL);
INSERT INTO "community_platform_pages" VALUES(113,4858,'lyj','锦绣江南 三期','https://shenzhen.leyoujia.com/esf?b=632',NULL);
INSERT INTO "community_platform_pages" VALUES(114,4774,'lyj','锦绣江南 四期','https://shenzhen.leyoujia.com/esf?b=104',NULL);
INSERT INTO "community_platform_pages" VALUES(115,1188,'lyj','港中旅花园 一期','https://shenzhen.leyoujia.com/esf?b=263',NULL);
INSERT INTO "community_platform_pages" VALUES(116,1113,'lyj','港中旅花园 二期','https://shenzhen.leyoujia.com/esf?b=630',NULL);
INSERT INTO "community_platform_pages" VALUES(117,2931,'fang','君临海域','https://sz.esf.fang.com/house-xm2810135374',NULL);
INSERT INTO "community_platform_pages" VALUES(118,314,'fang','海关草埔生活区','https://sz.esf.fang.com/house-xm2810211356',NULL);
INSERT INTO "community_platform_pages" VALUES(119,170,'fang','松泉山庄','https://sz.esf.fang.com/house-xm2810171511',NULL);
INSERT INTO "community_platform_pages" VALUES(120,6067,'fang','京基华樾','https://sz.esf.fang.com/house-xm2810209896',NULL);
INSERT INTO "community_platform_pages" VALUES(121,5840,'fang','中信·信荣汇','https://sz.esf.fang.com/house-xm2810205866',NULL);
INSERT INTO "community_platform_pages" VALUES(122,335,'fang','培峰苑','https://sz.esf.fang.com/house-xm2810135406',NULL);
INSERT INTO "community_platform_pages" VALUES(123,582,'fang','景福花园','https://sz.esf.fang.com/house-xm2810004353',NULL);
INSERT INTO "community_platform_pages" VALUES(124,949,'fang','景福花园','https://sz.esf.fang.com/house-xm2810004353',NULL);
INSERT INTO "community_platform_pages" VALUES(125,389,'fang','莲塘D小区','https://sz.esf.fang.com/house-xm2810395422',NULL);
INSERT INTO "community_platform_pages" VALUES(126,491,'fang','莲通楼','https://sz.esf.fang.com/house-xm2810646144',NULL);
INSERT INTO "community_platform_pages" VALUES(127,1345,'fang','福源花园三期','https://sz.esf.fang.com/house-xm2810717058',NULL);
INSERT INTO "community_platform_pages" VALUES(128,1098,'fang','朗庭豪园','https://sz.esf.fang.com/house-xm2810004617',NULL);
INSERT INTO "community_platform_pages" VALUES(129,1333,'fang','京隆苑','https://sz.esf.fang.com/house-xm2810171119',NULL);
INSERT INTO "community_platform_pages" VALUES(130,1312,'fang','中港城','https://sz.esf.fang.com/house-xm2810011632',NULL);
INSERT INTO "community_platform_pages" VALUES(131,1181,'fang','香蜜三村','https://sz.esf.fang.com/house-xm2810004921',NULL);
INSERT INTO "community_platform_pages" VALUES(132,1586,'fang','香荔新村','https://sz.esf.fang.com/house-xm2810266026',NULL);
INSERT INTO "community_platform_pages" VALUES(133,1649,'fang','惠翔苑','https://sz.esf.fang.com/house-xm2810264222',NULL);
INSERT INTO "community_platform_pages" VALUES(134,1113,'fang','港中旅花园(二期)','https://sz.esf.fang.com/house-xm2810076011',NULL);
INSERT INTO "community_platform_pages" VALUES(135,1103,'fang','香域中央花园','https://sz.esf.fang.com/house-xm2810648028',NULL);
INSERT INTO "community_platform_pages" VALUES(136,2263,'fang','瑞铧苑','https://sz.esf.fang.com/house-xm2810249944',NULL);
INSERT INTO "community_platform_pages" VALUES(137,2452,'fang','水湾1979一期','https://sz.esf.fang.com/house-xm2810858164',NULL);
INSERT INTO "community_platform_pages" VALUES(138,2466,'fang','水湾1979二期','https://sz.esf.fang.com/house-xm2810858164',NULL);
INSERT INTO "community_platform_pages" VALUES(139,2101,'fang','万科蛇口公馆','https://sz.esf.fang.com/house-xm2810135278',NULL);
INSERT INTO "community_platform_pages" VALUES(140,2330,'fang','滨福庭园','https://sz.esf.fang.com/house-xm2810078503',NULL);
INSERT INTO "community_platform_pages" VALUES(141,2381,'fang','熙湾','https://sz.esf.fang.com/house-xm2810070463',NULL);
INSERT INTO "community_platform_pages" VALUES(142,5667,'fang','锦尚公馆','https://sz.esf.fang.com/house-xm2810139338',NULL);
INSERT INTO "community_platform_pages" VALUES(143,5525,'fang','颐城栖湾里','https://sz.esf.fang.com/house-xm2810199766',NULL);
INSERT INTO "community_platform_pages" VALUES(144,1188,'fang','港中旅花园(一期)','https://sz.esf.fang.com/house-xm2810648630',NULL);
INSERT INTO "community_platform_pages" VALUES(173,2032,'fang','大冲城市花园','https://sz.esf.fang.com/house-xm2810135264',NULL);
INSERT INTO "community_platform_pages" VALUES(174,2041,'fang','华润城润府二期','https://sz.esf.fang.com/house-xm2810135506',NULL);
INSERT INTO "community_platform_pages" VALUES(175,2185,'fang','红树别院','https://sz.esf.fang.com/house-xm2811058924',NULL);
INSERT INTO "community_platform_pages" VALUES(176,5073,'lj','根竹园花园','https://sz.lianjia.com/ershoufang/c2411064064755',NULL);
INSERT INTO "community_platform_pages" VALUES(177,2068,'lj','蔚蓝海岸3期','https://sz.lianjia.com/ershoufang/c2411049010947',NULL);
INSERT INTO "community_platform_pages" VALUES(178,2150,'lj','浪琴屿花园','https://sz.lianjia.com/ershoufang/c2411049639314',NULL);
INSERT INTO "community_platform_pages" VALUES(179,2215,'lj','育德佳园','https://sz.lianjia.com/ershoufang/c2411063123851',NULL);
INSERT INTO "community_platform_pages" VALUES(180,2263,'lj','瑞铧苑','https://sz.lianjia.com/ershoufang/c2411049641297',NULL);
INSERT INTO "community_platform_pages" VALUES(181,2146,'lj','海上世界双玺','https://sz.lianjia.com/ershoufang/c246944473492728',NULL);
INSERT INTO "community_platform_pages" VALUES(182,2452,'lj','水湾1979一期','https://sz.lianjia.com/ershoufang/c248098604921410',NULL);
INSERT INTO "community_platform_pages" VALUES(183,2466,'lj','水湾1979二期','https://sz.lianjia.com/ershoufang/c2420027615902071',NULL);
INSERT INTO "community_platform_pages" VALUES(184,2101,'lj','万科蛇口公馆','https://sz.lianjia.com/ershoufang/c2420030661177318',NULL);
INSERT INTO "community_platform_pages" VALUES(185,2332,'lj','纯海岸','https://sz.lianjia.com/ershoufang/c2411048960865',NULL);
INSERT INTO "community_platform_pages" VALUES(186,2330,'lj','滨福庭园','https://sz.lianjia.com/ershoufang/c2411048959324',NULL);
INSERT INTO "community_platform_pages" VALUES(187,2381,'lj','熙湾','https://sz.lianjia.com/ershoufang/c2411048909565',NULL);
INSERT INTO "community_platform_pages" VALUES(188,5482,'lj','前海天境花园','https://sz.lianjia.com/ershoufang/c2420053970388249',NULL);
INSERT INTO "community_platform_pages" VALUES(189,5667,'lj','锦尚公馆','https://sz.lianjia.com/ershoufang/c24000000022089',NULL);
INSERT INTO "community_platform_pages" VALUES(190,5526,'lj','招商嵘玺家园','https://sz.lianjia.com/ershoufang/c24000000057491',NULL);
INSERT INTO "community_platform_pages" VALUES(191,5525,'lj','颐城栖湾里','https://sz.lianjia.com/ershoufang/c24000000027369',NULL);
INSERT INTO "community_platform_pages" VALUES(192,2032,'lj','大冲城市花园','https://sz.lianjia.com/ershoufang/c2411063168752',NULL);
INSERT INTO "community_platform_pages" VALUES(193,2041,'lj','华润城润府二期','https://sz.lianjia.com/ershoufang/c2418068184772309',NULL);
INSERT INTO "community_platform_pages" VALUES(194,2185,'lj','红树别院','https://sz.lianjia.com/ershoufang/c2411063256583',NULL);
INSERT INTO "community_platform_pages" VALUES(195,1995,'lj','华润城润府一期','https://sz.lianjia.com/ershoufang/c2411063262534',NULL);
INSERT INTO "community_platform_pages" VALUES(196,5114,'lj','万科金域缇香二期','https://sz.lianjia.com/ershoufang/c2411099549793',NULL);
INSERT INTO "community_platform_pages" VALUES(197,5118,'lj','坪山招商花园城北区','https://sz.lianjia.com/ershoufang/c2411063168002',NULL);
INSERT INTO "community_platform_pages" VALUES(198,5106,'lj','坪山招商花园城南区','https://sz.lianjia.com/ershoufang/c2416999780463265',NULL);
INSERT INTO "community_platform_pages" VALUES(199,5121,'lj','大东城二期（嘉宏湾花园二期）','https://sz.lianjia.com/ershoufang/c2411051909674',NULL);
INSERT INTO "community_platform_pages" VALUES(200,5650,'lj','君成世界湾','https://sz.lianjia.com/ershoufang/c24000000004273',NULL);
INSERT INTO "community_platform_pages" VALUES(201,5430,'lj','兴围华府','https://sz.lianjia.com/ershoufang/c24000000034922',NULL);
INSERT INTO "community_platform_pages" VALUES(202,5564,'lj','泰福名苑','https://sz.lianjia.com/ershoufang/c24000000024228',NULL);
INSERT INTO "community_platform_pages" VALUES(203,5338,'lj','新锦安海纳公馆','https://sz.lianjia.com/ershoufang/c2420037552969297',NULL);
INSERT INTO "community_platform_pages" VALUES(204,5919,'lj','都市茗荟花园二期','https://sz.lianjia.com/ershoufang/c24000000082205',NULL);
INSERT INTO "community_platform_pages" VALUES(205,5317,'lj','大悦城天玺壹号','https://sz.lianjia.com/ershoufang/c24000000019563',NULL);
INSERT INTO "community_platform_pages" VALUES(206,5692,'lj','大悦城三期','https://sz.lianjia.com/ershoufang/c24000000047187',NULL);
INSERT INTO "community_platform_pages" VALUES(207,5670,'lj','鸿荣源珈誉府','https://sz.lianjia.com/ershoufang/c24000000064094',NULL);
INSERT INTO "community_platform_pages" VALUES(208,5730,'lj','鸿荣源珈誉府2区','https://sz.lianjia.com/ershoufang/c24000000078205',NULL);
INSERT INTO "community_platform_pages" VALUES(209,5724,'lj','中粮悦章凤凰里','https://sz.lianjia.com/ershoufang/c24000000065483',NULL);
INSERT INTO "community_platform_pages" VALUES(210,5722,'lj','万丰海岸城瀚府一期','https://sz.lianjia.com/ershoufang/c24000000055989',NULL);
INSERT INTO "community_platform_pages" VALUES(211,3424,'lj','西荟城三期','https://sz.lianjia.com/ershoufang/c2417725566758059',NULL);
INSERT INTO "community_platform_pages" VALUES(212,3471,'lj','万科翡御郡府','https://sz.lianjia.com/ershoufang/c2420030737756743',NULL);
INSERT INTO "community_platform_pages" VALUES(213,3397,'lj','万科翡丽郡一期','https://sz.lianjia.com/ershoufang/c2411099661912',NULL);
INSERT INTO "community_platform_pages" VALUES(214,3467,'lj','万科翡丽郡四期','https://sz.lianjia.com/ershoufang/c2413975829646188',NULL);
INSERT INTO "community_platform_pages" VALUES(215,3529,'lj','联投东方华府(一期)','https://sz.lianjia.com/ershoufang/c2420023972634589',NULL);
INSERT INTO "community_platform_pages" VALUES(216,3764,'lj','前进公社二期','https://sz.lianjia.com/ershoufang/c2411053239061',NULL);
INSERT INTO "community_platform_pages" VALUES(217,3752,'lj','鸿盛花园','https://sz.lianjia.com/ershoufang/c2411053162109',NULL);
INSERT INTO "community_platform_pages" VALUES(218,3189,'lj','西城上筑','https://sz.lianjia.com/ershoufang/c2411053142705',NULL);
INSERT INTO "community_platform_pages" VALUES(219,3284,'lj','风临洲','https://sz.lianjia.com/ershoufang/c2411050199785',NULL);
INSERT INTO "community_platform_pages" VALUES(220,3207,'lj','菁英趣庭','https://sz.lianjia.com/ershoufang/c2411050552009',NULL);
INSERT INTO "community_platform_pages" VALUES(221,3280,'lj','尚都一期','https://sz.lianjia.com/ershoufang/c2411053076703',NULL);
INSERT INTO "community_platform_pages" VALUES(222,3201,'lj','中洲华府','https://sz.lianjia.com/ershoufang/c2411049238509',NULL);
INSERT INTO "community_platform_pages" VALUES(223,3651,'lj','35区住宅楼','https://sz.lianjia.com/ershoufang/c2411100250555',NULL);
INSERT INTO "community_platform_pages" VALUES(224,3479,'lj','宝湖居花园','https://sz.lianjia.com/ershoufang/c2411050346033',NULL);
INSERT INTO "community_platform_pages" VALUES(225,3384,'lj','风采轩','https://sz.lianjia.com/ershoufang/c2411049522508',NULL);
INSERT INTO "community_platform_pages" VALUES(226,2913,'lj','新世界倚山花园(三期)','https://sz.lianjia.com/ershoufang/c2411062669427',NULL);
INSERT INTO "community_platform_pages" VALUES(227,2981,'lj','上善梧桐苑','https://sz.lianjia.com/ershoufang/c2411100453063',NULL);
INSERT INTO "community_platform_pages" VALUES(228,2931,'lj','君临海域','https://sz.lianjia.com/ershoufang/c2411063027896',NULL);
INSERT INTO "community_platform_pages" VALUES(229,2996,'lj','蓝郡左岸','https://sz.lianjia.com/ershoufang/c2411062929971',NULL);
INSERT INTO "community_platform_pages" VALUES(230,3011,'lj','爱琴湾山庄','https://sz.lianjia.com/ershoufang/c2411062603204',NULL);
INSERT INTO "community_platform_pages" VALUES(231,3045,'lj','皇庭玺园','https://sz.lianjia.com/ershoufang/c2411062674885',NULL);
INSERT INTO "community_platform_pages" VALUES(232,3084,'lj','东部华庭','https://sz.lianjia.com/ershoufang/c2411062673701',NULL);
INSERT INTO "community_platform_pages" VALUES(233,3033,'lj','京基天涛轩','https://sz.lianjia.com/ershoufang/c246947705466739',NULL);
INSERT INTO "community_platform_pages" VALUES(234,5817,'lj','深业上林苑','https://sz.lianjia.com/ershoufang/c24000000063893',NULL);
INSERT INTO "community_platform_pages" VALUES(235,1345,'lj','福源花园三期','https://sz.lianjia.com/ershoufang/c2411048692986',NULL);
INSERT INTO "community_platform_pages" VALUES(236,1098,'lj','朗庭豪园','https://sz.lianjia.com/ershoufang/c2411063009748',NULL);
INSERT INTO "community_platform_pages" VALUES(237,1333,'lj','京隆苑','https://sz.lianjia.com/ershoufang/c2411048719279',NULL);
INSERT INTO "community_platform_pages" VALUES(238,1312,'lj','中港城','https://sz.lianjia.com/ershoufang/c2411048618421',NULL);
INSERT INTO "community_platform_pages" VALUES(239,1181,'lj','香蜜三村','https://sz.lianjia.com/ershoufang/c2411049467200',NULL);
INSERT INTO "community_platform_pages" VALUES(240,1586,'lj','香荔新村','https://sz.lianjia.com/ershoufang/c2411049515362',NULL);
INSERT INTO "community_platform_pages" VALUES(241,1498,'lj','长景阁','https://sz.lianjia.com/ershoufang/c2411049452500',NULL);
INSERT INTO "community_platform_pages" VALUES(242,1649,'lj','惠翔苑','https://sz.lianjia.com/ershoufang/c2411063457606',NULL);
INSERT INTO "community_platform_pages" VALUES(243,1103,'lj','香域中央花园','https://sz.lianjia.com/ershoufang/c2411049555741',NULL);
INSERT INTO "community_platform_pages" VALUES(244,1198,'lj','香山美树苑','https://sz.lianjia.com/ershoufang/c2411050074573',NULL);
INSERT INTO "community_platform_pages" VALUES(245,1570,'lj','香榭茗园','https://sz.lianjia.com/ershoufang/c2411049587432',NULL);
INSERT INTO "community_platform_pages" VALUES(246,172,'lj','绿景山庄','https://sz.lianjia.com/ershoufang/c2411048890244',NULL);
INSERT INTO "community_platform_pages" VALUES(247,275,'lj','比华利山庄','https://sz.lianjia.com/ershoufang/c2411099432230',NULL);
INSERT INTO "community_platform_pages" VALUES(248,314,'lj','海关草埔生活区','https://sz.lianjia.com/ershoufang/c246877270257037',NULL);
INSERT INTO "community_platform_pages" VALUES(249,170,'lj','松泉山庄','https://sz.lianjia.com/ershoufang/c2411048745644',NULL);
INSERT INTO "community_platform_pages" VALUES(250,6067,'lj','京基华樾','https://sz.lianjia.com/ershoufang/c24000000083203',NULL);
INSERT INTO "community_platform_pages" VALUES(251,5840,'lj','中信·信荣汇','https://sz.lianjia.com/ershoufang/c24000000055993',NULL);
INSERT INTO "community_platform_pages" VALUES(252,335,'lj','培峰苑','https://sz.lianjia.com/ershoufang/c2420029864981686',NULL);
INSERT INTO "community_platform_pages" VALUES(253,582,'lj','景福花园','https://sz.lianjia.com/ershoufang/c2411049296803',NULL);
INSERT INTO "community_platform_pages" VALUES(254,949,'lj','景福花园','https://sz.lianjia.com/ershoufang/c2411049296803',NULL);
INSERT INTO "community_platform_pages" VALUES(255,799,'lj','莲塘路200号','https://sz.lianjia.com/ershoufang/c2420030448206774',NULL);
INSERT INTO "community_platform_pages" VALUES(256,389,'lj','莲塘D小区','https://sz.lianjia.com/ershoufang/c2411099906129',NULL);
INSERT INTO "community_platform_pages" VALUES(257,491,'lj','莲通楼','https://sz.lianjia.com/ershoufang/c2411049485493',NULL);
INSERT INTO "community_platform_pages" VALUES(258,5863,'lj','壹湾府','https://sz.lianjia.com/ershoufang/c24000000065482',NULL);
INSERT INTO "community_platform_pages" VALUES(259,5756,'lj','卓越和奕府二期','https://sz.lianjia.com/ershoufang/c24000000139338',NULL);
INSERT INTO "community_platform_pages" VALUES(260,6018,'lj','富基云珑府','https://sz.lianjia.com/ershoufang/c24000000052093',NULL);
INSERT INTO "community_platform_pages" VALUES(261,5535,'lj','珑悦理家园','https://sz.lianjia.com/ershoufang/c24000000055984',NULL);
INSERT INTO "community_platform_pages" VALUES(262,4747,'lj','苹果园','https://sz.lianjia.com/ershoufang/c2411050401115',NULL);
INSERT INTO "community_platform_pages" VALUES(263,4953,'lj','风和日丽三期','https://sz.lianjia.com/ershoufang/c2420057917563541',NULL);
INSERT INTO "community_platform_pages" VALUES(264,4841,'lj','日出印象二期','https://sz.lianjia.com/ershoufang/c2411049857052',NULL);
INSERT INTO "community_platform_pages" VALUES(265,4789,'lj','城投七里香榭','https://sz.lianjia.com/ershoufang/c2411049066468',NULL);
INSERT INTO "community_platform_pages" VALUES(266,4820,'lj','幸福枫景','https://sz.lianjia.com/ershoufang/c2411049073210',NULL);
INSERT INTO "community_platform_pages" VALUES(267,4832,'lj','世纪春城三期','https://sz.lianjia.com/ershoufang/c2411048954387',NULL);
INSERT INTO "community_platform_pages" VALUES(268,4509,'lj','朗泓龙园大观二期','https://sz.lianjia.com/ershoufang/c2420034661094314',NULL);
INSERT INTO "community_platform_pages" VALUES(269,5808,'lj','和健云谷','https://sz.lianjia.com/ershoufang/c24000000002273',NULL);
INSERT INTO "community_platform_pages" VALUES(270,4190,'lj','远洋新天地','https://sz.lianjia.com/ershoufang/c2414151184582770',NULL);
INSERT INTO "community_platform_pages" VALUES(271,4656,'lj','远洋新天地水岸花园','https://sz.lianjia.com/ershoufang/c2420068798061473',NULL);
INSERT INTO "community_platform_pages" VALUES(272,6043,'lj','岗宏翰林汇','https://sz.lianjia.com/ershoufang/c24000000057980',NULL);
INSERT INTO "community_platform_pages" VALUES(273,5829,'lj','金光华凤凰九里','https://sz.lianjia.com/ershoufang/c24000000068294',NULL);
INSERT INTO "community_platform_pages" VALUES(274,5639,'lj','融湖盛景花园','https://sz.lianjia.com/ershoufang/c24000000052092',NULL);
INSERT INTO "community_platform_pages" VALUES(275,5870,'lj','静安府','https://sz.lianjia.com/ershoufang/c24000000055979',NULL);
INSERT INTO "community_platform_pages" VALUES(276,5739,'lj','紫和嘉园','https://sz.lianjia.com/ershoufang/c24000000057176',NULL);
INSERT INTO "community_platform_pages" VALUES(277,5629,'lj','勤诚达誉府','https://sz.lianjia.com/ershoufang/c24000000028680',NULL);
INSERT INTO "community_platform_pages" VALUES(278,6037,'lj','龙湖星玺青云阙','https://sz.lianjia.com/ershoufang/c24000000065589',NULL);
INSERT INTO "community_platform_pages" VALUES(279,6069,'lj','泰瑞府','https://sz.lianjia.com/ershoufang/c24000000057977',NULL);
INSERT INTO "community_platform_pages" VALUES(280,3971,'lj','绿景大公馆','https://sz.lianjia.com/ershoufang/c2411051684876',NULL);
INSERT INTO "community_platform_pages" VALUES(281,3951,'lj','阳光天健城','https://sz.lianjia.com/ershoufang/c2411051697199',NULL);
INSERT INTO "community_platform_pages" VALUES(282,4078,'lj','奥林华府','https://sz.lianjia.com/ershoufang/c2411051681122',NULL);
INSERT INTO "community_platform_pages" VALUES(283,4314,'lj','园景花园','https://sz.lianjia.com/ershoufang/c248938133401323',NULL);
INSERT INTO "community_platform_pages" VALUES(284,4627,'lj','西湖新村','https://sz.lianjia.com/ershoufang/c2411051126572',NULL);
INSERT INTO "community_platform_pages" VALUES(285,5648,'lj','百合世纪广场','https://sz.lianjia.com/ershoufang/c24000000028579',NULL);
INSERT INTO "community_platform_pages" VALUES(286,1188,'lj','中旅国际公馆1期','https://sz.lianjia.com/ershoufang/c2411099687515',NULL);
INSERT INTO "community_platform_pages" VALUES(287,1113,'lj','中旅国际公馆2期','https://sz.lianjia.com/ershoufang/c2411049588802',NULL);
INSERT INTO "community_platform_pages" VALUES(288,4800,'lj','锦绣江南一期','https://sz.lianjia.com/ershoufang/c2411050534024',NULL);
INSERT INTO "community_platform_pages" VALUES(289,4831,'lj','锦绣江南二期','https://sz.lianjia.com/ershoufang/c2414573209923668',NULL);
INSERT INTO "community_platform_pages" VALUES(290,4858,'lj','锦绣江南三期','https://sz.lianjia.com/ershoufang/c2410495969997229',NULL);
INSERT INTO "community_platform_pages" VALUES(291,4774,'lj','锦绣江南四期','https://sz.lianjia.com/ershoufang/c2411050534778',NULL);
INSERT INTO "community_platform_pages" VALUES(292,6046,'lj','合正新悦启园','https://sz.lianjia.com/ershoufang/c24000000027672',NULL);
INSERT INTO "community_platform_pages" VALUES(293,4160,'lj','信义金御半山一期','https://sz.lianjia.com/ershoufang/c2411062844829',NULL);
INSERT INTO "community_platform_pages" VALUES(294,5073,'ke','根竹园花园','https://sz.ke.com/ershoufang/c2411064064755',NULL);
INSERT INTO "community_platform_pages" VALUES(295,2068,'ke','蔚蓝海岸3期','https://sz.ke.com/ershoufang/c2411049010947',NULL);
INSERT INTO "community_platform_pages" VALUES(296,2150,'ke','浪琴屿花园','https://sz.ke.com/ershoufang/c2411049639314',NULL);
INSERT INTO "community_platform_pages" VALUES(297,2215,'ke','育德佳园','https://sz.ke.com/ershoufang/c2411063123851',NULL);
INSERT INTO "community_platform_pages" VALUES(298,2263,'ke','瑞铧苑','https://sz.ke.com/ershoufang/c2411049641297',NULL);
INSERT INTO "community_platform_pages" VALUES(299,2146,'ke','海上世界双玺','https://sz.ke.com/ershoufang/c246944473492728',NULL);
INSERT INTO "community_platform_pages" VALUES(300,2452,'ke','水湾1979一期','https://sz.ke.com/ershoufang/c248098604921410',NULL);
INSERT INTO "community_platform_pages" VALUES(301,2466,'ke','水湾1979二期','https://sz.ke.com/ershoufang/c2420027615902071',NULL);
INSERT INTO "community_platform_pages" VALUES(302,2101,'ke','万科蛇口公馆','https://sz.ke.com/ershoufang/c2420030661177318',NULL);
INSERT INTO "community_platform_pages" VALUES(303,2332,'ke','纯海岸','https://sz.ke.com/ershoufang/c2411048960865',NULL);
INSERT INTO "community_platform_pages" VALUES(304,2330,'ke','滨福庭园','https://sz.ke.com/ershoufang/c2411048959324',NULL);
INSERT INTO "community_platform_pages" VALUES(305,2381,'ke','熙湾','https://sz.ke.com/ershoufang/c2411048909565',NULL);
INSERT INTO "community_platform_pages" VALUES(306,5482,'ke','前海天境花园','https://sz.ke.com/ershoufang/c2420053970388249',NULL);
INSERT INTO "community_platform_pages" VALUES(307,5667,'ke','锦尚公馆','https://sz.ke.com/ershoufang/c24000000022089',NULL);
INSERT INTO "community_platform_pages" VALUES(308,5526,'ke','招商嵘玺家园','https://sz.ke.com/ershoufang/c24000000057491',NULL);
INSERT INTO "community_platform_pages" VALUES(309,5525,'ke','颐城栖湾里','https://sz.ke.com/ershoufang/c24000000027369',NULL);
INSERT INTO "community_platform_pages" VALUES(310,2032,'ke','大冲城市花园','https://sz.ke.com/ershoufang/c2411063168752',NULL);
INSERT INTO "community_platform_pages" VALUES(311,2041,'ke','华润城润府二期','https://sz.ke.com/ershoufang/c2418068184772309',NULL);
INSERT INTO "community_platform_pages" VALUES(312,2185,'ke','红树别院','https://sz.ke.com/ershoufang/c2411063256583',NULL);
INSERT INTO "community_platform_pages" VALUES(313,1995,'ke','华润城润府一期','https://sz.ke.com/ershoufang/c2411063262534',NULL);
INSERT INTO "community_platform_pages" VALUES(314,5114,'ke','万科金域缇香二期','https://sz.ke.com/ershoufang/c2411099549793',NULL);
INSERT INTO "community_platform_pages" VALUES(315,5118,'ke','坪山招商花园城北区','https://sz.ke.com/ershoufang/c2411063168002',NULL);
INSERT INTO "community_platform_pages" VALUES(316,5106,'ke','坪山招商花园城南区','https://sz.ke.com/ershoufang/c2416999780463265',NULL);
INSERT INTO "community_platform_pages" VALUES(317,5121,'ke','大东城二期（嘉宏湾花园二期）','https://sz.ke.com/ershoufang/c2411051909674',NULL);
INSERT INTO "community_platform_pages" VALUES(318,5650,'ke','君成世界湾','https://sz.ke.com/ershoufang/c24000000004273',NULL);
INSERT INTO "community_platform_pages" VALUES(319,5430,'ke','兴围华府','https://sz.ke.com/ershoufang/c24000000034922',NULL);
INSERT INTO "community_platform_pages" VALUES(320,5564,'ke','泰福名苑','https://sz.ke.com/ershoufang/c24000000024228',NULL);
INSERT INTO "community_platform_pages" VALUES(321,5338,'ke','新锦安海纳公馆','https://sz.ke.com/ershoufang/c2420037552969297',NULL);
INSERT INTO "community_platform_pages" VALUES(322,5919,'ke','都市茗荟花园二期','https://sz.ke.com/ershoufang/c24000000082205',NULL);
INSERT INTO "community_platform_pages" VALUES(323,5317,'ke','大悦城天玺壹号','https://sz.ke.com/ershoufang/c24000000019563',NULL);
INSERT INTO "community_platform_pages" VALUES(324,5692,'ke','大悦城三期','https://sz.ke.com/ershoufang/c24000000047187',NULL);
INSERT INTO "community_platform_pages" VALUES(325,5670,'ke','鸿荣源珈誉府','https://sz.ke.com/ershoufang/c24000000064094',NULL);
INSERT INTO "community_platform_pages" VALUES(326,5730,'ke','鸿荣源珈誉府2区','https://sz.ke.com/ershoufang/c24000000078205',NULL);
INSERT INTO "community_platform_pages" VALUES(327,5724,'ke','中粮悦章凤凰里','https://sz.ke.com/ershoufang/c24000000065483',NULL);
INSERT INTO "community_platform_pages" VALUES(328,5722,'ke','万丰海岸城瀚府一期','https://sz.ke.com/ershoufang/c24000000055989',NULL);
INSERT INTO "community_platform_pages" VALUES(329,3424,'ke','西荟城三期','https://sz.ke.com/ershoufang/c2417725566758059',NULL);
INSERT INTO "community_platform_pages" VALUES(330,3471,'ke','万科翡御郡府','https://sz.ke.com/ershoufang/c2420030737756743',NULL);
INSERT INTO "community_platform_pages" VALUES(331,3397,'ke','万科翡丽郡一期','https://sz.ke.com/ershoufang/c2411099661912',NULL);
INSERT INTO "community_platform_pages" VALUES(332,3467,'ke','万科翡丽郡四期','https://sz.ke.com/ershoufang/c2413975829646188',NULL);
INSERT INTO "community_platform_pages" VALUES(333,3529,'ke','联投东方华府(一期)','https://sz.ke.com/ershoufang/c2420023972634589',NULL);
INSERT INTO "community_platform_pages" VALUES(334,3764,'ke','前进公社二期','https://sz.ke.com/ershoufang/c2411053239061',NULL);
INSERT INTO "community_platform_pages" VALUES(335,3752,'ke','鸿盛花园','https://sz.ke.com/ershoufang/c2411053162109',NULL);
INSERT INTO "community_platform_pages" VALUES(336,3189,'ke','西城上筑','https://sz.ke.com/ershoufang/c2411053142705',NULL);
INSERT INTO "community_platform_pages" VALUES(337,3284,'ke','风临洲','https://sz.ke.com/ershoufang/c2411050199785',NULL);
INSERT INTO "community_platform_pages" VALUES(338,3207,'ke','菁英趣庭','https://sz.ke.com/ershoufang/c2411050552009',NULL);
INSERT INTO "community_platform_pages" VALUES(339,3280,'ke','尚都一期','https://sz.ke.com/ershoufang/c2411053076703',NULL);
INSERT INTO "community_platform_pages" VALUES(340,3201,'ke','中洲华府','https://sz.ke.com/ershoufang/c2411049238509',NULL);
INSERT INTO "community_platform_pages" VALUES(341,3651,'ke','35区住宅楼','https://sz.ke.com/ershoufang/c2411100250555',NULL);
INSERT INTO "community_platform_pages" VALUES(342,3479,'ke','宝湖居花园','https://sz.ke.com/ershoufang/c2411050346033',NULL);
INSERT INTO "community_platform_pages" VALUES(343,3384,'ke','风采轩','https://sz.ke.com/ershoufang/c2411049522508',NULL);
INSERT INTO "community_platform_pages" VALUES(344,2913,'ke','新世界倚山花园(三期)','https://sz.ke.com/ershoufang/c2411062669427',NULL);
INSERT INTO "community_platform_pages" VALUES(345,2981,'ke','上善梧桐苑','https://sz.ke.com/ershoufang/c2411100453063',NULL);
INSERT INTO "community_platform_pages" VALUES(346,2931,'ke','君临海域','https://sz.ke.com/ershoufang/c2411063027896',NULL);
INSERT INTO "community_platform_pages" VALUES(347,2996,'ke','蓝郡左岸','https://sz.ke.com/ershoufang/c2411062929971',NULL);
INSERT INTO "community_platform_pages" VALUES(348,3011,'ke','爱琴湾山庄','https://sz.ke.com/ershoufang/c2411062603204',NULL);
INSERT INTO "community_platform_pages" VALUES(349,3045,'ke','皇庭玺园','https://sz.ke.com/ershoufang/c2411062674885',NULL);
INSERT INTO "community_platform_pages" VALUES(350,3084,'ke','东部华庭','https://sz.ke.com/ershoufang/c2411062673701',NULL);
INSERT INTO "community_platform_pages" VALUES(351,3033,'ke','京基天涛轩','https://sz.ke.com/ershoufang/c246947705466739',NULL);
INSERT INTO "community_platform_pages" VALUES(352,5817,'ke','深业上林苑','https://sz.ke.com/ershoufang/c24000000063893',NULL);
INSERT INTO "community_platform_pages" VALUES(353,1345,'ke','福源花园三期','https://sz.ke.com/ershoufang/c2411048692986',NULL);
INSERT INTO "community_platform_pages" VALUES(354,1098,'ke','朗庭豪园','https://sz.ke.com/ershoufang/c2411063009748',NULL);
INSERT INTO "community_platform_pages" VALUES(355,1333,'ke','京隆苑','https://sz.ke.com/ershoufang/c2411048719279',NULL);
INSERT INTO "community_platform_pages" VALUES(356,1312,'ke','中港城','https://sz.ke.com/ershoufang/c2411048618421',NULL);
INSERT INTO "community_platform_pages" VALUES(357,1181,'ke','香蜜三村','https://sz.ke.com/ershoufang/c2411049467200',NULL);
INSERT INTO "community_platform_pages" VALUES(358,1586,'ke','香荔新村','https://sz.ke.com/ershoufang/c2411049515362',NULL);
INSERT INTO "community_platform_pages" VALUES(359,1498,'ke','长景阁','https://sz.ke.com/ershoufang/c2411049452500',NULL);
INSERT INTO "community_platform_pages" VALUES(360,1649,'ke','惠翔苑','https://sz.ke.com/ershoufang/c2411063457606',NULL);
INSERT INTO "community_platform_pages" VALUES(361,1103,'ke','香域中央花园','https://sz.ke.com/ershoufang/c2411049555741',NULL);
INSERT INTO "community_platform_pages" VALUES(362,1198,'ke','香山美树苑','https://sz.ke.com/ershoufang/c2411050074573',NULL);
INSERT INTO "community_platform_pages" VALUES(363,1570,'ke','香榭茗园','https://sz.ke.com/ershoufang/c2411049587432',NULL);
INSERT INTO "community_platform_pages" VALUES(364,172,'ke','绿景山庄','https://sz.ke.com/ershoufang/c2411048890244',NULL);
INSERT INTO "community_platform_pages" VALUES(365,275,'ke','比华利山庄','https://sz.ke.com/ershoufang/c2411099432230',NULL);
INSERT INTO "community_platform_pages" VALUES(366,314,'ke','海关草埔生活区','https://sz.ke.com/ershoufang/c246877270257037',NULL);
INSERT INTO "community_platform_pages" VALUES(367,170,'ke','松泉山庄','https://sz.ke.com/ershoufang/c2411048745644',NULL);
INSERT INTO "community_platform_pages" VALUES(368,6067,'ke','京基华樾','https://sz.ke.com/ershoufang/c24000000083203',NULL);
INSERT INTO "community_platform_pages" VALUES(369,5840,'ke','中信·信荣汇','https://sz.ke.com/ershoufang/c24000000055993',NULL);
INSERT INTO "community_platform_pages" VALUES(370,335,'ke','培峰苑','https://sz.ke.com/ershoufang/c2420029864981686',NULL);
INSERT INTO "community_platform_pages" VALUES(371,582,'ke','景福花园','https://sz.ke.com/ershoufang/c2411049296803',NULL);
INSERT INTO "community_platform_pages" VALUES(372,949,'ke','景福花园','https://sz.ke.com/ershoufang/c2411049296803',NULL);
INSERT INTO "community_platform_pages" VALUES(373,799,'ke','莲塘路200号','https://sz.ke.com/ershoufang/c2420030448206774',NULL);
INSERT INTO "community_platform_pages" VALUES(374,389,'ke','莲塘D小区','https://sz.ke.com/ershoufang/c2411099906129',NULL);
INSERT INTO "community_platform_pages" VALUES(375,491,'ke','莲通楼','https://sz.ke.com/ershoufang/c2411049485493',NULL);
INSERT INTO "community_platform_pages" VALUES(376,5863,'ke','壹湾府','https://sz.ke.com/ershoufang/c24000000065482',NULL);
INSERT INTO "community_platform_pages" VALUES(377,5756,'ke','卓越和奕府二期','https://sz.ke.com/ershoufang/c24000000139338',NULL);
INSERT INTO "community_platform_pages" VALUES(378,6018,'ke','富基云珑府','https://sz.ke.com/ershoufang/c24000000052093',NULL);
INSERT INTO "community_platform_pages" VALUES(379,5535,'ke','珑悦理家园','https://sz.ke.com/ershoufang/c24000000055984',NULL);
INSERT INTO "community_platform_pages" VALUES(380,4747,'ke','苹果园','https://sz.ke.com/ershoufang/c2411050401115',NULL);
INSERT INTO "community_platform_pages" VALUES(381,4953,'ke','风和日丽三期','https://sz.ke.com/ershoufang/c2420057917563541',NULL);
INSERT INTO "community_platform_pages" VALUES(382,4841,'ke','日出印象二期','https://sz.ke.com/ershoufang/c2411049857052',NULL);
INSERT INTO "community_platform_pages" VALUES(383,4789,'ke','城投七里香榭','https://sz.ke.com/ershoufang/c2411049066468',NULL);
INSERT INTO "community_platform_pages" VALUES(384,4820,'ke','幸福枫景','https://sz.ke.com/ershoufang/c2411049073210',NULL);
INSERT INTO "community_platform_pages" VALUES(385,4832,'ke','世纪春城三期','https://sz.ke.com/ershoufang/c2411048954387',NULL);
INSERT INTO "community_platform_pages" VALUES(386,4509,'ke','朗泓龙园大观二期','https://sz.ke.com/ershoufang/c2420034661094314',NULL);
INSERT INTO "community_platform_pages" VALUES(387,5808,'ke','和健云谷','https://sz.ke.com/ershoufang/c24000000002273',NULL);
INSERT INTO "community_platform_pages" VALUES(388,4190,'ke','远洋新天地','https://sz.ke.com/ershoufang/c2414151184582770',NULL);
INSERT INTO "community_platform_pages" VALUES(389,4656,'ke','远洋新天地水岸花园','https://sz.ke.com/ershoufang/c2420068798061473',NULL);
INSERT INTO "community_platform_pages" VALUES(390,6043,'ke','岗宏翰林汇','https://sz.ke.com/ershoufang/c24000000057980',NULL);
INSERT INTO "community_platform_pages" VALUES(391,5829,'ke','金光华凤凰九里','https://sz.ke.com/ershoufang/c24000000068294',NULL);
INSERT INTO "community_platform_pages" VALUES(392,5639,'ke','融湖盛景花园','https://sz.ke.com/ershoufang/c24000000052092',NULL);
INSERT INTO "community_platform_pages" VALUES(393,5870,'ke','静安府','https://sz.ke.com/ershoufang/c24000000055979',NULL);
INSERT INTO "community_platform_pages" VALUES(394,5739,'ke','紫和嘉园','https://sz.ke.com/ershoufang/c24000000057176',NULL);
INSERT INTO "community_platform_pages" VALUES(395,5629,'ke','勤诚达誉府','https://sz.ke.com/ershoufang/c24000000028680',NULL);
INSERT INTO "community_platform_pages" VALUES(396,6037,'ke','龙湖星玺青云阙','https://sz.ke.com/ershoufang/c24000000065589',NULL);
INSERT INTO "community_platform_pages" VALUES(397,6069,'ke','泰瑞府','https://sz.ke.com/ershoufang/c24000000057977',NULL);
INSERT INTO "community_platform_pages" VALUES(398,3971,'ke','绿景大公馆','https://sz.ke.com/ershoufang/c2411051684876',NULL);
INSERT INTO "community_platform_pages" VALUES(399,3951,'ke','阳光天健城','https://sz.ke.com/ershoufang/c2411051697199',NULL);
INSERT INTO "community_platform_pages" VALUES(400,4078,'ke','奥林华府','https://sz.ke.com/ershoufang/c2411051681122',NULL);
INSERT INTO "community_platform_pages" VALUES(401,4314,'ke','园景花园','https://sz.ke.com/ershoufang/c248938133401323',NULL);
INSERT INTO "community_platform_pages" VALUES(402,4627,'ke','西湖新村','https://sz.ke.com/ershoufang/c2411051126572',NULL);
INSERT INTO "community_platform_pages" VALUES(403,5648,'ke','百合世纪广场','https://sz.ke.com/ershoufang/c24000000028579',NULL);
INSERT INTO "community_platform_pages" VALUES(404,1188,'ke','中旅国际公馆1期','https://sz.ke.com/ershoufang/c2411099687515',NULL);
INSERT INTO "community_platform_pages" VALUES(405,1113,'ke','中旅国际公馆2期','https://sz.ke.com/ershoufang/c2411049588802',NULL);
INSERT INTO "community_platform_pages" VALUES(406,4800,'ke','锦绣江南一期','https://sz.ke.com/ershoufang/c2411050534024',NULL);
INSERT INTO "community_platform_pages" VALUES(407,4831,'ke','锦绣江南二期','https://sz.ke.com/ershoufang/c2414573209923668',NULL);
INSERT INTO "community_platform_pages" VALUES(408,4858,'ke','锦绣江南三期','https://sz.ke.com/ershoufang/c2410495969997229',NULL);
INSERT INTO "community_platform_pages" VALUES(409,4774,'ke','锦绣江南四期','https://sz.ke.com/ershoufang/c2411050534778',NULL);
INSERT INTO "community_platform_pages" VALUES(410,6046,'ke','合正新悦启园','https://sz.ke.com/ershoufang/c24000000027672',NULL);
INSERT INTO "community_platform_pages" VALUES(411,4160,'ke','信义金御半山一期','https://sz.ke.com/ershoufang/c2411062844829',NULL);
INSERT INTO "community_platform_pages" VALUES(412,1995,'fang','华润城润府一期','https://sz.esf.fang.com/house-xm2810135074',NULL);
INSERT INTO "community_platform_pages" VALUES(413,5650,'fang','君成世界湾','https://sz.esf.fang.com/house-xm2810138134',NULL);
INSERT INTO "community_platform_pages" VALUES(414,5430,'fang','兴围华府','https://sz.esf.fang.com/house-xm2810138544',NULL);
INSERT INTO "community_platform_pages" VALUES(415,5564,'fang','泰福名苑','https://sz.esf.fang.com/house-xm2810137888',NULL);
INSERT INTO "community_platform_pages" VALUES(416,5338,'fang','新锦安海纳公馆','https://sz.esf.fang.com/house-xm2810137416',NULL);
INSERT INTO "community_platform_pages" VALUES(417,5919,'fang','都市茗荟花园二期','https://sz.esf.fang.com/house-xm2810137578',NULL);
INSERT INTO "community_platform_pages" VALUES(418,5317,'fang','大悦城天玺壹号','https://sz.esf.fang.com/house-xm2811058928',NULL);
INSERT INTO "community_platform_pages" VALUES(419,5692,'fang','大悦城三期','https://sz.esf.fang.com/house-xm2810202390',NULL);
INSERT INTO "community_platform_pages" VALUES(420,5670,'fang','鸿荣源珈誉府','https://sz.esf.fang.com/house-xm2810205952',NULL);
INSERT INTO "community_platform_pages" VALUES(421,5730,'fang','鸿荣源珈誉府2区','https://sz.esf.fang.com/house-xm2810205952',NULL);
INSERT INTO "community_platform_pages" VALUES(422,5724,'fang','中粮悦章凤凰里','https://sz.esf.fang.com/house-xm2810208632',NULL);
INSERT INTO "community_platform_pages" VALUES(423,5722,'fang','万丰海岸城瀚府一期','https://sz.esf.fang.com/house-xm2810205998',NULL);
INSERT INTO "community_platform_pages" VALUES(424,3424,'fang','西荟城三期','https://sz.esf.fang.com/house-xm2810931848',NULL);
INSERT INTO "community_platform_pages" VALUES(425,3471,'fang','万科翡御郡府','https://sz.esf.fang.com/house-xm2810139320',NULL);
INSERT INTO "community_platform_pages" VALUES(426,3397,'fang','万科翡丽郡一期','https://sz.esf.fang.com/house-xm2811004658',NULL);
INSERT INTO "community_platform_pages" VALUES(427,3467,'fang','万科翡丽郡四期','https://sz.esf.fang.com/house-xm2811004658',NULL);
INSERT INTO "community_platform_pages" VALUES(428,3529,'fang','联投东方华府(一期)','https://sz.esf.fang.com/house-xm2811238572',NULL);
INSERT INTO "community_platform_pages" VALUES(429,3189,'fang','西城上筑','https://sz.esf.fang.com/house-xm2810092263',NULL);
INSERT INTO "community_platform_pages" VALUES(430,3284,'fang','风临洲','https://sz.esf.fang.com/house-xm2810143762',NULL);
INSERT INTO "community_platform_pages" VALUES(431,3207,'fang','菁英趣庭','https://sz.esf.fang.com/house-xm2810151622',NULL);
INSERT INTO "community_platform_pages" VALUES(432,6018,'fang','富基云珑府','https://sz.esf.fang.com/house-xm2810137896',NULL);
INSERT INTO "community_platform_pages" VALUES(433,5535,'fang','中海珑悦理','https://sz.esf.fang.com/house-xm2810206064',NULL);
INSERT INTO "community_platform_pages" VALUES(434,4774,'fang','锦绣江南','https://sz.esf.fang.com/house-xm2810026133',NULL);
INSERT INTO "community_platform_pages" VALUES(435,4800,'fang','锦绣江南','https://sz.esf.fang.com/house-xm2810026133',NULL);
INSERT INTO "community_platform_pages" VALUES(436,4831,'fang','锦绣江南','https://sz.esf.fang.com/house-xm2810026133',NULL);
INSERT INTO "community_platform_pages" VALUES(437,4858,'fang','锦绣江南','https://sz.esf.fang.com/house-xm2810026133',NULL);
INSERT INTO "community_platform_pages" VALUES(438,4747,'fang','苹果园','https://sz.esf.fang.com/house-xm2810032095',NULL);
INSERT INTO "community_platform_pages" VALUES(439,4953,'fang','风和日丽花园三期','https://sz.esf.fang.com/house-xm2810683138',NULL);
INSERT INTO "community_platform_pages" VALUES(440,4841,'fang','日出印象二期','https://sz.esf.fang.com/house-xm2810067875',NULL);
INSERT INTO "community_platform_pages" VALUES(441,4789,'fang','城投七里香榭','https://sz.esf.fang.com/house-xm2810113112',NULL);
INSERT INTO "community_platform_pages" VALUES(442,4820,'fang','幸福枫景','https://sz.esf.fang.com/house-xm2810367782',NULL);
INSERT INTO "community_platform_pages" VALUES(443,4832,'fang','世纪春城三期','https://sz.esf.fang.com/house-xm2810093781',NULL);
INSERT INTO "community_platform_pages" VALUES(444,4509,'fang','朗泓龙园大观二期','https://sz.esf.fang.com/house-xm2810209210',NULL);
INSERT INTO "community_platform_pages" VALUES(445,4190,'fang','远洋新天地','https://sz.esf.fang.com/house-xm2810134840',NULL);
INSERT INTO "community_platform_pages" VALUES(446,4656,'fang','远洋新天地水岸花园','https://sz.esf.fang.com/house-xm2810134840',NULL);
INSERT INTO "community_platform_pages" VALUES(447,6043,'fang','岗宏翰林汇','https://sz.esf.fang.com/house-xm2810208482',NULL);
INSERT INTO "community_platform_pages" VALUES(448,5829,'fang','金光华凤凰九里','https://sz.esf.fang.com/house-xm2810209322',NULL);
INSERT INTO "community_platform_pages" VALUES(449,6046,'fang','合正新悦启园','https://sz.esf.fang.com/house-xm2810200234',NULL);
INSERT INTO "community_platform_pages" VALUES(450,5639,'fang','融湖盛景花园','https://sz.esf.fang.com/house-xm2810205382',NULL);
INSERT INTO "community_platform_pages" VALUES(451,5870,'fang','静安府','https://sz.esf.fang.com/house-xm2810205392',NULL);
INSERT INTO "community_platform_pages" VALUES(452,5739,'fang','紫和嘉园','https://sz.esf.fang.com/house-xm2810208500',NULL);
INSERT INTO "community_platform_pages" VALUES(453,5629,'fang','勤诚达誉府','https://sz.esf.fang.com/house-xm2810199742',NULL);
INSERT INTO "community_platform_pages" VALUES(454,6069,'fang','泰瑞府','https://sz.esf.fang.com/house-xm2810205390',NULL);
INSERT INTO "community_platform_pages" VALUES(455,3971,'fang','绿景城市立方','https://sz.esf.fang.com/house-xm2810582542',NULL);
INSERT INTO "community_platform_pages" VALUES(456,3951,'fang','阳光天健城','https://sz.esf.fang.com/house-xm2810344382',NULL);
INSERT INTO "community_platform_pages" VALUES(457,4078,'fang','奥林华府','https://sz.esf.fang.com/house-xm2810169302',NULL);
INSERT INTO "community_platform_pages" VALUES(458,4314,'fang','园景花园','https://sz.esf.fang.com/house-xm2810025513',NULL);
INSERT INTO "community_platform_pages" VALUES(459,5648,'fang','百合世纪广场','https://sz.esf.fang.com/house-xm2810137706',NULL);
INSERT INTO "community_platform_pages" VALUES(460,5114,'fang','万科金域缇香二期','https://sz.esf.fang.com/house-xm2810989182',NULL);
INSERT INTO "community_platform_pages" VALUES(461,5118,'fang','坪山招商花园城北区','https://sz.esf.fang.com/house-xm2811077782',NULL);
INSERT INTO "community_platform_pages" VALUES(462,5106,'fang','坪山招商花园城南区','https://sz.esf.fang.com/house-xm2811077782',NULL);
INSERT INTO "community_platform_pages" VALUES(463,5121,'fang','大东城二期（嘉宏湾花园二期）','https://sz.esf.fang.com/house-xm2811186112',NULL);
INSERT INTO "community_platform_pages" VALUES(464,2913,'fang','新世界倚山花园(三期)','https://sz.esf.fang.com/house-xm2810143582',NULL);
INSERT INTO "community_platform_pages" VALUES(465,2981,'fang','上善梧桐苑','https://sz.esf.fang.com/house-xm2811179082',NULL);
INSERT INTO "community_platform_pages" VALUES(466,2996,'fang','蓝郡左岸','https://sz.esf.fang.com/house-xm2811059020',NULL);
INSERT INTO "community_platform_pages" VALUES(467,3011,'fang','爱琴湾山庄','https://sz.esf.fang.com/house-xm2810649304',NULL);
INSERT INTO "community_platform_pages" VALUES(468,3045,'fang','皇庭玺园','https://sz.esf.fang.com/house-xm2810773982',NULL);
INSERT INTO "community_platform_pages" VALUES(469,3033,'fang','京基天涛轩','https://sz.esf.fang.com/house-xm2810649302',NULL);
INSERT INTO "community_platform_pages" VALUES(470,172,'fang','绿景山庄','https://sz.esf.fang.com/house-xm2810004405',NULL);
INSERT INTO "community_platform_pages" VALUES(471,275,'fang','比华利山庄','https://sz.esf.fang.com/house-xm2810004311',NULL);
INSERT INTO "community_platform_pages" VALUES(472,1498,'fang','长景阁','https://sz.esf.fang.com/house-xm2810253912',NULL);
INSERT INTO "community_platform_pages" VALUES(473,1198,'fang','香山美树苑','https://sz.esf.fang.com/house-xm2810085879',NULL);
INSERT INTO "community_platform_pages" VALUES(474,1570,'fang','香榭茗园','https://sz.esf.fang.com/house-xm2810258544',NULL);
INSERT INTO "community_platform_pages" VALUES(475,2068,'fang','蔚蓝海岸三期','https://sz.esf.fang.com/house-xm2810030639',NULL);
INSERT INTO "community_platform_pages" VALUES(476,2150,'fang','浪琴屿花园','https://sz.esf.fang.com/house-xm2810004781',NULL);
INSERT INTO "community_platform_pages" VALUES(477,2215,'fang','育德佳园','https://sz.esf.fang.com/house-xm2810024334',NULL);
INSERT INTO "community_platform_pages" VALUES(478,2146,'fang','海上世界双玺','https://sz.esf.fang.com/house-xm2811130750',NULL);
INSERT INTO "community_platform_pages" VALUES(479,2332,'fang','纯海岸','https://sz.esf.fang.com/house-xm2810085740',NULL);
INSERT INTO "community_platform_pages" VALUES(480,5482,'fang','龙光前海天境花园','https://sz.esf.fang.com/house-xm2810138460',NULL);
INSERT INTO "community_platform_pages" VALUES(481,5526,'fang','嵘玺家园','https://sz.esf.fang.com/house-xm2810199560',NULL);
INSERT INTO "community_platform_pages" VALUES(514,3651,'fang','35区住宅楼','https://sz.esf.fang.com/house-xm2810600594',NULL);
INSERT INTO "community_platform_pages" VALUES(515,3201,'fang','中洲华府','https://sz.esf.fang.com/house-xm2810209528',NULL);
INSERT INTO "community_platform_pages" VALUES(516,3479,'fang','宝湖居花园','https://sz.esf.fang.com/house-xm2810266142',NULL);
INSERT INTO "community_platform_pages" VALUES(517,3280,'fang','尚都','https://sz.esf.fang.com/house-xm2810076262',NULL);
INSERT INTO "community_platform_pages" VALUES(518,3384,'fang','风采轩','https://sz.esf.fang.com/house-xm2810011633',NULL);
INSERT INTO "community_platform_pages" VALUES(556,5756,'fang','卓越和奕府二期','https://sz.esf.fang.com/house-xm2810199748',NULL);
INSERT INTO "community_platform_pages" VALUES(557,4160,'fang','信义荔山御园D区','https://sz.esf.fang.com/house-xm2810441902',NULL);
INSERT INTO "community_platform_pages" VALUES(597,5644,'fang','卓越和奕府一期','https://sz.esf.fang.com/house-xm2810199748',NULL);
INSERT INTO "community_platform_pages" VALUES(638,5233,'fang','中信红树湾','https://sz.esf.fang.com/house-xm2810068683',NULL);
INSERT INTO "community_platform_pages" VALUES(639,2095,'fang','中信红树湾南区','https://sz.esf.fang.com/house-xm2810081660',NULL);
INSERT INTO "community_platform_pages" VALUES(640,2048,'fang','中信红树湾北区','https://sz.esf.fang.com/house-xm2811074244',NULL);
INSERT INTO "community_platform_pages" VALUES(754,5073,'ajk','根竹园花园','https://shenzhen.anjuke.com/sale?comm_id=611660',NULL);
INSERT INTO "community_platform_pages" VALUES(755,2068,'ajk','蔚蓝海岸三期','https://shenzhen.anjuke.com/sale?comm_id=115348',NULL);
INSERT INTO "community_platform_pages" VALUES(756,2150,'ajk','浪琴屿花园','https://shenzhen.anjuke.com/sale?comm_id=95420',NULL);
INSERT INTO "community_platform_pages" VALUES(757,2215,'ajk','育德佳园一二期','https://shenzhen.anjuke.com/sale?comm_id=181275',NULL);
INSERT INTO "community_platform_pages" VALUES(758,2263,'ajk','瑞铧苑','https://shenzhen.anjuke.com/sale?comm_id=181142',NULL);
INSERT INTO "community_platform_pages" VALUES(759,2146,'ajk','海上世界双玺','https://shenzhen.anjuke.com/sale?comm_id=616637',NULL);
INSERT INTO "community_platform_pages" VALUES(760,2452,'ajk','水湾1979一期','https://shenzhen.anjuke.com/sale?comm_id=907385',NULL);
INSERT INTO "community_platform_pages" VALUES(761,2466,'ajk','水湾1979二期','https://shenzhen.anjuke.com/sale?comm_id=907385',NULL);
INSERT INTO "community_platform_pages" VALUES(762,2101,'ajk','万科蛇口公馆','https://shenzhen.anjuke.com/sale?comm_id=1025564',NULL);
INSERT INTO "community_platform_pages" VALUES(763,2332,'ajk','纯海岸','https://shenzhen.anjuke.com/sale?comm_id=95670',NULL);
INSERT INTO "community_platform_pages" VALUES(764,2330,'ajk','滨福庭园','https://shenzhen.anjuke.com/sale?comm_id=95782',NULL);
INSERT INTO "community_platform_pages" VALUES(765,2381,'ajk','熙湾','https://shenzhen.anjuke.com/sale?comm_id=95780',NULL);
INSERT INTO "community_platform_pages" VALUES(766,5482,'ajk','前海天境花园','https://shenzhen.anjuke.com/sale?comm_id=1441243',NULL);
INSERT INTO "community_platform_pages" VALUES(767,5667,'ajk','锦尚公馆','https://shenzhen.anjuke.com/sale?comm_id=1682884',NULL);
INSERT INTO "community_platform_pages" VALUES(768,5526,'ajk','招商嵘玺家园','https://shenzhen.anjuke.com/sale?comm_id=1673114',NULL);
INSERT INTO "community_platform_pages" VALUES(769,5525,'ajk','颐城栖湾里','https://shenzhen.anjuke.com/sale?comm_id=1837913',NULL);
INSERT INTO "community_platform_pages" VALUES(770,2032,'ajk','大冲城市花园','https://shenzhen.anjuke.com/sale?comm_id=724900',NULL);
INSERT INTO "community_platform_pages" VALUES(771,2041,'ajk','华润城润府二期','https://shenzhen.anjuke.com/sale?comm_id=1034006',NULL);
INSERT INTO "community_platform_pages" VALUES(772,2185,'ajk','红树别院','https://shenzhen.anjuke.com/sale?comm_id=652107',NULL);
INSERT INTO "community_platform_pages" VALUES(773,1995,'ajk','华润城润府一期','https://shenzhen.anjuke.com/sale?comm_id=907366',NULL);
INSERT INTO "community_platform_pages" VALUES(774,5114,'ajk','万科金域缇香二期','https://shenzhen.anjuke.com/sale?comm_id=506862',NULL);
INSERT INTO "community_platform_pages" VALUES(775,5118,'ajk','坪山招商花园城北区','https://shenzhen.anjuke.com/sale?comm_id=1640575',NULL);
INSERT INTO "community_platform_pages" VALUES(776,5106,'ajk','坪山招商花园城南区','https://shenzhen.anjuke.com/sale?comm_id=576377',NULL);
INSERT INTO "community_platform_pages" VALUES(777,5121,'ajk','大东城二期（嘉宏湾花园二期）','https://shenzhen.anjuke.com/sale?comm_id=1001438',NULL);
INSERT INTO "community_platform_pages" VALUES(778,5650,'ajk','君成世界湾','https://shenzhen.anjuke.com/sale?comm_id=1478643',NULL);
INSERT INTO "community_platform_pages" VALUES(779,5430,'ajk','兴围华府','https://shenzhen.anjuke.com/sale?comm_id=1490098',NULL);
INSERT INTO "community_platform_pages" VALUES(780,5564,'ajk','泰福名苑','https://shenzhen.anjuke.com/sale?comm_id=1806913',NULL);
INSERT INTO "community_platform_pages" VALUES(781,5338,'ajk','海纳公馆','https://shenzhen.anjuke.com/sale?comm_id=1202599',NULL);
INSERT INTO "community_platform_pages" VALUES(782,5919,'ajk','都市茗荟花园二期','https://shenzhen.anjuke.com/sale?comm_id=1891624',NULL);
INSERT INTO "community_platform_pages" VALUES(783,5317,'ajk','大悦城天玺壹号','https://shenzhen.anjuke.com/sale?comm_id=445545',NULL);
INSERT INTO "community_platform_pages" VALUES(784,5692,'ajk','大悦城三期','https://shenzhen.anjuke.com/sale?comm_id=1599820',NULL);
INSERT INTO "community_platform_pages" VALUES(785,5670,'ajk','鸿荣源珈誉府','https://shenzhen.anjuke.com/sale?comm_id=1920735',NULL);
INSERT INTO "community_platform_pages" VALUES(786,5730,'ajk','鸿荣源珈誉府2区','https://shenzhen.anjuke.com/sale?comm_id=1935186',NULL);
INSERT INTO "community_platform_pages" VALUES(787,5724,'ajk','中粮悦章凤凰里','https://shenzhen.anjuke.com/sale?comm_id=1912997',NULL);
INSERT INTO "community_platform_pages" VALUES(788,5722,'ajk','万丰海岸城瀚府一期','https://shenzhen.anjuke.com/sale?comm_id=1906121',NULL);
INSERT INTO "community_platform_pages" VALUES(789,3424,'ajk','西荟城三期','https://shenzhen.anjuke.com/sale?comm_id=843024',NULL);
INSERT INTO "community_platform_pages" VALUES(790,3471,'ajk','万科翡御郡府','https://shenzhen.anjuke.com/sale?comm_id=1025549',NULL);
INSERT INTO "community_platform_pages" VALUES(791,3397,'ajk','万科翡丽郡一期','https://shenzhen.anjuke.com/sale?comm_id=381169',NULL);
INSERT INTO "community_platform_pages" VALUES(792,3467,'ajk','万科翡丽郡四期','https://shenzhen.anjuke.com/sale?comm_id=381169',NULL);
INSERT INTO "community_platform_pages" VALUES(793,3529,'ajk','联投东方华府(一期)','https://shenzhen.anjuke.com/sale?comm_id=843420',NULL);
INSERT INTO "community_platform_pages" VALUES(794,3764,'ajk','前进公社二期','https://shenzhen.anjuke.com/sale?comm_id=614272',NULL);
INSERT INTO "community_platform_pages" VALUES(795,3752,'ajk','鸿盛花园','https://shenzhen.anjuke.com/sale?comm_id=842946',NULL);
INSERT INTO "community_platform_pages" VALUES(796,3189,'ajk','西城上筑','https://shenzhen.anjuke.com/sale?comm_id=95013',NULL);
INSERT INTO "community_platform_pages" VALUES(797,3284,'ajk','风临洲','https://shenzhen.anjuke.com/sale?comm_id=95059',NULL);
INSERT INTO "community_platform_pages" VALUES(798,3207,'ajk','菁英趣庭','https://shenzhen.anjuke.com/sale?comm_id=97683',NULL);
INSERT INTO "community_platform_pages" VALUES(799,3280,'ajk','尚都','https://shenzhen.anjuke.com/sale?comm_id=487114',NULL);
INSERT INTO "community_platform_pages" VALUES(800,3201,'ajk','中洲华府','https://shenzhen.anjuke.com/sale?comm_id=381927',NULL);
INSERT INTO "community_platform_pages" VALUES(801,3651,'ajk','35区住宅楼','https://shenzhen.anjuke.com/sale?comm_id=122065',NULL);
INSERT INTO "community_platform_pages" VALUES(802,3479,'ajk','宝湖居花园','https://shenzhen.anjuke.com/sale?comm_id=95100',NULL);
INSERT INTO "community_platform_pages" VALUES(803,3384,'ajk','风采轩','https://shenzhen.anjuke.com/sale?comm_id=907320',NULL);
INSERT INTO "community_platform_pages" VALUES(804,2913,'ajk','新世界倚山花园(三期)','https://shenzhen.anjuke.com/sale?comm_id=1040673',NULL);
INSERT INTO "community_platform_pages" VALUES(805,2981,'ajk','上善梧桐苑','https://shenzhen.anjuke.com/sale?comm_id=296225',NULL);
INSERT INTO "community_platform_pages" VALUES(806,2931,'ajk','君临海域','https://shenzhen.anjuke.com/sale?comm_id=782237',NULL);
INSERT INTO "community_platform_pages" VALUES(807,2996,'ajk','蓝郡左岸','https://shenzhen.anjuke.com/sale?comm_id=511199',NULL);
INSERT INTO "community_platform_pages" VALUES(808,3011,'ajk','爱琴湾山庄','https://shenzhen.anjuke.com/sale?comm_id=204630',NULL);
INSERT INTO "community_platform_pages" VALUES(809,3045,'ajk','皇庭玺园','https://shenzhen.anjuke.com/sale?comm_id=368064',NULL);
INSERT INTO "community_platform_pages" VALUES(810,3084,'ajk','东部华庭','https://shenzhen.anjuke.com/sale?comm_id=640766',NULL);
INSERT INTO "community_platform_pages" VALUES(811,3033,'ajk','京基天涛轩','https://shenzhen.anjuke.com/sale?comm_id=215289',NULL);
INSERT INTO "community_platform_pages" VALUES(812,5817,'ajk','深业上林苑','https://shenzhen.anjuke.com/sale?comm_id=1929666',NULL);
INSERT INTO "community_platform_pages" VALUES(813,1345,'ajk','福源花园三期','https://shenzhen.anjuke.com/sale?comm_id=842665',NULL);
INSERT INTO "community_platform_pages" VALUES(814,1098,'ajk','朗庭豪园','https://shenzhen.anjuke.com/sale?comm_id=95943',NULL);
INSERT INTO "community_platform_pages" VALUES(815,1333,'ajk','京隆苑','https://shenzhen.anjuke.com/sale?comm_id=96338',NULL);
INSERT INTO "community_platform_pages" VALUES(816,1312,'ajk','中港城','https://shenzhen.anjuke.com/sale?comm_id=96143',NULL);
INSERT INTO "community_platform_pages" VALUES(817,1181,'ajk','香蜜三村','https://shenzhen.anjuke.com/sale?comm_id=95975',NULL);
INSERT INTO "community_platform_pages" VALUES(818,1586,'ajk','香荔新村','https://shenzhen.anjuke.com/sale?comm_id=96132',NULL);
INSERT INTO "community_platform_pages" VALUES(819,1498,'ajk','长景阁','https://shenzhen.anjuke.com/sale?comm_id=120720',NULL);
INSERT INTO "community_platform_pages" VALUES(820,1649,'ajk','惠翔苑','https://shenzhen.anjuke.com/sale?comm_id=188425',NULL);
INSERT INTO "community_platform_pages" VALUES(821,1188,'ajk','港中旅花园一期','https://shenzhen.anjuke.com/sale?comm_id=844680',NULL);
INSERT INTO "community_platform_pages" VALUES(822,1113,'ajk','港中旅花园二期','https://shenzhen.anjuke.com/sale?comm_id=97258',NULL);
INSERT INTO "community_platform_pages" VALUES(823,1103,'ajk','香域中央花园','https://shenzhen.anjuke.com/sale?comm_id=96239',NULL);
INSERT INTO "community_platform_pages" VALUES(824,1198,'ajk','香山美树苑','https://shenzhen.anjuke.com/sale?comm_id=96006',NULL);
INSERT INTO "community_platform_pages" VALUES(825,1570,'ajk','香榭茗园','https://shenzhen.anjuke.com/sale?comm_id=864173',NULL);
INSERT INTO "community_platform_pages" VALUES(826,172,'ajk','绿景山庄','https://shenzhen.anjuke.com/sale?comm_id=97025',NULL);
INSERT INTO "community_platform_pages" VALUES(827,275,'ajk','比华利山庄','https://shenzhen.anjuke.com/sale?comm_id=97001',NULL);
INSERT INTO "community_platform_pages" VALUES(828,314,'ajk','海关草埔生活区','https://shenzhen.anjuke.com/sale?comm_id=728555',NULL);
INSERT INTO "community_platform_pages" VALUES(829,170,'ajk','松泉山庄','https://shenzhen.anjuke.com/sale?comm_id=303043',NULL);
INSERT INTO "community_platform_pages" VALUES(830,6067,'ajk','京基华樾','https://shenzhen.anjuke.com/sale?comm_id=1931670',NULL);
INSERT INTO "community_platform_pages" VALUES(831,5840,'ajk','中信·信荣汇','https://shenzhen.anjuke.com/sale?comm_id=1758944',NULL);
INSERT INTO "community_platform_pages" VALUES(832,335,'ajk','培峰苑','https://shenzhen.anjuke.com/sale?comm_id=911141',NULL);
INSERT INTO "community_platform_pages" VALUES(833,582,'ajk','景福花园','https://shenzhen.anjuke.com/sale?comm_id=1912105',NULL);
INSERT INTO "community_platform_pages" VALUES(835,799,'ajk','莲塘路200号','https://shenzhen.anjuke.com/sale?comm_id=1196914',NULL);
INSERT INTO "community_platform_pages" VALUES(836,389,'ajk','莲塘D小区','https://shenzhen.anjuke.com/sale?comm_id=223311',NULL);
INSERT INTO "community_platform_pages" VALUES(837,491,'ajk','莲通楼','https://shenzhen.anjuke.com/sale?comm_id=843408',NULL);
INSERT INTO "community_platform_pages" VALUES(838,5863,'ajk','壹湾府','https://shenzhen.anjuke.com/sale?comm_id=1805459',NULL);
INSERT INTO "community_platform_pages" VALUES(839,5756,'ajk','卓越和奕府二期','https://shenzhen.anjuke.com/sale?comm_id=1923496',NULL);
INSERT INTO "community_platform_pages" VALUES(840,6018,'ajk','富基云珑府','https://shenzhen.anjuke.com/sale?comm_id=1952346',NULL);
INSERT INTO "community_platform_pages" VALUES(841,5535,'ajk','珑悦理家园','https://shenzhen.anjuke.com/sale?comm_id=1897308',NULL);
INSERT INTO "community_platform_pages" VALUES(842,4800,'ajk','锦绣江南1,2,3期','https://shenzhen.anjuke.com/sale?comm_id=95199',NULL);
INSERT INTO "community_platform_pages" VALUES(843,4831,'ajk','锦绣江南1,2,3期','https://shenzhen.anjuke.com/sale?comm_id=95199',NULL);
INSERT INTO "community_platform_pages" VALUES(844,4858,'ajk','锦绣江南1,2,3期','https://shenzhen.anjuke.com/sale?comm_id=95199',NULL);
INSERT INTO "community_platform_pages" VALUES(845,4774,'ajk','锦绣江南4期','https://shenzhen.anjuke.com/sale?comm_id=95171',NULL);
INSERT INTO "community_platform_pages" VALUES(846,4747,'ajk','苹果园','https://shenzhen.anjuke.com/sale?comm_id=95015',NULL);
INSERT INTO "community_platform_pages" VALUES(847,4953,'ajk','风和日丽花园三期','https://shenzhen.anjuke.com/sale?comm_id=871456',NULL);
INSERT INTO "community_platform_pages" VALUES(848,4841,'ajk','日出印象二期','https://shenzhen.anjuke.com/sale?comm_id=371970',NULL);
INSERT INTO "community_platform_pages" VALUES(849,4789,'ajk','城投七里香榭','https://shenzhen.anjuke.com/sale?comm_id=874190',NULL);
INSERT INTO "community_platform_pages" VALUES(850,4820,'ajk','幸福枫景','https://shenzhen.anjuke.com/sale?comm_id=97328',NULL);
INSERT INTO "community_platform_pages" VALUES(851,4832,'ajk','世纪春城三期','https://shenzhen.anjuke.com/sale?comm_id=98152',NULL);
INSERT INTO "community_platform_pages" VALUES(852,5969,'ajk','沙吓新二村','https://shenzhen.anjuke.com/sale?comm_id=1264321',NULL);
INSERT INTO "community_platform_pages" VALUES(853,4509,'ajk','朗泓龙园大观二期','https://shenzhen.anjuke.com/sale?comm_id=1543994',NULL);
INSERT INTO "community_platform_pages" VALUES(854,5808,'ajk','和健云谷','https://shenzhen.anjuke.com/sale?comm_id=1175172',NULL);
INSERT INTO "community_platform_pages" VALUES(855,4190,'ajk','远洋新天地','https://shenzhen.anjuke.com/sale?comm_id=911120',NULL);
INSERT INTO "community_platform_pages" VALUES(856,4656,'ajk','远洋新天地水岸花园','https://shenzhen.anjuke.com/sale?comm_id=1214825',NULL);
INSERT INTO "community_platform_pages" VALUES(857,6043,'ajk','岗宏翰林汇','https://shenzhen.anjuke.com/sale?comm_id=1964567',NULL);
INSERT INTO "community_platform_pages" VALUES(858,5829,'ajk','金光华凤凰九里','https://shenzhen.anjuke.com/sale?comm_id=1948930',NULL);
INSERT INTO "community_platform_pages" VALUES(859,5870,'ajk','静安府','https://shenzhen.anjuke.com/sale?comm_id=1944898',NULL);
INSERT INTO "community_platform_pages" VALUES(860,5739,'ajk','紫和嘉园','https://shenzhen.anjuke.com/sale?comm_id=1919625',NULL);
INSERT INTO "community_platform_pages" VALUES(861,5629,'ajk','勤诚达誉府','https://shenzhen.anjuke.com/sale?comm_id=1894464',NULL);
INSERT INTO "community_platform_pages" VALUES(862,6037,'ajk','龙湖星玺青云阙','https://shenzhen.anjuke.com/sale?comm_id=1966387',NULL);
INSERT INTO "community_platform_pages" VALUES(863,6069,'ajk','泰瑞府','https://shenzhen.anjuke.com/sale?comm_id=1954022',NULL);
INSERT INTO "community_platform_pages" VALUES(864,3971,'ajk','绿景城市立方','https://shenzhen.anjuke.com/sale?comm_id=317564',NULL);
INSERT INTO "community_platform_pages" VALUES(865,3951,'ajk','阳光天健城','https://shenzhen.anjuke.com/sale?comm_id=97346',NULL);
INSERT INTO "community_platform_pages" VALUES(866,4078,'ajk','奥林华府','https://shenzhen.anjuke.com/sale?comm_id=96749',NULL);
INSERT INTO "community_platform_pages" VALUES(867,4314,'ajk','园景花园','https://shenzhen.anjuke.com/sale?comm_id=96706',NULL);
INSERT INTO "community_platform_pages" VALUES(868,4160,'ajk','信义荔山御园D区','https://shenzhen.anjuke.com/sale?comm_id=657863',NULL);
INSERT INTO "community_platform_pages" VALUES(869,4627,'ajk','西湖新村','https://shenzhen.anjuke.com/sale?comm_id=1264439',NULL);
INSERT INTO "community_platform_pages" VALUES(870,5648,'ajk','百合世纪广场','https://shenzhen.anjuke.com/sale?comm_id=1859227',NULL);
INSERT INTO "community_platform_pages" VALUES(871,949,'ajk','景福花园二期','https://shenzhen.anjuke.com/sale?comm_id=1912105',NULL);
INSERT INTO "community_platform_pages" VALUES(873,2310,'lyj','海上世界双玺花园三期','https://shenzhen.leyoujia.com/esf?b=844560',NULL);
INSERT INTO "community_platform_pages" VALUES(877,4812,'lyj','日出印象A区(一期)','https://shenzhen.leyoujia.com/esf?b=186',NULL);
INSERT INTO "community_platform_pages" VALUES(883,799,'lyj','莲塘路200号','https://shenzhen.leyoujia.com/esf?b=765345',NULL);
INSERT INTO "community_platform_pages" VALUES(884,5233,'lyj','中信红树湾','https://shenzhen.leyoujia.com/esf?b=392',NULL);
INSERT INTO "community_platform_pages" VALUES(885,2095,'lyj','中信红树湾二期','https://shenzhen.leyoujia.com/esf?b=539',NULL);
INSERT INTO "community_platform_pages" VALUES(890,4354,'lj','信义金御半山二期','https://sz.lianjia.com/ershoufang/c2414146798128971',NULL);
INSERT INTO "community_platform_pages" VALUES(891,4354,'ke','信义金御半山二期','https://sz.ke.com/ershoufang/c2414146798128971',NULL);
INSERT INTO "community_platform_pages" VALUES(892,4127,'lj','信义金御半山三期','https://sz.lianjia.com/ershoufang/c2414147411020895',NULL);
INSERT INTO "community_platform_pages" VALUES(893,4127,'ke','信义金御半山三期','https://sz.ke.com/ershoufang/c2414147411020895',NULL);
INSERT INTO "community_platform_pages" VALUES(894,5825,'lj','信义金御半山五期','https://sz.lianjia.com/ershoufang/c24000000115719',NULL);
INSERT INTO "community_platform_pages" VALUES(895,5825,'ke','信义金御半山五期','https://sz.ke.com/ershoufang/c24000000115719',NULL);
INSERT INTO "community_platform_pages" VALUES(896,5685,'lj','信义金御半山珑门','https://sz.lianjia.com/ershoufang/c24000000123427',NULL);
INSERT INTO "community_platform_pages" VALUES(897,5685,'ke','信义金御半山珑门','https://sz.ke.com/ershoufang/c24000000123427',NULL);
INSERT INTO "community_platform_pages" VALUES(900,2141,'lj','蔚蓝海岸1期','https://sz.lianjia.com/ershoufang/c2411049656473',NULL);
INSERT INTO "community_platform_pages" VALUES(901,2141,'ke','蔚蓝海岸1期','https://sz.ke.com/ershoufang/c2411049656473',NULL);
INSERT INTO "community_platform_pages" VALUES(902,2081,'lj','蔚蓝海岸2期','https://sz.lianjia.com/ershoufang/c2411049539129',NULL);
INSERT INTO "community_platform_pages" VALUES(903,2081,'ke','蔚蓝海岸2期','https://sz.ke.com/ershoufang/c2411049539129',NULL);
INSERT INTO "community_platform_pages" VALUES(904,2321,'lj','蔚蓝海岸南山4期','https://sz.lianjia.com/ershoufang/c2411099817462',NULL);
INSERT INTO "community_platform_pages" VALUES(905,2321,'ke','蔚蓝海岸南山4期','https://sz.ke.com/ershoufang/c2411099817462',NULL);
INSERT INTO "community_platform_pages" VALUES(906,5233,'lj','中信红树湾(组页sq6162)','https://sz.lianjia.com/ershoufang/sq6162',NULL);
INSERT INTO "community_platform_pages" VALUES(907,5233,'ke','中信红树湾(组页sq6162)','https://sz.ke.com/ershoufang/sq6162',NULL);
INSERT INTO "community_platform_pages" VALUES(908,2048,'lj','中信红树湾北区','https://sz.lianjia.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(909,2048,'ke','中信红树湾北区','https://sz.ke.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(910,2095,'lj','中信红树湾南区','https://sz.lianjia.com/ershoufang/c2411049783963',NULL);
INSERT INTO "community_platform_pages" VALUES(911,2095,'ke','中信红树湾南区','https://sz.ke.com/ershoufang/c2411049783963',NULL);
INSERT INTO "community_platform_pages" VALUES(912,6083,'ajk','尚都二期','https://shenzhen.anjuke.com/sale?comm_id=1026635',NULL);
INSERT INTO "community_platform_pages" VALUES(913,6083,'ke','尚都二期','https://sz.ke.com/ershoufang/c2414233347044137',NULL);
INSERT INTO "community_platform_pages" VALUES(914,6083,'lj','尚都二期','https://sz.lianjia.com/ershoufang/c2414233347044137',NULL);
INSERT INTO "community_platform_pages" VALUES(915,6083,'fang','尚都','https://sz.esf.fang.com/house-xm2810076262',NULL);
INSERT INTO "community_platform_pages" VALUES(916,6083,'lyj','鸿荣源尚都','https://shenzhen.leyoujia.com/esf?b=48',NULL);
INSERT INTO "community_platform_pages" VALUES(917,6082,'lyj','海上世界双玺','https://shenzhen.leyoujia.com/esf?b=38533',NULL);
INSERT INTO "community_platform_pages" VALUES(918,6082,'ajk','海上世界双玺','https://shenzhen.anjuke.com/sale?comm_id=616637',NULL);
INSERT INTO "community_platform_pages" VALUES(919,6082,'fang','海上世界双玺','https://sz.esf.fang.com/house-xm2811130750',NULL);
INSERT INTO "community_platform_pages" VALUES(920,6082,'ke','海上世界双玺','https://sz.ke.com/ershoufang/c246944473492728',NULL);
INSERT INTO "community_platform_pages" VALUES(921,6082,'lj','海上世界双玺','https://sz.lianjia.com/ershoufang/c246944473492728',NULL);
INSERT INTO "community_platform_pages" VALUES(922,6084,'ajk','中洲华府二期','https://shenzhen.anjuke.com/sale?comm_id=907391',NULL);
INSERT INTO "community_platform_pages" VALUES(923,6084,'fang','中洲华府','https://sz.esf.fang.com/house-xm2810209528',NULL);
INSERT INTO "community_platform_pages" VALUES(924,6084,'ke','中洲华府','https://sz.ke.com/ershoufang/c2411049238509',NULL);
INSERT INTO "community_platform_pages" VALUES(925,6084,'lj','中洲华府','https://sz.lianjia.com/ershoufang/c2411049238509',NULL);
INSERT INTO "community_platform_pages" VALUES(926,6084,'lyj','中洲华府','https://shenzhen.leyoujia.com/esf?b=54049',NULL);
INSERT INTO "community_platform_pages" VALUES(927,6085,'lyj','中信红树湾三期','https://shenzhen.leyoujia.com/esf?b=1002',NULL);
INSERT INTO "community_platform_pages" VALUES(928,6086,'lyj','中信红树湾四期','https://shenzhen.leyoujia.com/esf?b=272',NULL);
INSERT INTO "community_platform_pages" VALUES(929,6087,'lyj','中信红树湾五期','https://shenzhen.leyoujia.com/esf?b=704120',NULL);
INSERT INTO "community_platform_pages" VALUES(930,6085,'ke','中信红树湾北区','https://sz.ke.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(931,6085,'lj','中信红树湾北区','https://sz.lianjia.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(932,6085,'fang','中信红树湾北区','https://sz.esf.fang.com/house-xm2811074244',NULL);
INSERT INTO "community_platform_pages" VALUES(933,6086,'ke','中信红树湾北区','https://sz.ke.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(934,6086,'lj','中信红树湾北区','https://sz.lianjia.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(935,6086,'fang','中信红树湾北区','https://sz.esf.fang.com/house-xm2811074244',NULL);
INSERT INTO "community_platform_pages" VALUES(936,6087,'ke','中信红树湾北区','https://sz.ke.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(937,6087,'lj','中信红树湾北区','https://sz.lianjia.com/ershoufang/c2411049784546',NULL);
INSERT INTO "community_platform_pages" VALUES(938,6087,'fang','中信红树湾北区','https://sz.esf.fang.com/house-xm2811074244',NULL);
INSERT INTO "community_platform_pages" VALUES(939,6088,'ajk','联投东方华府(二期)','https://shenzhen.anjuke.com/sale?comm_id=611969',NULL);
INSERT INTO "community_platform_pages" VALUES(940,6089,'ajk','联投东方华府(三期)','https://shenzhen.anjuke.com/sale?comm_id=1793433',NULL);
INSERT INTO "community_platform_pages" VALUES(941,6090,'ajk','鸿荣源珈誉府3区','https://shenzhen.anjuke.com/sale?comm_id=1994397',NULL);
INSERT INTO "community_platform_pages" VALUES(942,6046,'ajk','合正新悦启园','https://shenzhen.anjuke.com/sale?comm_id=96706',NULL);
INSERT INTO "community_platform_pages" VALUES(943,4812,'ajk','日出印象(一期)','https://shenzhen.anjuke.com/sale?comm_id=177591',NULL);
INSERT INTO "community_platform_pages" VALUES(944,5644,'ajk','卓越和奕府(一期)','https://shenzhen.anjuke.com/sale?comm_id=1892861',NULL);
INSERT INTO "community_platform_pages" VALUES(945,4354,'ajk','信义金御半山二期','https://shenzhen.anjuke.com/sale?comm_id=1176458',NULL);
INSERT INTO "community_platform_pages" VALUES(946,4127,'ajk','信义金御半山三期','https://shenzhen.anjuke.com/sale?comm_id=1019193',NULL);
INSERT INTO "community_platform_pages" VALUES(947,5825,'ajk','信义金御半山五期','https://shenzhen.anjuke.com/sale?comm_id=1908362',NULL);
INSERT INTO "community_platform_pages" VALUES(948,5685,'ajk','信义金御半山珑门','https://shenzhen.anjuke.com/sale?comm_id=1919751',NULL);
INSERT INTO "community_platform_pages" VALUES(949,4296,'ajk','朗泓龙园大观(一期)','https://shenzhen.anjuke.com/sale?comm_id=800964',NULL);
INSERT INTO "community_platform_pages" VALUES(950,4815,'ajk','世纪春城(一期)','https://shenzhen.anjuke.com/sale?comm_id=323584',NULL);
INSERT INTO "community_platform_pages" VALUES(951,4818,'ajk','世纪春城(二期)','https://shenzhen.anjuke.com/sale?comm_id=97566',NULL);
INSERT INTO "community_platform_pages" VALUES(952,4759,'ajk','世纪春城(四期)','https://shenzhen.anjuke.com/sale?comm_id=97606',NULL);
INSERT INTO "community_platform_pages" VALUES(1035,5380,'ajk','佳兆业樾伴山','https://shenzhen.anjuke.com/sale?comm_id=1403197',NULL);
INSERT INTO "community_platform_pages" VALUES(1036,5380,'fang','佳兆业樾伴山','https://sz.esf.fang.com/house-xm2810137960',NULL);
INSERT INTO "community_platform_pages" VALUES(1037,5380,'lj','佳兆业樾伴山','https://sz.lianjia.com/ershoufang/c24000000003189',NULL);
INSERT INTO "community_platform_pages" VALUES(1038,5380,'ke','佳兆业樾伴山','https://sz.ke.com/ershoufang/c24000000003189',NULL);
INSERT INTO "community_platform_pages" VALUES(1039,5380,'lyj','佳兆业樾伴山','https://shenzhen.leyoujia.com/esf?b=848583',NULL);
INSERT INTO "community_platform_pages" VALUES(1040,2310,'ajk','双玺时光道','https://shenzhen.anjuke.com/sale?comm_id=1064384',NULL);
INSERT INTO "community_platform_pages" VALUES(1041,2310,'fang','双玺时光道','https://sz.esf.fang.com/house-xm2810134998',NULL);
INSERT INTO "community_platform_pages" VALUES(1042,2310,'lj','双玺时光道','https://sz.lianjia.com/ershoufang/c2417954746413963',NULL);
INSERT INTO "community_platform_pages" VALUES(1043,2310,'ke','双玺时光道','https://sz.ke.com/ershoufang/c2417954746413963',NULL);
CREATE TABLE deal_records (
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
CREATE TABLE listing_record_logs (
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
CREATE TABLE listing_records (
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
CREATE INDEX idx_deal_records_community
    ON deal_records (community_id, deal_date);
CREATE INDEX idx_deal_records_source_community
    ON deal_records (source_platform, community_id, deal_date);
CREATE INDEX idx_community_platform_pages_community
    ON community_platform_pages (community_id);
CREATE INDEX idx_listing_records_community_active
    ON listing_records (community_id, is_deleted);
CREATE INDEX idx_listing_records_source_community
    ON listing_records (source_platform, community_id, is_deleted);
CREATE INDEX idx_listing_record_logs_record_time
    ON listing_record_logs (listing_record_id, observed_at, id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('community_platform_pages',1043);
COMMIT;
