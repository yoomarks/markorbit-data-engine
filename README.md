# MarkOrbit Data Engine — M1.5

M1.5 是中国商标数据纵向切片的字段模型收口版。它以 PostgreSQL 管理控制面和 Entity Core，以 ClickHouse 保存中国商标案件、范围、主体关系、事件及派生关系；原始 ZIP 只在宿主机保留一份。

## 本版重点

- 完整申请号作为中国案件主键，一申请号对应一个案件；多类别保存在 `classes` 数组中。
- 中国直接申请和马德里指定中国均进入同一个中国案件体系。
- `G602365A` 解析为中国派生案件：根号 `G602365`、后缀 `A`、WIPO 国际注册号 `602365`。
- 基础文件 `1999.zip`、`2000.zip` 识别为按申请年份划分的 `BASE_PARTITION`，不是历史快照。
- `2023_1.zip` 识别为 `MONTHLY_PATCH`；月更来源优先于基础分片，导入时间不再决定当前事实。
- 永久保存颜色说明、放弃专用权说明、立体商标、共同申请、商标形式、地理标志、驰名标志等官方字段。
- 商品原始状态代码保留；未证实的 `0/1/2` 不擅自映射法律含义。
- 商品范围分别统计来源行、已解释有效、已解释无效、未映射状态；仅在全部状态可解释时提供 `effective_item_count`。
- 主体提及、确定性实体候选、案件主体当前关系及关系历史分层保存。
- 初审公告、注册公告、期限、商品范围、主体关系、派生案件生成可解释观察事件，并保存来源文件和逻辑行。
- 建立 `cn_case_relation_current` 与 `cn_scope_carve_out_current` 骨架；派生原因未知时明确保存 `UNKNOWN`，不虚构法律结论。
- 日期使用 `Date32`，并校验公告和期限是否早于申请日。
- 提供字段字典、案件检查、数量检查和 UTF-8 字段审计工具。

## 重要升级说明

M1.5 是结构性升级，不支持在 M1.4 测试库上原地迁移。升级时必须重建 PostgreSQL 和 ClickHouse 开发卷；`raw_data` 在宿主机目录中，不会被删除。

## 本地目录

```text
D:\yoomarks\markorbit-data-engine\
├── raw_data\
│   ├── incoming\cn\       # 待导入中国 ZIP
│   ├── archive\cn\        # 已成功导入的原 ZIP
│   ├── quarantine\
│   └── temp\
├── reports\
└── ...
```

## 安装或升级

1. 备份现有项目目录中的 `.env` 和 `raw_data`。
2. 将完整 M1.5 包解压覆盖到：

```text
D:\yoomarks\markorbit-data-engine
```

3. 若没有 `.env`：

```powershell
Copy-Item .env.example .env
```

4. 保持 Docker Desktop 和本地代理桥接运行，执行：

```powershell
cd D:\yoomarks\markorbit-data-engine
powershell.exe -ExecutionPolicy Bypass -File .\scripts\reset-m15.ps1
```

该脚本会删除开发数据库卷、重建镜像和数据库，但不会删除 `raw_data`。

5. 检查：

```powershell
Invoke-RestMethod http://localhost:8080/api/health | ConvertTo-Json -Depth 10
```

预期 `version` 为 `M1.5`，PostgreSQL 和 ClickHouse 均为 `ok`。

也可以执行完整运行时结构检查：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\validate-m15.ps1
```

## 重新回放测试数据

建议先暂停 Worker，手动逐包验收：

```powershell
docker compose stop worker
```

将归档中的原包复制回 `raw_data\incoming\cn`，建议顺序：

```text
1999.zip
2000.zip
2023_1.zip
```

每放入一个包，运行：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\run-cn.ps1
```

失败后修复代码再重试：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\retry-cn.ps1
```

成功后恢复 Worker：

```powershell
docker compose start worker
```

## 验收命令

汇总数量和商品解释状态：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\check-cn-counts.ps1
```

查看具体案件，包括 `G` 号和派生关系：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\inspect-cn-case.ps1 `
  -ApplicationNumber G602365A
```

导出完整字段审计：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\export-cn-field-audit.ps1
```

报告生成到：

```text
reports\cn_field_audit_<时间>\
```

审计脚本通过临时 SQL 文件和 `docker cp` 原样导出 UTF-8，不经过 PowerShell 文本管道二次转码。

## 控制台与 API

- 控制台：`http://localhost:8080`
- API 文档：`http://localhost:8080/docs`
- 字段结构：`GET /api/cn/schema`
- 数据汇总：`GET /api/cn/summary`
- 案件详情：`GET /api/cn/cases/{application_number}`

## 数据边界

- 原 ZIP 是权威来源，永久只保留一份。
- staging 最长保留 7 天，成功发布后立即清理。
- 不保存每次运行的全量 Parquet/DuckDB 快照。
- 月更包中未出现的案件，不解释为删除。
- 未证实的商品状态代码不映射法律含义。
- `A/B/AA` 后缀确定为案件派生结构；具体派生原因需由官方事件或范围证据确认。
- `G` 号仍是中国案件；WIPO 国际注册号只是跨库关联键。
- M1.5 只保存官方事实、结构关系和有证据等级的观察，不虚构最终法律状态。

详细字段见 `docs/CN_FIELD_DICTIONARY.md`，验收标准见 `docs/M1_5_ACCEPTANCE.md`。
