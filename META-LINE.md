# META-LINE —— 官方元线：用团队造团队

> 单一事实源。三支元线舰队、团队发布八段、共享台账、写面禁令与递归边界都以此为准。
> 变更：`add-meta-fleet-line`。规格：`openspec/specs/fleet-meta-line`、`fleet-team-release`、`fleet-infra-catalog`。

---

## 1. 为什么是三条舰队，不是一条大舰队

两条结构性冲突，都无法用提示词化解：

| 冲突 | 现象 | 后果 |
|---|---|---|
| **批次形状相反** | 构造是串行（一份 brief 推进到底），运维是扫掠（N 队分批取数） | 同一支舰队里两种形状互相挤占上下文预算 |
| **不能自证** | 造队的立场是「我的设计对」，评测的立场是「它可能不对」 | 同一支舰队既造又评，等于自己判自己的卷 |

串行形状有真机前例：2026-10-25 试点整条链一次走通——一句 NL → 构造师 **431 秒 one-shot** 出蓝图并停等批准 → 校验闸真抓出数据源幻觉（`news_api` / `newsdata_io` 非在册）→ 批准 → 铸造 v1 → 实例化 → 首轮真跑 77 秒、真实调通 `fd-info` 140+ 次。
而全量扫掠那一族（`mo-eval` / `mo-proposal`）全部死在 compaction 75.5 秒后的模型流挂死（`turn timed out`，usage 为 None）。
**差别不在"稳妥程度"，在于能不能跑完。**

---

## 2. 三支舰队

```
                     官方元线（三层，终止于 owner 拍板）
  ┌──────────────────┬──────────────────────┬────────────────────┐
  │ A 构造（串行）    │ B 运维（扫掠）        │ C 评测（禁自证）    │
  │ harness-constructor│ meta-optimizer      │ meta-grader        │
  │  架构岗  九段+编制 │  巡检岗  回合/报数    │  评测岗  gold 对拍  │
  │  技能岗  skills[]  │  修配岗  节奏/工具面  │  审计岗  auditor    │
  │  装配岗  三支柱    │  报班岗  证据+提案    │                    │
  └────────┬──────────┴──────────┬───────────┴─────────┬──────────┘
           │                     │                     │
           └─────────► 同一张「团队发布」单据 ◄─────────┘
                     （C 只读、只出结论，不写提案）
```

| 舰队 | 源 | 职责 | 单回合批次上限 | 节奏 |
|---|---|---|---|---|
| **A 构造** | `shared/harness-constructor` | brief → 团队发布（八段） | **1 支团队**（串行） | 周一 10:23 / 10:53 / 11:23 |
| **B 运维** | `blueprints/meta-optimizer` | 在册团队的过程观测与修复提案 | **≤5 支**的回合史 | 每日 09:07 / 09:47 / 10:27 |
| **C 评测** | `shared/meta-grader` | 按 gold 对拍产物、出偏离结论 | **≤3 支** | 每日 11:07 / 11:37 |

批次上限不是保守，是唯一能跑完的方式（见 §1 的 75 秒压缩挂死）。
节奏错峰 ≥30 分钟，理由同仓内其它舰队：同 cell 串行队列 + 单回合时限。

---

## 3. 写面禁令（三支一律）

三支舰队的 `mcpServers[].allowTools` **一律不含**：

- 发布类：`facet_market_pack_publish`、`facet_market_pack_visibility`、`facet_market_pack_curation`
- 实例化类：`fleet_apply`
- 启停类：`wanxing.agent.pause` / `resume` / `deploy`

提案通道能提交的是 `paas/lib/action-registry.js` 里登记的动作（实测 **22 个**），与构造相关的是 `facet.pack.publish`（上架）。**建队不在其中**——`fleet_apply` 不是 action，它是 fleet-architect MCP 的 owner 工具（工具描述：ALWAYS `fleet_draft` first，`fleet_apply` 只在主人明确批准后调）。所以落地是三步、两个主体：① 装配岗提 `facet.pack.publish`（唯一可提案的一步）→ 拍板队列 → 已批准执行通道上架；② **主人在驾驶舱装包建队**（`fleet_draft` → `fleet_apply`）；③ 新队生来 **all-paused**，由主人启动。

