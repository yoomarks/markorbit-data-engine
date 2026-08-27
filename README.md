# MarkOrbit Data Engine — M1.7

MarkOrbit Data Engine 是 MarkOrbit 的 **authoritative source-fact service**：PostgreSQL 管理 source package / job / control-plane state，ClickHouse 保存 CN、US Application、US Assignment、US TTAB 等 durable official facts。事实层保留来源、时间与可重放证据，不把原始官方状态、Assignment 或 TTAB 程序事实直接提升为法律结论。

根目录 `VERSION` 是 Data Engine **发布标记**，当前为 `M1.7`。各数据域拥有独立组件版本，不再用一个总版本推断所有组件。

## 当前组件版本

| 组件 | 当前版本 |
|---|---|
| Data Engine release | `M1.7` |
| CN fact model | `CN_M1.6` |
| US Application | `US_M1.4` |
| US Assignment | `US_ASSIGNMENT_M1.0` |
| US TTAB | `US_TTAB_M1.2` |
| US Alert Engine | `US_ALERT_ENGINE_M1.0` |
| Storage policy | `DATA_ENGINE_STORAGE_V2` |
| Replay telemetry | `DATA_ENGINE_REPLAY_TELEMETRY_V1` |
| Integration contract | `MARKORBIT_DATA_ENGINE_INTEGRATION_V1` |
| Domain lifecycle | `MARKORBIT_DOMAIN_LIFECYCLE_V1` |
| Four-domain acceptance | `MARKORBIT_FOUR_DOMAIN_ACCEPTANCE_V1` |

机器可读的权威矩阵由 `app.component_versions.component_versions()` 提供，并暴露在 `GET /api/v1/contract` 的 `component_versions` 字段。完整规则见 `docs/COMPONENT_VERSIONS.md`。

## 冻结的数据域顺序

真实 corpus 的推进顺序固定为：

```text
CN
 ↓
US Application
 ↓
US Assignment
 ↓
US TTAB
 ↓
Final Four-domain Acceptance
```

下游真实写入不是靠人工记忆控制：所有正式 US mutation entrypoint 都有 upstream apply gate；CN/US 写入口也有磁盘 headroom guard。不要绕过这些脚本直接执行数据库写入或 ad-hoc ingestion。

统一只读状态入口：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\status-domain-lifecycle.ps1 `
  -ExpectedHistoryParts 91
```

## CN M1.6

核心语义：

- 完整申请号是案件 identity；结构后缀关系与法律原因分离。
- `BASE_PARTITION` / `MONTHLY_PATCH` 的优先级由来源语义决定，不由导入时间决定。
- 月更未出现不解释为删除。
- `cn_goods_item_current` 保存 durable goods current facts 和 first-source provenance。
- `cn_goods_item_observation` 在 Storage V2 下只保存真实 transition，不保存重复 baseline observation。
- `cn_observed_event` 保存真实 delta + canonical PARTY history；可重建 baseline event 被抑制。
- `cn_case_party_relation_history` 仅保留 legacy compatibility schema，不再作为 canonical history。
- 案件状态推理与官方事实严格分层；经验规则仍需独立真值验证。

真实 CN replay 前必须先运行非破坏性 M1.6 preflight：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\preflight-m16-real-data.ps1
```

只有 preflight 明确允许后才进入真实 replay。状态推理审计是另一条更严格的边界：只有报告明确显示 `safe_to_run_inference_audit = true` 时才允许运行推理审计；这不会改变官方事实层。

CN 全量 replay：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\replay-cn-full.ps1
```

失败包显式恢复：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\replay-cn-full.ps1 `
  -ResumeFailed
```

只读 readiness：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-cn-replay-readiness.ps1
```

CN 完成后的最终 checkpoint：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\check-cn-final-checkpoint.ps1
```

## Storage V2

Storage V2 采用三层思路：

1. **Raw Authority**：官方 ZIP/XML 原始源保留，可校验 SHA-256、可重放。
2. **Current Fact Store**：ClickHouse 保存查询面需要的最新 durable facts。
3. **True Delta History**：只保存真实变化，不重复堆同一 baseline/current observation。

关键规则：

- raw source 是最终 authority；
- current 表承担可重建的当前状态；
- history 只保留不可由 current + raw 直接替代的真实 transition/provenance；
- 不依赖盲目 `OPTIMIZE FINAL` 清空间；
- 大规模 compaction 使用受保护 shadow/exchange 语义；
- replay 后必须保持 Storage V2 baseline regression = 0。

只读深度审计：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\audit-storage.ps1 `
  -Deep
```

## 磁盘安全门

Windows + Docker Desktop 环境同时检查宿主盘和 ClickHouse 内部盘。默认两层都必须至少保留：

```text
max(128 GiB, 10% total capacity) + 32 GiB reserve
```

