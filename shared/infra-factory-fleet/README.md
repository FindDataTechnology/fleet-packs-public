# infra-factory-fleet — 工厂集群共用 pack（草案）

Owner 自用的三岗运行编制：**巡检岗 / 优化岗 / 报班岗**。对外称「工厂集群」；runtime/pack 标识 `infra-factory-fleet` 沿用 fleet 词，符合词表规则（舰队一词仅限历史运行时标识）。

**状态：起草稿（OpenSpec change `add-infra-factory-fleet` 组2，tasks 2.1–2.6）。本目录不发布上架**——发布是主会话批准后的动作（tasks 2.7），蓝图铸造属组4。

## 岗位表

| 岗位 id | 名称 | cadence（cron · tz=Asia/Shanghai） | 内联纪律 | 引用共用层纪律包（只 ref 不重写） |
|---|---|---|---|---|
| `if-sentinel` | 巡检岗 | `7,37 * * * *`（每半小时 :07/:37 起，避开整点） | sentinel-discipline | fleet-eval-sentinel、fleet-brief-report |
| `if-optimizer` | 优化岗 | `17 9,21 * * *`（每日 09:17 / 21:17） | optimizer-discipline | fleet-eval-sentinel |
| `if-shiftlead` | 报班岗 | `43 8 * * 1`（周一 08:43） | shiftlead-discipline | fleet-board-contribution |

三岗 `cadence.promptTemplate` 顶部统一写明：**「错峰与在册舰队时刻表待 fleet_list 实探终校，若撞刻后移」**（spec R1 错峰 ≥15min 的终校走 tasks 2.6 全量对照）。已知校核点：09:07（巡检）与 09:17（优化）同日仅隔 10 分钟——若与在册八舰叠刻或回合拖尾，终校时按注记把优化岗后移，不改 pack 结构。

## mcpServers 工具面对照（allowTools ⊆ 各 server 在册面）

| registryName | allowTools | 用途 | 排除项（红线） |
|---|---|---|---|
| infra-telemetry | gitops_sync / workloads_health / services_liveness / crawl_summary / fleet_cost | 巡检五聚合只读面 | —（服务本身只读） |
| fleet-architect | fleet_list / fleet_spec_read / fleet_turns_read | 在册舰队时刻表、回合记录 | **禁 fleet_apply / fleet_draft**（提案不落地） |
| blueprint-author | blueprint_drafts_list / blueprint_validate / blueprint_save | 新缺口草稿 + dry-run 合形 | 不铸造（validate 只 dry-run，save 只存草稿） |
| facet-market | facet_market_pack_browse / facet_market_pack_read | 查共用纪律包在册性 | 禁 pack_versions / pack_publish |
| actions-mcp | propose_action | 跨面动作提案 | 只提案，不执行 |

decision 面与 harness-constructor 同款处理：`escalate` / `report_status` / `decision_escalate_to_owner` 属岗位运行时自带能力，**不列入 mcpServers**。mcpServers 共 5 项，正好卡 PACK_LIMITS 上限（≤5）。

## 自动化政策线（与 pack notes、design D3 逐字一致）

- **自动窗口（批准即执行的可逆闭集，三类）**：重启指定 deployment、回滚 image tag 至上一版、暂停异常 cron → 提案标 `auto_window`。
- **红线（永远人工批准+人工执行，五类）**：新建部署、DB 迁移、权限或凭据变更、跨服务批量动作、删除类动作 → 提案标 `human_always`，**红线动作不在任何岗位 allowTools**（工具够不着）。
- paas 执行段落地前运行**降级档**：全部提案停在 proposed/approved，执行腿由 Owner dev 会话承担，如实记档；批准前零写动作=存在边界。

## 发布门自检清单（组2 交付口径）

- [x] persona / skills 正文无 `{{...}}` 占位符（仅 `cadence.promptTemplate` 携平台注入位 fleetContext，与在册各舰同款）。
- [x] 内联物件非空：skills=3、agents=3、mcpServers=5（发布门下限 ≥1）。
- [x] 三岗 persona 以引用共用层包名，不重写同类纪律文本（spec R1 场景「纪律引用而非复制」）。
- [x] allowTools 逐 server 核在册面：fleet-architect 面为 harness-constructor 在册五工具之真子集（已排除 fleet_apply/fleet_draft）；其余按 tasks 1.1/2.5 声明的闭集。
- [x] name ≤80 / description ≤2000 / notes ≤500（实测 302）/ 单技能 content ≤64KiB / tags ≤10 / mcpServers ≤5。
- [x] agents 无 model/baseUrl/apiKey/url 等禁键；岗位级模型分层待 `fleet-post-model` 落地，落地前回落 cell 默认（spec R1，不阻塞）。
- [ ] PACK_MAX_AGENTS 限额：本包 3 岗，生产限额 40——远低于。
- [ ] 发布时：作者 email 精确匹配 `operator@example.com`；`python3 ~/.agents/skills/facet-pack-builder/scripts/validate_manifest.py` 过真校验器；packId 回填 fleet-packs/README 台账。
- [ ] 发布本身**不在本任务**（组2 之外，主会话批准后执行）。

## 文件

- `manifest.json` — 铸造产物（skills 段 content 与 skills/*.md 逐字同步）。
- `skills/sentinel-discipline.md` — 巡检纪律：五类信号阈值判据 + 清单条目格式 {类别, 目标标识, 观测值, 阈值, as_of} + 单回合口径收窄（lex 巡线 8 连败教训）。
- `skills/optimizer-discipline.md` — 优化纪律：异常→四通道提案映射、必附条目引用、幻觉源名防御、政策线分级标注、批准前零写。
- `skills/shiftlead-discipline.md` — 报班纪律：周报 digest 三段、decision_queue 正门、禁私信替代。