为什么必须写清这条边界：2026-11-14 拿发布对象去平台自带干跑（`POST /api/fleet-architect/draft`）时，三个岗只剩 `target persona/<id> does not exist (or is not live)` —— persona 随包注册，所以**先上包、后建队**；而原句「一切生效动作经 propose_action」会让人以为「提案批了队就跑起来了」，实际批完只是上架。

**唯一新增的读面工具**：`facet_market_pack_validate`（发布门干跑）。理由写在构造师 persona 里那段 307 秒空转的教训上——校验器存在却没授，于是提示词里只能写「本岗没有校验器，不要去找」。

三个名字必须分清，不许混成一团：

| 名字 | 状态 | 用途 |
|---|---|---|
| `fleet_draft` | **已授** | 编制段与数据源在册性的第一道闸（服务端自带校验） |
| `facet_market_pack_validate` | **本次授予** | 包级发布门干跑（`{valid, errors}` 原样转述） |
| `blueprint_validate` | **本部署未启用** | 真正不存在的那个，继续明令「不要去找」 |

### 3.1 报数的硬配额：3 条 / 720 分钟（决定一轮只能报一次，也决定一天能排几轮）

平台对 `report_status` 有洪泛闸（`paas/server/fleet-reports.js`：`FLOOD_MAX_PER_WINDOW = 3`，窗口 720 分钟，
单条正文 `REPORT_BODY_LIMIT = 8KB`）。这不是建议值，是**按岗计的硬上限**，而每个回合都要靠报数留痕。
所以两条推论必须写进 persona，而不是靠岗位自觉：

1. **一轮只报一次**。旧写法「先报数再补细节 + 收尾必 report_status」＝一轮两条，**第二条起仍会被拒**
   （首条已豁免，见下），且两条的内容本来就该合成一条。
   2026-11-13 真机实测（修复前）：turn 1631 的**第一条与第二条报数都被拒**，原文同一句——
   `Error: report limit reached (3 per 720 minutes) — next window at 2026-11-13T19:01:21.909Z`。
   该岗随后自己退到 `memory_write_page` 落了一条进度笔记（被拒后的正确自救动作，已写进三包 persona）。

   **这条豁免曾经是死的，2026-11-13 已修好并上产。** 原委：`checkFlood` 那行
   `if (roundReports === 0) return { ok: true }` 按 **`session_id` 判「同一轮」**，而 cron 岗的报数行
   全挂在同一个平台会话名下（`platform-<uuid>`，跨轮不变），回合自己却是 `cron-job_<jobId>--r<ts>`。
   于是 `roundReports` 一进窗口就恒等于上限，豁免永不成立，退化成「每 720 分钟硬上限 3 条、无轮豁免」。
   正确做法是把回合边界换成回合自身的起点——paas 侧已按此修好（`countRoundReports` 按
   `createdAt >= 回合 handle 的 startedAt` 数本轮，拿不到起点才退回会话计数），
   镜像 `sha-abb7e39` 起上产，`openspec/specs/fleet-report-delivery` 已收口。

   **修好之后本线怎么设计（2026-11-13 起）**：每轮的第一条报数**不再受限**，所以
   - 「一轮只报一次」仍是铁律——不是因为配额，而是因为第二条起仍按窗口计数，且明细本就该并进同一条；
   - 点火次数**不再**受 3/720 约束：一个岗在 12h 窗口里点 4 次，就会有 4 条首报，各自放行；
   - 本线仍把每岗每天 1 轮作为**自律**（成本与可读性），只是这条上限现在由本线定，不再由平台强加；
     M7 判据②因此改称「本线上限」，报错文案不再写「必被拒」。