手工检查：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\assert-storage-headroom.ps1
```

真实 CN/US mutation 脚本会自动调用该 gate。详见 `docs/STORAGE_HEADROOM_GUARD.md`。

## Replay Telemetry

主 deterministic replay 会自动记录 `DATA_ENGINE_REPLAY_TELEMETRY_V1` 运行账本：Git SHA、组件版本、开始/结束时间、package status 变化、ClickHouse active bytes/rows、stage bytes、ClickHouse 内部磁盘以及 Windows 宿主盘 free space。

```text
reports/replay_runs/<run_id>.json
reports/replay_ledger.jsonl
```

US 只有真实 `-Apply` 才记录，dry-run 不进入 ledger；CN full replay 始终属于真实 mutation command。所有 delta 都是 before/after observation，不是增长预测，也不宣称测到了运行过程中的 peak temporary usage。Telemetry 只做 SELECT + 本地 `reports/` 写入，失败只告警，不得覆盖 replay 原始结果。详见 `docs/REPLAY_TELEMETRY.md`。

## US Application M1.4

US Application 使用 USPTO serial number 作为 case identity，并支持历史覆盖分片 + daily package 的 deterministic precedence。

M1.4 当前包括：

- filed basis / current basis 分离；
- owner nationality 与地址国家分离；
- owner/classification/statement/correspondent 等 snapshot child replacement；
- event / Madrid event 累积历史；
- durable `us_case_observation_history` change history；
- 部分日期如 `YYYYMM00` 保留 raw，typed date 不伪造；
- source rank 与 package SHA-256 驱动 deterministic replay；
- raw status/event/statement/maintenance indicators 不直接解释成法律结论。

在 CN final checkpoint 通过后，US Application deterministic replay 才允许 `-Apply`：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\replay-us-deterministic.ps1 `
  -ExpectedHistoryParts 91 `
  -Apply -All
```

正式 acceptance 使用 source-backed audit。

## US Assignment M1.0

Assignment 域保存 USPTO recorded assignment / recorded-interest facts。

边界：

- 不把 recordation 自动解释为当前 title / legal ownership；
- source kind、recorded metadata、property serial 等保持显式；
- 不从文件名制造 effective date；
- 只有 US Application source-backed accepted 后才允许 Assignment mutation。

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\replay-us-assignment-deterministic.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -Apply -All
```

## US TTAB M1.2

TTAB 域保存程序性官方事实，不推断案件实体权利或最终结果。

边界：

- authoritative snapshot timestamp 必须可追溯；
- 不制造 midnight timestamp；
- 不从文件名推断程序时间；
- Assignment accepted 后才允许 TTAB mutation。

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\replay-us-ttab-deterministic.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -Apply -All
```

## 四域最终验收

四域都 accepted 后，统一 runner 会按冻结顺序重新生成正确的正式报告并调用现有 formal gate：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\run-four-domain-final-acceptance.ps1 `
  -ExpectedApplicationHistoryParts 91 `
  -ExpectedApplicationDailyThrough <authoritative-coverage-date>
```

它会保留 lifecycle、四域 acceptance、final acceptance 与 SHA-256 manifest，防止人工拿错 report 类型。

## Integration API V1

稳定只读 integration plane：

- `GET /api/v1/health`
- `GET /api/v1/contract`
- `GET /api/v1/cn/cases/{application_number}`
- `GET /api/v1/us/cases/{serial_number}`
- `GET /api/v1/us/cases/{serial_number}/360`
- `GET /api/v1/us/cases/{serial_number}/history`
- `GET /api/v1/us/cases/{serial_number}/assignments`
- `GET /api/v1/us/cases/{serial_number}/ttab`
- `GET /api/v1/us/changes`

Transport headers：

- `X-Request-ID`
- `X-MarkOrbit-Contract-Version`
- `X-MarkOrbit-Source-Owner`

Integration plane 是 read-only source-fact contract；consumer 不写回 Data Engine source facts，也不应跨服务直连数据库。

## 本地目录

```text
D:\yoomarks\markorbit-data-engine\
├── raw_data\
│   ├── incoming\cn\
│   ├── archive\cn\
│   ├── incoming\us\
│   ├── archive\us\
│   ├── quarantine\
│   └── temp\
├── reports\
└── ...
```

## CI

主 CI 同时覆盖：

- Ruff + Pytest；
- Linux runtime image；
- CN / Storage V2 fixtures；
- US Application / Assignment / TTAB / Alert fixtures；
- `windows-latest` 原生 PowerShell parser；
- 关键 operator script 参数契约。

Windows CI 不启动 live Docker corpus，只检查 PowerShell 语法和 operator contract；真实数据库行为继续由 Linux fixture 与本地受保护 replay 验证。

## 数据边界

- 原始官方 source package 是权威来源，注册后以 SHA-256 标识。
- source precedence 不由导入时间决定。
- CN 月更 omission 不等于 deletion。
- `FIRST_OBSERVED` 不等于法律状态发生日。
- US Assignment recordation 不等于 title conclusion。
- TTAB procedural fact 不等于 substantive-rights / outcome conclusion。
- 推理、风险、deadline interpretation 与官方事实分层保存。
- 跨 Application / Assignment / TTAB 不制造无权威来源支撑的 chronology。

进一步阅读：

- `docs/ARCHITECTURE.md`
- `docs/COMPONENT_VERSIONS.md`
- `docs/DOMAIN_APPLY_GATES.md`
- `docs/STORAGE_HEADROOM_GUARD.md`
- `docs/REPLAY_TELEMETRY.md`
- `docs/FOUR_DOMAIN_FINAL_RUNNER.md`
- `docs/M1_6_REAL_DATA_PREFLIGHT.md`
- `docs/CN_GOODS_LIFECYCLE_MODEL_V2.md`
- `docs/CN_CASE_STATUS_INFERENCE_MODEL_V1.md`
- `docs/US_M1_CORE_MODEL.md`
- `docs/US_M1_INGESTION.md`
