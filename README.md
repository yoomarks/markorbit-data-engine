# MarkOrbit Data Engine — M1.6

MarkOrbit Data Engine M1.6 是中国商标数据纵向重建版。PostgreSQL 管理数据包、任务、质量与实体控制面；ClickHouse 保存中国案件事实、主体关系、商品当前项、商品状态观察与完整范围生命周期；原始官方 ZIP 在宿主机只保留权威来源副本。

## M1.6 核心能力

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

## 本地目录

```text
D:\yoomarks\markorbit-data-engine\
├── raw_data\
│   ├── incoming\cn\       # 待导入/待重放 CN ZIP
│   ├── archive\cn\        # 已成功导入的权威原 ZIP
│   ├── quarantine\
│   └── temp\
├── reports\
└── ...
```

## M1.6 干净重建

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
```

只有这些 gate 通过后才开始真实 ZIP 重放。

## 真实数据重放

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
```

完成真实数据验收前，不要启动 persistent worker。生产式自动扫描需要恢复时再执行：

```powershell
docker compose start worker
```

## 案件状态推理验证

推理层不改变官方事实。历史审计的时间基准来自**已加载数据覆盖截止日**，不是电脑当前日期；只有真实 `STATUS_CHANGED` transition 可提供商品失效时间证据。

在稳定数据库快照上运行：

```powershell
docker compose stop worker
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
- 案件详情：`GET /api/cn/cases/{application_number}`

M1.6 的 summary 包含 durable goods item / observation / lifecycle 表数量；案件详情同时返回 class scopes、durable goods items 与 goods lifecycle。

## 数据边界

- 原始官方 ZIP 是权威来源，永久只保留一份权威副本。
- staging 最长保留 7 天，成功发布后清理。
- 不保存每次运行的全量 Parquet/DuckDB 快照。
- 月更包未出现的案件或商品，不解释为删除。
- 未经验证的商品状态语义不冒充法律结论。
- `FIRST_OBSERVED` 不等于状态变化发生日。
- 推理状态、推理原因、人工复核标签均不得覆盖官方事实层。
- 实证规则必须保留 model version、rule ID、confidence 与证据来源，并可重算、可撤销。

进一步阅读：

- `docs/ARCHITECTURE.md`
- `docs/CN_GOODS_LIFECYCLE_MODEL_V2.md`
- `docs/CN_CASE_STATUS_INFERENCE_MODEL_V1.md`
- `docs/CN_CASE_STATUS_INFERENCE_HISTORICAL_AUDIT.md`
- `docs/CN_CASE_STATUS_GROUND_TRUTH_REVIEW.md`
