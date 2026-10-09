# Data Engine 接管审计与首个开发切片

日期：2026-10-09（Asia/Shanghai）。任务：DE-A0 / DE-A1，Issue #879。

## 结论与证据效力

继续复用现有事实平台、采集接纳、存储拓扑、查询与恢复机制。
首个开发切片是为既有 Integration V1 增加 M20 data-use scope **描述元数据**。
本切片不实现 Workspace 授权裁决，也不代表细粒度授权已经落地。

代码审计基线：

| 对象 | 已读取基线 | 效力 |
| --- | --- | --- |
| 产品总规格 | Product Master Spec v1.1，M20 / M01 / M10 / M21 / M22 / M25 | 已批准产品方向；具体模型、商业 Pack、许可与套餐分配仍须对应准入 |
| Data Engine main | `f6d7c103ea37f7300f145150411516d9abe88aee` | 本次实际取得的远端主干 |
| MarkOrbit main | `86715f76d4a5b33839f431e4996a8a00b21c7918` | Core Entitlement 与 Gateway 消费边界，只读审查 |
| Data Engine 测试 | 主干：3089 passed / 2 skipped | Linux 本地测试；不是生产数据库验收 |
| 正在进行的工作 | WIPO MGS draft PR [#878](https://github.com/yoomarks/markorbit-data-engine/pull/878) | 单独开发范围；不修改其 reference-data / admission 文件 |
| 运维资料 | #837 / #843 / #849 最新已有评论与仓库 runbook | 历史报告与冻结计划；不是本次现场测量 |

未连接 Windows 生产主机、生产 PostgreSQL/ClickHouse 或 D/E/F 磁盘。
因此不能确认当前实际行数、空间、服务进程、调度、迁移执行状态或最新备份恢复结果。
本次没有进行生产查询、下载整库、部署、源文件清理或任何生产写入。
当前读取到的 Actions 记录主要是 PR 验证，也不能代替生产证据。

## 三个视角的共同决议

**产品：** 优先让现有数据成为可解释、可授权、可维护的业务输入。
八个 Data-linked 优先法域不是八个已完成数据集；全球资产导入也不依赖国家数据已上线。
查询、商机挖掘、Creator 使用与公开传播分别检查用途。
不先扩充国家清单或建立第二套 DataGrant 系统。

**体验：** 上层应分别呈现来源、资料截至时间、覆盖、冲突、权限和降级原因。
取不到、未覆盖、部分、过期不能显示成“0 / 没有 / 暂无事项”。
数据源成熟度与用户套餐权限也不能合成一个“可用”开关。
Data Engine 提供语义与证据；Lite/MO/MarkReg 拥有页面、双语文案与行动入口。
本切片只增加后台合同，不新增 UI，因此未声称完成 Storybook 或浏览器验收。

**技术：** 保留 PostgreSQL 控制面、现有按域存储、来源 pinning、manifest、checkpoint、
Data Trust、bounded query 和精确生产 authority plan。
Core 继续拥有 Entitlement；Knowledge 继续负责已归属其范围的采集与文档；
Data Engine 接纳结构化事实。事实变化不能自动变成 Work/Matter、法定期限或正式商机。
不新增并行 scheduler、数据库、grant store 或通用管理 JSON 写入口。

## KEEP / HARDEN / REWIRE / MIGRATE / BUILD / RETIRE

这些标记是后续处理建议，不是迁移或删除授权。

| 范围 | 判断 | 当前证据与下一步 |
| --- | --- | --- |
| CN / US 事实和 provenance | KEEP / HARDEN | `README.md`、`app/cn`、`app/us`、Assignment/TTAB 保持 source-first；补实时 currentness / acceptance 证据 |
| Data Trust | KEEP | `app/data_trust.py` 已分 queryable / complete / fresh / accepted / trusted-for-silence；不能替代 Workspace 权限 |
| 查询与身份检索 | KEEP / HARDEN | `integration_api.py`、`read_query_capability.py` 已有 bounded/keyset 与 exact reads；unsupported、unavailable 不得伪装空结果 |
| M20 scope 元数据 | BUILD（DE-A1） | 当前认证合同缺少五维使用语义；增量加入 foundation descriptor |
| 真实 M20 授权链 | REWIRE / EXTEND（待消费方准入） | 复用 Core `EntitlementGrantV1` / `ResolvedEntitlementV1`；先确认 key/value 与可信请求上下文映射，不能因元数据已发布就开放用途 |
| 国家来源目录 | HARDEN | `trademark_framework`、factory、SG snapshot、Global Hot 接纳各有现成语义；先整理差异和运行证据，不新建替代目录 |
| Work / DAG / operations | KEEP / HARDEN | 复用现有 Work Engine、SG DAG 与恢复分类；恢复候选不等于执行授权；补 MO typed-command 对接需另切片 |
| 研究 dataset | KEEP / HARDEN | `research_dataset.py` 与 CN filing-to-preliminary duration/replay 已存在；按真实 Brain 任务验证有界查询和精确 dataset lineage，不重建一套研究平台 |
| US deadline / maintenance 与 CN inference | REWIRE 评审 | `app/us/application_deadlines.py`、`maintenance.py`、`app/cn/case_status_inference.py` 是现有解释能力；M10 后续需审核与 MarkReg policy Owner 的接口，不能包装成官方或已审法律期限；本次不移动或删除 |
| Hot/Warm / Visual | KEEP / MIGRATE 既有任务 | 使用 Storage Topology V2；迁移、路由、compaction、源回收分开验收 |
| 重复系统 | RETIRE 候选策略 | 不新增平行授权中心、Knowledge 爬虫、生命周期 Rule Registry；未识别并确认可删除对象前不执行删除 |

## 国家与生产准备度

下表是代码和已有文档的结论，不是当前线上覆盖清单。

| 法域 | 现有路径 | 尚不能据此声称 |
| --- | --- | --- |
| CN | 成熟 native facts、来源优先级、查询、索引、Hot 迁移与切换 gates | R9 数据迁移完成不代表 serving/write cutover 已验收 |
| US | Application / Assignment / TTAB / TSDR / images / history / discovery | 所有数据均为最新；Assignment 记录等于法律权属；源事件等于已审期限 |
| GB | 2018 Domestic/Madrid 历史接纳、E stage、weekly journal 的受控入库与 F raw archive | 2018 source 或 journal observation 等于当前 register 全量状态；runbook 目标行数等于实测完成行数 |
| SG | IPOS 完整快照、delta/native evidence、独立归档恢复、单次 authority-bound refresh | 当前已按 M/W/F 自动更新；source probe 行数等于 accepted corpus 行数 |
| LA | Knowledge → Data Engine Global Hot pilot / full-baseline V2 接纳边界 | Data Engine 已完成官网存量采集；V2 代码可用等于 full coverage 或已启用周期采集 |
| CA | ST.96 baseline、rich child observations、ordered current / Delete 语义 | `CIPO_WEEKLY` 已通过真实 production-current 准入；image 全覆盖 |
| EU | TM-Link 历史 seed；EUIPO refresh/enrichment 保留接口方向 | historical seed 是当前权威注册簿 |
| AU | IPGOD 2022 多表历史接纳 | 已具备历史来源之后的当前 freshness |
| NZ | TM-Link 历史 seed；IPONZ 后续官方更新方向 | seed 当前状态已核实；官方 API 已可生产使用 |

### 已有运维结论的精确范围

- [#837](https://github.com/yoomarks/markorbit-data-engine/issues/837)：历史 R9 六表迁移与 residency 报告 accepted；后续 cutover、compaction 和 Phase D source reclaim 尚是独立 gate。不能拿表迁移 receipt 代替服务切换。
- [#843](https://github.com/yoomarks/markorbit-data-engine/issues/843)：最新已有记录是 `FROZEN_REPRESENTATIVE_READ_PLAN_NO_APPLY`。计划 SHA `54f502860a741b0a34765e16798b529e82b84eb54d9ade7c42e3d6e097d22bc9`；代表性查询尚未执行。即使将来通过，也不授权 serving。
- [#849](https://github.com/yoomarks/markorbit-data-engine/issues/849)：最新已有计划是 `FROZEN_NO_APPLY`，SHA `579410c4735be4ee875fb26cd2bce33175e391ff87c7ffb48a37d44a42b8f89f`。历史 accepted snapshot 为 878,707 rows；当时 source-only probe 为 879,500 rows，不能混用。`recurring_schedule_enabled=false`。
- LA V2：`docs/LA_FULL_BASELINE_ADMISSION_V2.md` 仍要求上游真实 pilot、UInt16 实际 schema、单独 full-baseline enablement、真实 ID manifest 与覆盖验收；不在 Data Engine 重做网页采集。

以上计划在本次未重新冻结或执行。不得从本报告复制为新执行授权。

### 存储与成本

`app/storage_topology_v2.py` 已明确：D 只承接 `hot_cn`；E 承接 `hot_us`、
`hot_global` 和全部 Warm 增长；F 承接 Raw、backup、original visual 和 Knowledge artifacts。
GB / LA / SG structured hot 数据归 E / hot_global，GB logo/raw authority 归 F。
机器策略的 recommended reserve 为 30%、hard reserve 为 20%；旧 headroom 与各域 gate 仍需分别检查，
不能用某一个阈值替换其他现有安全门。以上是策略，不是本次实测容量。

SG 维护复用 #849：便宜 probe 与整库刷新分开；常规整库候选约每周一次；
周留存点采用不可变 manifest/内容去重，月备份必须有独立物理副本和恢复校验。
LA 维护按来源周期与状态分层，当前“每周 1–2 次”的产品方向需要真实 source/cost 证据后准入。
Workspace priority watch 复用来源去重，不能为一个案件暗中触发国家整库下载。
本次不批准新频率、SLA、价格、免费额度或生产 cron。

## DE-A1：本次实现与边界

入口：`GET /api/v1/contract` → `foundation_contracts.data_use_scope`。
版本：`MARKORBIT_DATA_USE_SCOPE_DESCRIPTOR_V1`，状态 `METADATA_ONLY`。

描述五维 scope、八种 access action、八个优先法域、Core 授权职责、未来 admission 条件、
required/optional dependencies 和 dataset metadata 需求。
明确 `runtime_enforcement_implemented=false` 与 `descriptor_grants_access=false`。
Creator 沿用 CAPABILITY_USE + purpose/product context，不创建 CREATOR_USE。

现有服务 bearer、rate limit、只读路由、资源清单、G0 JSON artifact 与事实 envelope 不变。
无新端点、schema、生产授权裁决、商业分配、Source licence 结论或调度。
`deny_by_default` 是未来 admission 的要求，不能被解释为现有所有读路由已实现 M20 deny-by-default。

消费方应在后续 owner task 中复用 Core 合同，审查可信服务端 subject、purpose、grant/version、
source licence/currentness 和 action 映射。Data Engine 不接受客户端自称已获授权，不直接读 Core 数据库。
具体 `DataUseGrant/evaluation` 投影是否需要新增，须由该审查确认；本切片没有预设答案。

## 后续顺序与验收门

| 顺序 | 工作 | 完成依据 |
| --- | --- | --- |
| 1 | DE-A1 metadata（#879） | 相关测试、Ruff、全套 pytest 和适用 hosted CI；遵循仓库当前 review/merge 规则 |
| 2 | DE-A2 consumer/provider 授权对接审计 | 确认 Core 既有 key/value 能表达哪些 scope；冻结最小跨库合同与负向 fixtures，再实现真实 trusted-context evaluation |
| 3 | DE-O1 现场只读 current-state 校验 | fresh host / DB / serving / topology / source coverage / backup receipt；沿 #837/#843/#849 原任务收口，不重复开迁移 |
| 4 | DE-W1 Source freshness / watch 接口 | 复用现有事实变化与 source refs；required/optional/unknown/degraded + 去重/预算/回放；业务 Owner 消费，不自动创建正式业务对象 |
| 5 | DE-R1 有界研究与 dataset replay | 由获准 Brain job 驱动，只补现有 research primitive 缺口；精确 source/query/snapshot lineage、成本和历史复现 |
| 6 | 国家扩展与生产推广 | 当前来源、许可、数据准备度、恢复、性能和真实消费者逐域验收；不能因八国方向已批准就批量启用 |

普通代码可以继续开发。现场通道当前缺失，只影响实时生产核验；
精确生产执行 token、登录或部署授权按各自真实 gate 在需要时处理。
非破坏性开发不等待生产通道；生产 mutation 不通过通用接管指示绕开既有 authority gate。

## 验证与交付追踪

主干基线：3089 passed / 2 skipped。DE-A1 最终本地全套：3094 passed / 2 skipped，Ruff 通过。DE-A1 focused：37 passed（含 HTTP 401 / 200 / 405 和无效认证配置 503）。
使用当前 MarkOrbit 的真实 `parseDataEngineIntegrationDescriptor` 读取本切片实际 descriptor，
新元数据兼容、禁止跨服务 DB 的负向校验通过；这是 parser 兼容证据，不是实际 Core data-use 授权验收。
最终验证以关联 PR 的 exact head、Actions 与 PR 描述为准；本审计不提前宣称 hosted CI、
独立审批、合并或生产验收完成。
两个 skip 为既有套件行为，本次未新增 skip 或删除断言。
本机无 Docker / Windows PowerShell，数据库 runtime-image / fixture 与 PowerShell 检查交由适用 hosted CI。

持久交付：本报告、代码、tests 和后续任务边界随 Data Engine 分支/PR 管理。
附件总规格保持原件不变；MarkOrbit 主仓库本次没有写入。
