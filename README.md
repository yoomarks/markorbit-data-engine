# MarkOrbit Data Engine — M1.6 + US M1

MarkOrbit Data Engine 当前同时承载中国商标 M1.6 与美国商标 US M1。PostgreSQL 管理跨法域的数据包、任务、质量与实体控制面；ClickHouse 保存各法域的 durable official facts。根目录 `VERSION` 仍是当前引擎发布标记 `M1.6`；美国数据模型独立使用组件版本 `US_M1.0`。

## CN M1.6 核心能力

- **案件身份稳定**：完整申请号是一案一键；`A/B/AA` 等后缀形成结构派生关系，具体法律原因无证据时保持 `UNKNOWN`。
- **中国直接申请与马德里指定中国统一建模**：`G` 号仍是 CN 案件，WIPO 国际注册号只作为跨源关联键。
- **来源优先级由来源语义决定**：`BASE_PARTITION` 是申请年份分片；`MONTHLY_PATCH` 是后续更新，月更优先于基础分片，导入时间不决定当前事实。
- **商品项持久化**：M1.6 新增 `cn_goods_item_current`，不再只保存 class 聚合结果。
- **商品状态变化可追溯**：`cn_goods_item_observation` 保存真实 item transition；`FIRST_OBSERVED` 只是首次看见，不伪装成法律事件时间。
- **月更遗漏不等于删除**：月更只标识 touched scope，最终 class scope 从完整 durable goods universe 重建。
- **范围生命周期**：`cn_goods_scope_lifecycle_current` 区分 operational effective、risk、high-confidence inactive、final inactive 与 unknown。
- **严格商品身份**：商品 identity 由申请号、类别、商品序号、类似群、规范化名称共同形成，避免仅凭序号错误合并。
- **官方事实与推理彻底分层**：商品代码描述商品证据，不直接编码案件法律状态或原因。
- **案件状态推理仍为 EMPIRICAL**：R1–R7 只在独立验证层运行，不写入官方事实表；必须经过官方证据人工真值复核后才可能升级。

当前发布标记以仓库根目录 `VERSION` 为唯一来源；API `/api/health`、`/api/cn/summary` 与运行镜像均读取同一标记。

## US M1 核心能力

- **USPTO serial number 为案件身份**：registration number 仅作为属性和辅助查询键。
- **官方事实与法律解释分层**：保存 USPTO raw `status_code/status_date`、事件、statement、filing-basis flags，不在事实层直接生成 `ACTIVE/DEAD`、Section 8/15 或 renewal 结论。
- **流式 XML 解析**：Trademark Daily Applications XML 使用 `iterparse`，ZIP 内 XML 直接流式读取，不展开整包到临时目录。
- **部分日期不伪造**：类似 `YYYYMM00` 的 first-use 日期保留 raw 值，typed date 为 `NULL`。
- **确定性日包优先级**：当前支持 `apcYYMMDD.zip` / `.xml`；未知文件名、同日多份不同源、同日未建模 revision 均阻断。
- **整包重放恢复**：registered SHA-256 在发布前重新校验；中断/失败时按 source package UUID 清理 US 输出并从权威源整包重放。
- **一次只处理一个 US 包**：在真实 USPTO 包验收建立性能与质量基线前，不启用批量自动追赶。

US M1 当前核心表：

- `us_case_current`
- `us_owner_current`
- `us_classification_current`
- `us_event_history`
- `us_statement_current`

## 本地目录

```text
D:\yoomarks\markorbit-data-engine\
├── raw_data\
│   ├── incoming\cn\       # 待导入/待重放 CN ZIP
│   ├── archive\cn\        # 已成功导入的 CN 权威原 ZIP
│   ├── incoming\us\       # 待导入 USPTO daily XML/ZIP
│   ├── archive\us\        # 已成功导入的 US 权威源包
│   ├── quarantine\
│   └── temp\
├── reports\
└── ...
```

## CN M1.6 干净重建

M1.6 durable item history 不能从旧的 class aggregate 凭空恢复，因此从 M1.5 升级或需要完整重放时，应进行一次干净重建。`reset-m16.ps1` 会删除 PostgreSQL/ClickHouse 开发卷，但**不会删除 raw ZIP**；它还会把 archive 中缺失于 incoming 的 ZIP 复制回待重放目录。

```powershell
cd D:\yoomarks\markorbit-data-engine
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-m16.ps1
```

