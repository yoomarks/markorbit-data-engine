# M20 当前授权链审计与首个消费切片准入

日期：2026-10-09（Asia/Shanghai）。任务：[DE-A2 / #881](https://github.com/yoomarks/markorbit-data-engine/issues/881)。

## 决议

复用 Core `EntitlementGrantV1` / `ResolvedEntitlementV1`。对于一个精确、经批准的五维 Scope，
版本化服务端映射到一个 BOOLEAN key 已能表达“有权 / 无权”；没有证据要求第二套 grant store。
Core 仍负责当前身份及授权解析，Gateway 负责可信业务上下文和消费入口准入，
Data Engine 负责事实、来源版本、查询限额和数据准备度。

首个候选为现有 **US 申请人 NAME 检索**：SEARCH、Portfolio 服务端上下文。
不把返回多类事实的 CN/US 单案接口直接命名为一个已批准的细粒度数据集。
具体 Dataset Family/版本、key、许可、currentness 和商业分配仍未批准；本报告不予代批。
下一实现任务已登记为 [MarkOrbit #1499 / M20-C1](https://github.com/yoomarks/markorbit/issues/1499)。

本次交付是代码审计、真实 Core 解析回放及实施验收边界。
没有改变任何运行时读接口，也没有实现或启用 M20 细粒度准入。

## 证据基线与真实路径

| 基线 | 精确版本 / 范围 |
| --- | --- |
| 产品 | 附件 Product Master Spec v1.1，M20；五维语义已批准，具体模型/Pack/许可/分配另批 |
| Data Engine | `d2d154444bd8271be2dc066a86cd5fe73fe1533d`；含 #880 描述元数据 |
| MarkOrbit | `86715f76d4a5b33839f431e4996a8a00b21c7918`；只读审查与本地 fixture 回放 |
| 现场 | 未连接生产主机或数据库；不把本地/CI 测试当作当前覆盖、许可或生产授权证据 |

| 实际代码 | 已有保证 | 当前 M20 缺口 |
| --- | --- | --- |
| Gateway `product-loop-http.ts`：`applicantQuery` | Session、Origin/CSRF、`workspace:read`；拒绝外部 `requestContext`，从 principal 构造 Workspace | 没有调用 data-use entitlement；身份校验不是数据用途授权 |
| Gateway `data-engine-applicant-discovery-http.ts` | 规范化查询、owner query/Workspace 回显匹配、envelope parser | 没有许可/currentness/admitted dataset 映射；本地 request context 不是 Core 许可证明 |
| Gateway `data-engine-route-support.ts` / `data-engine-http.ts` | Provider bearer、超时、request/correlation ID、合同/Owner 校验、错误传播 | 只转发 trace，不存在当前 grant/purpose attestation；未发现这些路径中的授权缓存 |
| Gateway `data-engine-product-http.ts` | Workspace 身份和读权限；CN/US 多类事实读取 | principal 在读前校验后未用于 M20 决策；不是此次单 Scope 实现范围 |
| Core `workspace-commercial-http.ts`：内部 entitlement resolve | Internal service secret；`currentWorkspaceAuthority.validate`；从当前 actor 构造 subject | `asOf` 由服务调用者提供；当前 authority 的版本结果没有随 `ResolvedEntitlementV1` 返回 |
| Core `current-workspace-authority.ts` | 查当前 user/workspace/membership、身份绑定、ACTIVE、读权限；可校验 expected versions | 调用者必须传递已知版本；源故障与冲突不能作为许可 |
| Core `workspace-commercial.ts`：`resolveEntitlement` | 按 `asOf` 取已记录最新 grant 版本、精确 subject/key、ACTIVE 和生效区间；返回 contributing refs | 是通用历史解析；不负责来源 licence、数据覆盖或 action/purpose 映射 |
| DE `integration_security.py` / `integration_api.py` | Service bearer、轮换、限流、GET 读接口 | 没有 Workspace/data-use entitlement；查询里的 Workspace 仅是上下文/游标绑定 |
| DE `us/applicant_owner_read.py` / candidate index | NAME 已有 bounded 索引查询、epoch readiness、读前后 epoch 一致性、cursor/query/snapshot 绑定 | 没有获准的许可/用途映射与 freshness 要求；epoch readiness 不等于数据已覆盖今天 |
| DE `data_trust.py` | 独立 queryable / complete / fresh / accepted / trusted-for-silence | 不能替代 Workspace entitlement，也不能从 queryable 推导许可 |

Core 内部入口为 `POST /internal/workspaces/:workspaceId/commercial/entitlements/resolve`。
Gateway 的公开 `GET /api/workspace-commercial/entitlements/:entitlementKey` 接受调用者 `asOf`，
属于合法历史查询。保持该行为；新消费准入直接使用内部入口并由可信服务选择当前授权时点。
资料的历史截至时间与授权评价时间必须分别传递和记录。

## 真实 Core 回放

使用 MarkOrbit 基线中原始 `WorkspaceCommercialServiceV1` 与已有 in-memory repository，
不是重写 resolver；仅创建 audit fixture，不写生产 grant。可重放脚本：`replay-m20-core.mjs`。

```sh
node --experimental-transform-types docs/audits/replay-m20-core.mjs /absolute/path/to/markorbit
```

脚本要求该 checkout 的 HEAD 等于上述基线，Node 24 原生 TypeScript 转换；不安装依赖、不访问数据库。
这是解析行为证据，不是 Core HTTP/Gateway 端到端验收、TypeScript 全工程检查或独立人工 review。

| 7 项回放 | 实际结果 |
| --- | --- |
| 当前 ACTIVE BOOLEAN true | 返回 true 与 grant v1 lineage |
| 相同 key、其他 Workspace | `NO_APPLICABLE_ENTITLEMENT` |
| 相同 Workspace、另一用途 key | `NO_APPLICABLE_ENTITLEMENT`；resolver 只认精确 key，不自动解释用途 |
| 10-05 已 recorded REVOKED v2，10-09 当前评价 | `NO_APPLICABLE_ENTITLEMENT` |
| 同一已撤销 grant，指定 10-01 历史评价 | 仍返回 ACTIVE v1 和历史 `resolvedAt`；不能复用为今天的许可 |
| `effectiveTo` 正好等于评价时刻 | 已不生效，`NO_APPLICABLE_ENTITLEMENT` |
| 当前 ACTIVE BOOLEAN false | resolver 成功返回 false；消费方必须检查值，HTTP 200 不代表允许 |

## 首个实现范围与最小合同

消费路径：`POST /api/data-engine/applicants/discover` →
`GET /api/v1/us/applicants/by-name` → `discover_applicants_by_name`。
限制为 US + NAME；精确规范化名字不是 fuzzy 搜索、验证法律身份或联系人营销许可。
复用 `ApplicantDiscoveryRequestV1` / `ApplicantDiscoveryEnvelopeV1`、Integration V1 transport/error，
不复制共享类型，不新增通用管理写口、授权数据库或自创 signed-token 协议。

| 输入/证据 | 唯一可信来源 | 实现规则 |
| --- | --- | --- |
| Subject / Workspace / member / known versions | Gateway 当前认证 principal + Core 当前 authority | 不接受浏览器给出的 subject；Core 冲突/故障 fail closed |
| Jurisdiction / dataset family & compatible read-model versions | Owner 批准的版本化服务端 Scope→key 映射 | 不用浏览器自由字符串、前缀通配或默认全开；缺映射即 unavailable |
| SEARCH / Portfolio | 此受控业务入口的服务端选择 | caller 不得更改 key/action/purpose；不附带 OPPORTUNITY_USE / Creator / 公共传播权 |
| 当前 entitlement instant | 可信服务端 clock | 与 source-history instant 分开；每页重新解析，不缓存许可结论 |
| 授权结果 | 原始 `ResolvedEntitlementV1` | 对齐 subject/key、当前 `resolvedAt`、BOOLEAN true 和 lineage；不另造 grant 真相 |
| 实际 source snapshot | Provider 当前 serving epoch、已有 source refs 和 cursor | 与商业授权的数据模型版本分开；动态 epoch 不要求每次重建 grant，但必须在获准兼容范围内 |
| Licence / permitted use / admission revision | 相应 Owner 获准记录 | 当前没有本次可验证的真实记录；不能从官方来源或 service bearer 推定许可 |
| Currentness / completeness / acceptance | Provider 实测覆盖 + 已批准消费要求，复用 Data Trust | `observed_at`/入库时刻不是源覆盖日期；未知不能伪造 fresh |

**Consumer enforcement 边界：** 最小切片在 Gateway 消费入口先解析当前 Core 权限，
再执行获准的有界查询；Provider 保留既有 service-protected fact API。
这不能宣称其他 consumer、所有 bearer 持有人或所有直读接口已经实行 M20。
上线前必须确认允许的 consumer/credential 入口清单、来源准入与绕过路径，未通过不启用生产。
若未来确需 Provider 独立验证 Workspace 决策，再由 owner task 证明合同缺口；本审计不先设计 token 系统。

**Source pinning 边界：** 既有后续 cursor 已绑定 query/Workspace/source version，且读前后检查 epoch。
首个无 cursor 请求的 epoch 当前由 Provider 选择；还没有消费准入与该 source 版本之间的绑定证据。
运行时实现须补齐获准范围与实际 snapshot 的检查，并在失败时禁止返回数据。
共享事实缓存可继续作为事实缓存，但每次交付前必须有新 Workspace 决策及准确版本检查。

## 负向验收矩阵与体验语义

以下是 M20-C1 待实施验收，不是声称本次全部已测试的运行时能力。

| Fixture / 触发 | 准入结果和必须证明的行为 |
| --- | --- |
| 未登录 / forged subject 或 requestContext / 当前 membership inactive | 拒绝；不向 Provider 请求事实；保留既有身份安全语义 |
| wrong Workspace / known authority version stale | 拒绝或 current-authority conflict；不泄漏其他 Workspace 决策/数据 |
| missing / revoked / expired grant，上一页还允许 | 当前页不准入；不能使用先前决策或历史时间复活权限 |
| BOOLEAN false / unknown key / incompatible value kind | 不准入；不能把 200 或非空 lineage 当成允许 |
| caller action/purpose/key/evaluation instant | 请求拒绝或由服务端固定并明确绑定；不扩权 |
| dataset/read-model version 不兼容 / snapshot 不匹配 | conflict/unavailable；不默默切换版本或降级成其他数据 |
| licence unknown / 不允许 Portfolio SEARCH | 不准入；记录许可缺失/限制原因，不显示“没有结果” |
| coverage unknown / stale / index 未 accepted / Core 或 Provider outage | unavailable/degraded；required scope 不可用则禁用/转人工 |
| Provider epoch 在读期间变化 / continuation snapshot 过期 | 复用 409 conflict / 503 unavailable 语义，不返回混合快照 |
| 其他 Workspace 已填充的响应缓存 / 重放旧决策 | 重新检查当前 Workspace 和版本；无授权事实交付 |
| scope/budget 超限 / rate limit / cursor 参数错误 | 保留现有 422 / 429 / 400 等语义和 retry 提示；不退回无限查询 |
| 允许 + 可查询覆盖下合法 0 结果 | 返回 query 和 snapshot 证据下的空集；不是法律身份或全世界不存在结论 |
| optional scope 缺失（后续 Capability） | 只有 Owner 声明允许才 reduced mode，并明确降低覆盖/置信度；首片不引入 Capability |

MO 的审计记录应解释 Workspace、scope mapping revision、当前评价时点、grant refs、
实际 snapshot、许可/准备度原因和结果；不要记录 bearer、原始名字、整个 body 或 grant 私密内容。
业务界面由产品 Owner 将这些原因转换为自然语言；用户不需要学习内部 key、receipt 或 fingerprint。
本次没有 UI 改动或浏览器验收。

## 成本、实施任务与验证

首片每次请求/翻页增加一次 Core evaluation，调用必须设超时；超时、故障和限流须分别保留，
不为 performance 缓存授权结果。现有 Provider NAME hard bounds 为 max 100 pages / 100 results，
owner-read settings 为 1 thread / 1,000,000 rows、overflow throw；不据此承诺生产延迟或新 SLA。
Core 当前 PostgreSQL `listGrants()` 读取全部 ENTITLEMENT_GRANT，再由 resolver 过滤 subject/key/time；
因此本次不能声称内部解析已为单 Scope 有界。正式准入前必须验证规模与查询成本，
需要优化时由 Core Owner 单独保证最新版本、撤销和历史语义，不在本审计中换写授权 SQL。
来源许可/readiness 若需要 preflight 或 receipt 扩展，先验证实际缺失字段，控制新增调用与日志。
评价时点、事实 snapshot 和交付时点应分别记录；跨服务请求没有已证明的原子撤销/读取保证，
不得把“每页当前评价”表述为授权和事实之间的强一致事务。

[M20-C1 / #1499](https://github.com/yoomarks/markorbit/issues/1499) 已列出 allowed directories、
共享合同、负向 fixtures、测试、bootstrap/prepush 与 closeout gates。
只处理 US NAME 消费准入，保留历史 entitlement read；生产默认缺获准记录即不可启用。
DE source metadata/pinning 缺口由其独立 Owner 切片处理；本次未修改 MarkOrbit 文件。
WIPO MGS #878、schema、采集、scheduler、生产运维、Pack/价格和其他业务用途不在此次变更内。

本地验证：原始 Core 解析回放 **7/7 passed**；DE 既有 scope descriptor、owner read、
来源 pinning、Data Trust 相关测试 **50 passed**。无新增 runtime/test 断言削弱，无新依赖。
提交前检查本次仅文档及其本地回放脚本；适用 hosted CI 和 exact-head 合并状态以关联 PR 为准。
本报告不能代替现场许可、覆盖、性能、当前身份/授权或生产部署验收。