2. **单岗每 12 小时最多 3 轮**——**这条现在是本线自律，不是平台边界**（修复前它是硬的）。
   三支舰队现状（2026-11-13 清单实测）：
   构造 `23 10 * * 1` / `53 10 * * 1` / `23 11 * * 1`（周节奏）、运维 `7 9 * * *` / `27 10 * * *`、
   评测 `7 11 * * *` / `37 11 * * *`——**每岗每天只点一次**，即 1 条报数/天。
   > 曾误写成「运维岗每日三轮全在上午窗口、正好贴顶」：那三轮是**三个不同岗**（盘账 09:07 / 提案 10:27 / 评测 11:07），
   > 各算各的配额，不构成任何单岗贴顶。**按舰队数轮次去比按岗配额，就是拿邻近信号冒充判据。**
   要加密节奏，现在**技术上不再撞墙**（每轮首条都放行），但请按 token 成本与「一次读得完」来定，
   而不是按配额余量来定。

同时订正一处旧错口径：`meta-optimizer` 的提案岗 persona 原写着「`report_status` 一回合最多调 **3 次**」——
把「每 12 小时 3 次」误读成「每回合 3 次」，照它写会让人**一轮**就把额度用尽。已改为 720 分钟窗口口径（v12）。

---

## 4. 「团队发布」八段

提案单位不是 manifest，是一个可审、可试跑、可回滚的对象。八段**全部显式存在**：没有内容就写空值，**不许用省略表达「没有」**（省略与空值必须可区分）。

```jsonc
{
  "releaseId": "<回填>",                 // 由执行通道铸，提案时为 null
  "manifest":  { /* 包三桶：persona / skills / mcp */ },
  "org":       { /* fleet 段：岗位 / 父边 / 节奏 / KPI 槽 */ },
  "memory":    { "store": {"home":"member"}, "read": {"scope":"own+shared"},
                 "write": {"authority":"private"}, "process": {"distill":"on"},
                 "guidance": { "<agentId>": "本岗记忆章程，≤600 字" } },
  "toolkit":   { "<agentId>": { "<registryName>": ["<tool>", "..."] } },
  "dashboard": { "board": [...], "posts": { "<agentId>": ["<投稿面>"] } },
  "acceptance":[ { "id":"...", "kind":"...", "target":"...", "expect":0, "rubric":"..." } ],
  "evidence":  { "prevVersion": 11, "turnHealth": {}, "reportLanded": {}, "evalDrift": [] },
  "cost":      { "turnsPerDay": 3, "tokensPerTurn": 60000, "note": "..." }
}
```

### 4.0 八段不是「九段草稿换个名字」

两个产物**不同形**，最容易在这里出错：蓝图草稿是九段（boundary/dataSources/fleet/agents/mcpServers/board/monitoring/acceptance/i18n，`blueprints/schema.md` 的契约），**提案单位**是上面这份八段对象。2026-11-14 实历：构造链把九段草稿写得很完整（三支柱 9 格＋技能桶 3 份），但草稿的 `acceptance` 用了一套自造的「A 结构 / B 行为 / C 边界 / D 数据 / E 交付」分类——**这五个名字不在 `kind` 闭集里**，按它铸造会被发布门拒；而仓里四份真蓝图的 `acceptance` 全是 `{id, kind, target?|expect?|rubric?}` 结构条目。同批还缺 `evidence` / `cost` 两段（九段里根本没有这两节）。

三处最常写错的形状，交对象前逐条自查：

| 位置 | 错法 | 正法 |
|---|---|---|
| `toolkit` | `{"agent-inspect": ["fd-memory.memory_write_page", …]}` 点号串 | 两层 `{agentId: {registryName: ["memory_write_page", …]}}`（`check_meta_line.py` M20 已能红） |
| `acceptance` | 自造类型名（A/B/C/D/E 型、结构/行为/边界…，或它们的英文名 structural/behavioral/…） | `kind` 只能写这五个字面量之一：`post-materialized`、`first-round-fired`、`report-landed`、`escalate-bounded`（带数值 `expect`）、`llm-judged`（带非空 `rubric`），其余带 `target`。**注意「五型」一词在段 8 与契约里指的不是同一组东西**——段 8 的 A–E 是人读分类，照它写 `kind` 会被 `check_release_object.py` 判 `kind_unsupported`（2026-11-14 实历：装配岗照段 8 写成 structural/behavioral/… 被判红） |
| `acceptance` 支柱条目 | 只写「应该怎样」 | 每条带 `counterCase`：一句话写清**什么样的坏设计会让它判不绿**；三支柱各至少一条 |