验证环境创建后，persistent worker 保持关闭。先运行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-m16.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-contract.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-cn-fixture.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-m16-goods.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-m16-real-data.ps1
```

最后一条 preflight 是**非破坏性真实数据安全门禁**：它检查当前 M1.6 运行时、数据库、CN ingestion 锁、原始 ZIP/SHA、durable goods replay boundary，并明确输出是否允许开始真实重放。

只有这些 gate 通过后才开始真实 ZIP 重放。

## CN 真实数据重放

先确认 worker 未运行：

```powershell
docker compose stop worker
```

逐包或按既定顺序执行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-cn.ps1
```

失败后修复并重试：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-cn.ps1
```

M1.6 验收可使用：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-m16-acceptance.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-m16-goods-identity.ps1 -FileName 1999.zip
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-m16-monthly-patch.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-m16-real-data.ps1
```

完成真实数据验收前，不要启动 persistent worker。生产式自动扫描需要恢复时再执行：

```powershell
docker compose start worker
```

## US M1 本地导入

US M1 不在 parser/publisher 中保存 USPTO Open Data Portal 登录凭据。先把官方 Trademark Daily Applications XML 包放到：

```text
raw_data\incoming\us\apcYYMMDD.zip
```

然后执行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-us.ps1
```

脚本会先幂等应用 US M1 ClickHouse schema，再启动独立 one-shot worker。成功后权威源包移动到 `raw_data\archive\us`。再次运行同一命令会按 source rank 处理下一个 registered package。

如果某包失败或源文件缺失，普通 US continuation 会阻断，先执行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-us.ps1
```

US 与 CN 使用不同 PostgreSQL advisory lock，因此 US one-shot 不复用或削弱 CN guarded ingestion lock。详细约束见 `docs/US_M1_INGESTION.md`。

## CN 案件状态推理验证

推理层不改变官方事实。历史审计的时间基准来自**已加载数据覆盖截止日**，不是电脑当前日期；只有真实 `STATUS_CHANGED` transition 可提供商品失效时间证据。

先运行 preflight。只有报告中的 `safe_to_run_inference_audit = true` 时，才进入案件状态历史推理验证：

```powershell
docker compose stop worker
powershell.exe -ExecutionPolicy Bypass -File .\scripts\preflight-m16-real-data.ps1
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-cn-case-status-inference.ps1 -SamplePerRule 50
```

审计 V2 使用每条规则独立的 deterministic SHA-256 bottom-k 样本，不取扫描顺序中的“前 N 个”案件。

将审计 JSON 转为人工复核 CSV：

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\build-cn-case-status-review-packet.ps1 `
  -AuditPath .\reports\cn_case_status_inference_<timestamp>.json
```

人工依据 CNIPA 官方证据填写 `CONFIRMED` / `REJECTED` / `INSUFFICIENT_EVIDENCE` 后评分：

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\score-cn-case-status-review-packet.ps1 `
  -ReviewPath .\reports\cn_case_status_inference_<timestamp>_review.csv
```

评分只计算验证指标，**不会自动把 EMPIRICAL 规则升级为 VALIDATED**。

## API

- 控制台：`http://localhost:8080`
- API 文档：`http://localhost:8080/docs`
- 健康与发布版本：`GET /api/health`
- CN 字段结构：`GET /api/cn/schema`
- CN 数据汇总：`GET /api/cn/summary`
- CN 案件详情：`GET /api/cn/cases/{application_number}`

US API 查询端点将在 US publisher/真实包验收后加入；US M1 当前先冻结官方事实 ingestion contract。

## 数据边界

- 原始官方 source package 是权威来源；注册后以 SHA-256 识别，不只依赖文件名。
- staging 最长保留 7 天，成功发布后清理。
- 不保存每次运行的全量 Parquet/DuckDB 快照。
- CN 月更包未出现的案件或商品，不解释为删除。
- CN `FIRST_OBSERVED` 不等于状态变化发生日。
- US raw status/event/statement 不直接冒充 MarkOrbit 法律状态结论。
- 推理状态、推理原因、人工复核标签均不得覆盖官方事实层。
- 实证规则必须保留 model version、rule ID、confidence 与证据来源，并可重算、可撤销。

进一步阅读：

- `docs/ARCHITECTURE.md`
- `docs/CN_GOODS_LIFECYCLE_MODEL_V2.md`
- `docs/M1_6_REAL_DATA_PREFLIGHT.md`
- `docs/CN_CASE_STATUS_INFERENCE_MODEL_V1.md`
- `docs/CN_CASE_STATUS_INFERENCE_HISTORICAL_AUDIT.md`
- `docs/CN_CASE_STATUS_GROUND_TRUTH_REVIEW.md`
- `docs/US_M1_CORE_MODEL.md`
- `docs/US_M1_INGESTION.md`
