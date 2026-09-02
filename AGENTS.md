# AGENTS.md — jeethink-rpa 开发协作规范

> 本文件仅约束 `jeethink-rpa` Python 工程，与仓库根目录的 `AGENTS.md` 独立。
> 改动前先阅读本文；当本文、项目文档和用户当次指令冲突时，以用户当次指令为准。

## 1. 项目定位

jeethink-rpa 是房产实时询价工程。它使用 FastAPI 和 nodriver，面向多平台采集房产数据，并与小区主数据、房源记录模块协作完成询价和数据沉淀。

当前接入贝壳、安居客、链家、房天下、乐有家。入口服务是 `scripts/api_server.py`。

## 2. 开始工作前

先根据任务读取对应文档，不在 `AGENTS.md` 重复架构、接口和业务规则：

| 任务 | 必读文档 |
|---|---|
| 了解项目和运行方式 | `README.md` |
| RPA 分层、状态、并发、风控 | `docs/系统架构与运行时状态.md` |
| 新平台或平台 HTML 改造 | `docs/平台扩展对接文档.md` |
| 小区主数据 | `docs/小区基础数据模块.md` |
| 房源记录和入库 | `docs/房源记录模块.md` |
| HTTP 接口 | `docs/API接口文档.md` |
| 当前阶段需求 | `work/` 中与任务对应的文件（目录存在时） |

`work/` 是临时工作文档目录，记录已确认但可能尚未实现的需求；需求完成后会删除。不要把其中内容当作现有代码行为，也不要让永久代码依赖该目录。

## 3. 开发流程与约束

新功能、业务流程调整或跨模块改动必须按以下顺序推进，环节不能跳过：

1. **沟通**：理解用户目标、范围和术语；区分用户请求、现有代码和文档描述。
2. **需求评审**：检查当前实现和影响模块，说明已确认规则、风险、缺口和明确不在范围内的内容。
3. **技术方案**：给出模块职责、数据流、接口或模型影响、关键边界、测试重点和取舍；此时不改代码，也不擅自写正式文档。
4. **实施计划**：将已确认方案拆成可独立验收的工作块，说明实施顺序和验证方式。
5. **用户拍板**：用户确认方案和计划后，才可以按其授权写入 `work/` 文档；只有用户明确要求“开始开发”或“实现”后，才可以改代码。

用户可以分别授权文档和代码。方案确认不自动等于代码授权；需求仍在讨论时，不得提前创建文档或实现。

- 先区分现有代码、文档约定和当前需求；不明确的行为不得自行补全为新功能。
- 复用项目已有分层、模型和工具；不要为了未确认的后续需求预建同步、下架或大而全的抽象。
- 既有平台采集顺序、算法决策、状态机和风控边界，未经用户明确授权不得改变。
- 平台页面改造先核对真实 HTML，再修改 parser；按现有 MVP 脚本逐步验证，不盲写选择器或正则。
- parser 保持纯函数；浏览器、登录态和风控处理留在 adapter；不要让平台 adapter 直接写业务数据库。
- Python 文件以 `# -*- coding: utf-8 -*-` 和简短 docstring 开头。日志使用 `logging.getLogger(__name__)`，关键步骤记录上下文，异常使用 warning/error。
- 代码实现后，再同步已经实现内容的正式文档。

## 4. 测试与验证

测试保持精简，只覆盖当前业务重点。当前保留 14 个测试模块、45 个测试。

| 改动范围 | 运行目标 |
|---|---|
| `app/community_data/*` | `tests/community_data/test_community_data.py` |
| `app/inquiry/*` | `tests/inquiry/` |
| `app/inquiry_analysis/*` | `tests/inquiry_analysis/` |
| `app/rpa/parsers/ajk.py` | `tests/parsers/test_ajk.py` |
| `app/rpa/parsers/fang.py` | `tests/parsers/test_fang.py` |
| `app/rpa/parsers/ke.py` | `tests/parsers/test_ke.py` |
| `app/rpa/parsers/lj.py` | `tests/parsers/test_lj.py` |
| `app/rpa/parsers/lyj.py` | `tests/parsers/test_lyj.py` |
| `app/rpa/platforms/base.py` 小区归属 | `tests/platforms/test_base_community.py` |
| `app/property_records/*` | `tests/property_records/test_property_records.py` |

- 改动后必须运行与改动直接相关的测试，不必默认跑全量。
- 新能力只添加验证关键业务边界的最小测试，不恢复已删除的低价值测试套件。
- 平台 HTML 改造先用对应 MVP 脚本验证真实页面，再运行相邻 parser 测试。
- 全量 `python -m pytest tests/ -v` 仅在改动面大、怀疑广泛回归或发版前执行。

## 5. Git 提交规范

- 只有用户明确要求时才创建提交。
- 提交前检查 `git status --short`、`git diff` 和暂存区；不得覆盖或夹带用户已有的无关改动、未跟踪脚本、输出和运行产物。
- 只暂存本次任务相关文件，不使用 `git add .` 扩大范围。
- 提交前执行 `git diff --cached --check`；代码改动还要运行对应测试。纯文档整理可不跑测试，但必须说明原因。
- 提交信息使用简洁中文，一个提交只处理一个完整工作块。
- 提交后说明提交哈希、提交内容、验证结果，以及未纳入的用户文件。
- 禁止使用 `git reset --hard`、`git checkout --` 等破坏性操作覆盖用户改动。