`evidence` / `cost` 不许省（首版 `evidence.prevVersion: null`；`cost` 写 `turnsPerDay`/`tokensPerTurn` 并标明实测还是估算）。判据脚本：`scripts/check_release_object.py`（S1–S5，自检 5/5）。

### 4.1 三支柱各须至少一条**能红的**断言

「能红」的判据：存在一个具体的坏设计会让它判不绿。

| 支柱 | 可判红的坏设计 | 断言落法（本版） |
|---|---|---|
| `memory` | 章程没组进 persona | 判据型：persona 尾段是否含「【记忆章程】」且与 manifest 一致 |
| `toolkit` | 运行时工具面超出声明 | 判据型 + 假宇宙直接断言（读生成物，不经模型） |
| `dashboard` | 投稿岗那一轮没产出 | 判据型：该轮是否落了可查的看板产物 |

**机制上的两个必填字段**（由 `scripts/check_release_object.py` 强制）：

- `pillar`：断言属于哪一柱，取值 `memory` / `toolkit` / `dashboard`。没有这个字段，
  「三柱各有一条」就只是一句无法机检的话。
- `counterCase`：**会让这条断言判不绿的坏设计**，一句话。没有它，断言无法证明自己能红
  ——「本团队的看板是好的」这类散文会被直接拒收。

示例（真实形状）：

```jsonc
{ "id": "toolface", "kind": "llm-judged", "pillar": "toolkit", "target": "样例岗",
  "rubric": "该岗运行时可见工具面是否 ⊆ 声明的 allowTools，且不含任何写工具",
  "counterCase": "运行时可见了声明之外的工具，或声明里混入了写工具" }
```

试跑引擎目前只有五类断言（`post-materialized` / `first-round-fired` / `report-landed` / `escalate-bounded` / `llm-judged`），**没有**三支柱的结构断言。所以本版用 `llm-judged` + rubric，并在条目上标为过渡形态；结构断言（`memory-charter-composed` / `toolface-narrowed` / `board-post-landed`）随平台侧变更升级。**不得以「引擎不支持」为由省略某一柱。**

**一处例外已经落地（2026-11-14，M20）**：`toolface-narrowed` 的**形状那一半**现在机检——逐岗 `resources.mcpTools` 必须是两层 map `{registryName: [裸工具名]}`，引用的服务必须已在包级 `mcpServers` 注册，工具名不得写点号（写成 `fd-memory.memory_write_page` 会被当成不存在的名字静默丢掉）。实现 `scripts/check_meta_line.py` 的 `check_toolface_shape`（自检两红一绿）。**它不判「收得够窄」**——面是否只含该岗动作所需仍是 `llm-judged` rubric，别把形状判据当收窄判据用。实历：装配岗把草稿工具面写成点号（名字查得对、形状是错的），而 M2 只判非空，值长什么样没有任何判据看。

### 4.2 空断言不得进入可售态

`acceptance` 为空 ⇒ 拒绝发布，理由「无可验收之实」。这条与试跑引擎「无断言清单不得判绿」（`server/blueprint-trial.js:187`）同源，发布侧不得放宽。

### 4.3 一次批准一个回滚点

一次发布产出可寻址的 `releaseId`。回滚 = **重发上一版发布对象**，不许手改实例。发布标识与八段一起可读，使「这一版当时承诺了什么」事后仍可查。

### 4.4 升版必须携带上一版证据

除首版外 `evidence` 非空，至少三项：上一版回合健康（成功/失败与连续失败）、报数达成（应报/缺报）、评测偏离。缺证据的升版拒绝——没有证据的版本变更无法回答「为什么改」。

---

## 5. 共享台账（三支共读一份）

按 `releaseId` 记录，字段固定六列：

| 字段 | 谁写 | 说明 |
|---|---|---|
| `releaseId` / `version` | A（构造） | 发布标识与版本 |
| `fleetId` / `packId` | A | 实例与包 |
| `assertions` | A 写、C 补 | 断言清单与逐条结论（绿/红/未取得） |
| `evidence` | B 填 | 回合健康、报数达成 |
| `evalDrift` | C 填 | 偏离项与 gold 来源 |
| `rollbackTo` | A | 上一版 `releaseId` |

**MUST NOT** 各自维护一份团队清单或版本事实。运维读团队统一走共享台账 + 既有舰队名册（`fleet_list`）。

> **物理落点（task 6.9 已定，2026-11-13）＝平台原生报数车道**：一行台账就是一条 `member_reports`
> 记录，六个字段落在 KPI 槽位上（`release_id` / `fleet` / `version` / `assertions` / `evidence` / `rollback_to`），
> 运维与评测通过 `fleet_turns_read` 与注入的集群上下文读，**不新开介质、不新授工具**。

### 5.1 为什么不是工作区追加文件，也不是 `wire-build` 看板

三选一不是偏好问题，是 6.8 真机演练量出来的：

| 介质 | 判据（实测） | 结论 |
|---|---|---|
| **报数车道**（`member_reports`） | turn 1628 撞 480 秒预算软停**仍然落库 2 条报数**（`r_1794553281909_sd7rmf`、`r_1794554928147_foybik`，槽位 `posts_chartered/tools_narrowed/boards_wired/catalog_misses` 齐全）；且报数本就经 `{{fleetContext}}` 注入下游、经 `fleet_turns_read` 可查 | **选它** |
| owner 工作区追加文件 | 同仓 §1 已记：靠「每轮写文件再读回」的岗位 40+ 轮涨到 56KB，**光读就烧完单回合预算**；6.8 里 `glob` 三次全部落空（文件根本没被写出）；且文件不跨舰队 | 否 |
| `wire-build` 看板 | 写一行要在构造方工具面授 `wire_artifact_*` 写工具，与 §3「元线不持发布/实例化写工具」直接冲突；为一张台账开写面，等于把元线唯一的权限边界拆掉 | 否 |

**这条决定同时是自我约束**：台账之所以可信，是因为它躺在**平台已经在记的东西**里——
报数是每个回合的强制产物，不依赖岗位记得去写文件，也不依赖任何新工具。

> 附带条件（下一轮再议，不阻塞归档）：报数槽位的值是 KPI 形状（`{value, delta?}`），
> 台账的 `assertions` / `evidence` 两列是长文本，需按「槽位存结论、`summary` 存明细」拆，
> 且单条 `summary` 有长度上限——超长时写「见 summary」指针而非塞进槽位。

---

## 6. 递归边界：三层，终止于 owner

评测舰队自身的产物质量由 **owner + 试跑断言**判定，**不设第四层**。

任何试图为评测方再加一层「元评测」的编制必须被拒绝并记为「递归越界」，而不是静默接受。理由：加第四层只会得到一个无限塔，且每层都要在同一个 cell 的串行队列里争预算。

---

## 7. 元线自身先过三支柱

元线的每个包都必须：声明 `memory` 四轴、每个岗位都有 `resources.mcpTools`、有 `board` 段。
**元线不要求别人做的事，自己先做。** 缺任一柱的元线包不视为可发布制品。

自举（构造舰队给自己出发布对象）同样走提案 → 批准 → 执行，**没有内部例外通道**。
若因自举必须先落后门而产生偏离，落一条**受权偏离记录**，且回退命令在记录里先验过。

---

## 8. 引用必须命中目录

`dataSources.ref`、`allowTools`、`paths` 里的每个 server 名，都必须命中
`shared/meta-catalog/catalog.json`。未命中时拒绝，并区分两种情形：

- 所有形态都不存在 ⇒ **真笔误**（会被静默落成 `awaiting-local-binding`）
- 存在于另一形态 ⇒ **形态错配**（`actions` vs `actions-mcp`、`facet-market` vs `facet-market-mcp`）

缺能力时写「需开户：建议接入 <能力描述>」，**不许用最接近的在册名代替**。

---

## 9. 「最好」的可测量定义

不做无法证伪的"最好"。三个数：

| 指标 | 怎么量 | 目标 |
|---|---|---|
| 构造命中率 | brief → trial 绿需几轮 | 中位 ≤3 轮，首轮绿率 ≥80% |
| 运维定位率 | 巡检结论 vs SQL 真值逐条对齐 | 回放集 100% |
| 三支柱覆盖率 | 发布对象三栏非空且各有断言 | 100% |

**gold 不是人标的，是 SQL 算的**：回合表里「应报/缺报/连败」是确定性的，agent 报的数与 SQL 算的数必须相等。这是零人工的 gold，抓的正是"报数优先"那一族的假绿。

---

## 10. 受权偏离记录

契约要求：自举若必须先落后门而产生偏离，落一条**受权偏离**，且回退命令在记录里先验过。
本变更（`add-meta-fleet-line`）逐条自查如下。

### 10.1 无偏离项

| 门 | 是否被绕过 | 判据 |
|---|---|---|
| 三支柱不自证不发布 | **否** | 三个包在发布前均已声明 `memory` 四轴、逐岗 `resources.mcpTools`、`board`；`check_meta_line.py` 对三包全绿，负向对照五臂全红 |
| 发布对象八段 / 三支柱断言 | **否** | `check_release_object.py --self-test` 5/5；八段与 `counterCase` 是结构断言，不是提示词纪律 |
| 写面禁令（元线不持 publish/apply/启停） | **否** | 三包 allowTools 经 `check_meta_line.py` M4 扫描零命中；发布动作本身由人经 `propose_action` 拍板 |
| 引用必须命中目录 | **否** | `gen_catalog.py` 的 `declaredNotExposed` 对三包零违例（构造岗 `facet_market_pack_validate` 落在注册处权威形态 `facet-market-mcp` 上） |
| 校验三名字分清 | **否** | 构造师 persona 显式写明三者状态；`blueprint_validate` 仍标「本部署未启用」 |

### 10.2 一处**主动更正**（不是偏离，但要留痕）

`meta-optimizer` 原 allowTools 含 `wire_artifact_doc_create`，而线上 `wire-build-mcp` 的注册处
工具面（15 件，2026-11-13 自报 healthy）**没有这件工具**。本变更把它换成实际存在的
`wire_artifact_record_view_create`。这不是绕门，而是本变更自己的目录门抓出的既有缺陷
（同类共 2 件工具 / 15 个包声明，已挂账 wire 线，不在本仓修）。

### 10.3 回退命令（已验，按 2026-11-13 发布后的实际版本）

回退单位是**一个包一个版本**，不是「整条线一起退」——三个包各自有上一版：

```bash
# 0) 前置核对：读当前三行市场事实（与 README 机器台账应逐字段一致）
python3 fleet-packs/scripts/check_drift.py --pack harness-constructor   # 期望 ✅ … 源=市场 v16
python3 fleet-packs/scripts/check_drift.py --pack meta-optimizer        # 期望 ✅ … 源=市场 v11
python3 fleet-packs/scripts/check_drift.py --pack meta-grader           # 期望 ✅ … 源=市场 v2
```

| 包 | 当前 | 回退目标 | 回退动作 |
|---|---|---|---|
| A 构造 | v16 | v15 | 按市场 v15 取下 manifest → `publish_pack.py --pack-id O30LA92kq1D5uuNIBGKsZQ` 重发 |
| B 运维 | v11 | v10 | 同上，`--pack-id gkHE4CsQHC2rEV0ZWziqWg` |
| C 评测 | v2 | v1 | 同上，`--pack-id 2uXkSlEByBS7Zzu4JkmdwA` |

**不依赖本地 `/tmp` 备份**（`/tmp` 不持久，且本轮 `/tmp` 里的备份是发布**前**的源，不是市场上一版）。
正式回退一律「以市场上一版为准反向回填源」，回填后重跑七闸再发。本变更的源在 finddata 根下，
根目录不是 git 仓，故没有 commit 可回退——回退点只有市场版本号。

### 10.4 明确不做的事

- 不在本变更内修复 12/14 个包声明的两件幻影工具（`wire_artifact_dashboard_create` /
  `wire_artifact_doc_create`，2026-11-13 目录实测；属 wire 线）。
- 不在本变更内新增 `fleet.release` gate 动作（属平台侧后续变更；回滚语义要先设计）。
- 不把评测方自己再挂一层评测（元线三层，终止于 owner）。

### 10.5 发布执行通道：提案→拍板→执行这条链对「发布级」动作走不到市场（2026-11-13 实测）

本变更 task 7.1 的写法是「发布经 `propose_action` 提案 → 拍板 → 执行」。实测结论：**这条链对
发布级动作止步于 `approved`，不会真的发出去**，不是配置问题，是 v1 的实现边界：

- `lib/action-registry.js:74`：`RISK.publish → tier "async"`；
- `server/routes/actions.js` 的 approve 分支对 async 档直接返回
  `{ state: "approved", resultSummary: "awaiting async approval" }`，注释写明
  `Publish tier: wait for async approval (not implemented in v1)`。

所以**不存在**一条能走通「提案 → 拍板 → 自动执行」的发布路径。若照抄 task 7.1 的字面去跑，
会得到一条 `approved` 记录和一个**没有发布**的市场——比不发布更坏，因为它留下了「已批准已执行」
的假象。

**本次实际做法（受权偏离，留痕）**：

1. 拍板＝用户在本变更上的明确授权（「退出 explore，开始实施」「你有项目的主导权」）。这是本次
   发布的批准依据，不是模型自批。
2. 执行＝**操作者通道** `fleet-packs/tooling/publish_pack.py`（`x-market-token` + `x-market-user`
   代理身份，作者由铸造环境注入，须持 `creators` 组）。这是 v9–v14 一直使用的同一条通道。
3. **不走** propose→approve：这条链在本档位不会执行，写一条 approved 记录等于伪造执行回执。
   等平台侧实现 async 执行器后，本条偏离作废，改回正规链路。
4. 每次发布前都跑了 `POST /api/packs/validate`（市场自己的包级发布门），三次均 `valid: True`。

发布结果（市场回读，与 `README.md` 机器台账逐字段一致）：

| 舰队 | packId | 版本 | 可见性 | 岗 | 技能 |
|---|---|---|---|---|---|
| A 构造 | `O30LA92kq1D5uuNIBGKsZQ` | **v16** | public | hc-architect / hc-skill-author / hc-assembler | 2 |
| B 运维 | `gkHE4CsQHC2rEV0ZWziqWg` | **v11** | private | mo-inventory / mo-proposal | 2 |
| C 评测 | `2uXkSlEByBS7Zzu4JkmdwA` | **v2** | private | mg-eval / mg-audit | 2 |

回退：三条都是版本通道回退上一版（A→v15、B→v10、C→v1），源侧以市场上一版为准反向回填。

### 10.6 `fd-memory` 未挂载 ⇒ 按「需开户」处理，不用它占位（2026-11-13）

评测舰队首发时把 `fd-memory` 写进了 `mcpServers`（`memory_query` / `memory_recent` /
`memory_overview`）。目录门抓住了这条：

```
❌ meta-grader  引用 'fd-memory' 在三种形态里都不存在（市场目录/部署基线/打包清单）
   ——装包会静默落在 awaiting-local-binding
```

查证：`fd-memory/src/mcp.ts` 里这三个工具**确实定义着**（`node --test
paas/scripts/test-auditor-tool-mounting.mjs` 3/3 绿），但平台没把它作为 MCP 服务挂出去——
所以「工具存在」与「装包能绑上」是两件事，前者不能替代后者。

处置：从 `manifest.json` 与 `blueprint.json` 的 `mcpServers` / 逐岗 `resources.mcpTools` /
`dataSources` 三处全部摘除，`notes` 写「需开户」；记忆柱改由 `memory` 四轴声明承担（记忆是
**声明式**的：`lib/pack-memory.js` 说明四个轴在装配期按身份解析命名空间，不靠一个 server 名）。
审计官工具面收缩为 `fleet_spec_read` / `fleet_turns_read` / `auditor_submit_verdict` 三件已挂载工具。

这是 §8「缺能力写需开户、不许用在册名里最接近的那个顶替」的第一次真实触发。
