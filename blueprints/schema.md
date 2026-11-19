# 垂直蓝图格式 v2（fleet-packs/blueprints/）

单一 JSON 文档声明一个垂直的完整构造面。**字段与既有契约同源**：`fleet` 段与壹座集群模板逐字段一致（可直接过 parseFleetTemplate）；纪律引用指向 L1 共用包；数据源只允许 registry 在册名或内部库逻辑名（禁端点/凭据）。构造师把自然语言编译成这份文档；批准后铸造链与八集群模板完全同路。

> **v2 变更（2026-10-28，跨仓对账）**：本格式的字段名与断言形状此前（v1）与壹座蓝图构造间**落地形状**不一致——v1 用 `vertical / sources / boards / acceptance:["自由文本"]`，平台侧落地为 `boundary / dataSources / board / acceptance:[{id,kind,…}]`。契约真源=本文件；平台侧形状以 `fleet-blueprint-studio` spec 为准，两者已逐字对齐。
>
> **平台侧 v1 归一化适配层已于 2026-10-29 摘除**（paas change `remove-blueprint-v1-compat`，12/12，strict 绿）。现在把 v1 蓝图粘进构造间**不再被静默兼容**——平台以一条多行错误明确拒绝，并给出 v1→v2 逐字段迁移指引（`vertical{name,tagline,non_goals[]}` → `boundary{name,oneLine,outOfScope}`、`sources[].registryName` → `dataSources[].ref`、`boards` → `board`、自由文本断言 → 五型结构化条目）。摘除依据：摘前实测生产库**仅 1 条蓝图且已是 v2 形状**（其余 cell 0 行），归一化层从未服务过真实数据，属纯技术债。

> **2026-11-08 可选扩展（不升 v3）**：新增两个**可选**键——`skills[]`（技能桶，技能正文随蓝图进铸造产物）与 `kind`（`team`/`agent` 文档类型）。两者缺席时行为与扩展前完全一致（`skills` 缺省即空桶，`kind` 缺省等价 `team`），存量蓝图零迁移。原 v1→v2 是破坏性改名才需要摘兼容层；本次是加键，故不升大版本。平台侧承载已就位（paas `add-agent-builder-workspace` 4.3，`server/blueprint-validate.js` 的 `assembleManifest` 照搬 `content.skills` 进 manifest），本文件此前漏记该两键。

## 字段（v2 · 权威）

```jsonc
{
  "kind": "team",                     // 可选：team（缺省，六段齐全）| agent（岗位文档型，见 §岗位文档型）
  "boundary": {                       // 板块边界
    "name": "…",                      // 垂直名（铸造时作场景主名，出「…集群（模板）」）
    "oneLine": "…",                   // 一句话用途（盯什么）
    "outOfScope": "…"                 // 明确不做什么（防编制蔓延，字符串，多个用顿号连接）
  },
  "dataSources": [                    // 数据来源引用（只读名单，无端点无凭据）
    // ref 字符约束（平台校验器原文）：^[a-z0-9][a-z0-9._-]{0,63}$（大小写不敏感）
    // = 在册 server 名或内部库逻辑名；不是 URL、不嵌端口、不带凭据。
    { "ref": "fd-info", "note": "政策原文与部委公告" }
  ],
  "fleet": { /* 与壹座集群模板 fleet 段逐字段一致（v1 起未变）：
        members[]（ref/title/parentRef/target{kind:pack-preset,presetId}/
        kpiSlots/cadence{cron,tz,promptTemplate}）+
        orchestration{mode,edges[]} + 可选 i18n{en:{…}}。
        纪律来源=岗位 persona 文本引用 L1 包名；
        节奏错峰 ≥30min；promptTemplate 可含 {{fleetContext}} */ },
  "board": [                          // 产物骨架（v2：键名 board，值为数组）
    { "artifact": "doc|dashboard|form|record-view", "title": "…",
      "discipline": "draft+提案制（引用 fleet-board-contribution）" }
  ],
  "monitoring": {                     // 监控条件
    "anomaly_criteria": ["…"],        // 什么算异常（岗位判据）
    "escalate": { "threshold": "…", "severity_default": "high" },
    "notify": "proposal-channel"      // 通知一律走拍板/提案通道
  },
  "acceptance": [                     // 真跑断言清单（v2：结构化条目，非自由文本）
    { "id": "a1", "kind": "post-materialized", "target": "监视位" },
    { "id": "a2", "kind": "first-round-fired" },
    { "id": "a3", "kind": "report-landed", "target": "合成位" },
    { "id": "a4", "kind": "escalate-bounded", "expect": 3 },
    { "id": "a5", "kind": "llm-judged", "target": "合成位",
      "rubric": "日报合成且缺位如实标注「未收到」，无编造条目" }
  ],
  "agents": [ /* 内联岗位 persona（至少一份内联件：skills/agents/mcpServers/bots 任一非空，
                 否则铸造过不了发布门「a pack must contain at least one skill, MCP
                 reference, agent, or bot」） */ ],
  "skills": [                         // 可选：技能桶（<name,description,content> 与 manifest 技能段同形）
    { "name": "ontology-draft-deterministic-rules",
      "description": "草案自洽五判据：…",
      "content": "---\nname: …\ndescription: …\n---\n\n# …\n" }
  ],
  "mcpServers": [ { "registryName": "fd-info", "allowTools": ["fetch_announcements"] } ],
  "i18n": { "en": { "name": "…", "description": "…" } },
  "_casting": { "source_blueprint": "…", "approved_at": null,
                "packDependencies": { } }   // 铸造时回填
}
```

## 断言五型（kind 闭集 · 未知 kind 一律拒）

| kind | 语义 | 额外字段 |
|---|---|---|
| `post-materialized` | 岗位已物化（任务落地、节律在册） | `target?` 岗位标题 |
| `first-round-fired` | 首轮回合真实触发并完成 | `target?` |
| `report-landed` | 报数槽位落库且键与模板 kpiSlots 对齐 | `target?` |
| `escalate-bounded` | 拍板项数量不超过预期上限 | `target?`、`expect` 数值 |
| `llm-judged` | 自由语义判据，交 judge 车道按 `rubric` 三态判定（pass / fail / insufficient-evidence） | `target?`、`rubric` 非空原文 |

**机器判四型 + 模型判一型**。可售态的唯一真源仍是确定性：`llm-judged` 判绿还须**主人对报告签字**（双钥匙），且判据模型不得来自该蓝图自己的岗位编制（禁自证）、判定只喂已收证据不喂自述、单次试跑的 `llm-judged` 条数与证据字符均设上限（超限如实拒，不静默截断）。详见 paas change `add-blueprint-llm-assertions`。

> 收 `free-text` 这一 kind 的平台是**旧版**；v2 蓝图不得再写自由文本串——v1 的自由文本会被归一成 `kind:"llm-judged"`（rubric 即原文）或如实拒，视平台版本。

## 技能桶 `skills[]`（2026-11-08 可选扩展）

技能桶是**能力的实际载体**：编制（`fleet`）、岗位文本（`agents`）、工具面（`mcpServers`）都只是装配，真正决定一支团队能干什么的是技能正文。此前本文件未记录该键，导致构造师不知道可以写技能正文——四份存量蓝图 `skills` 实测全为 0，构造师只能重组已有零件。

- **形状与上限与 pack manifest 技能段完全一致**：`[{name, description, content}]`，沿用 `PACK_LIMITS`（≤10 份，单份 `content` ≤64KiB）。不引入第二套字段名，不另立校验器——校验走既有 `validatePackManifest` 技能段。
- **`content` 是完整技能文档**：带 `---\nname/description\n---` frontmatter 的 Markdown 正文，须含可执行工作流、职责边界、输出契约；只写目标不写判据的技能视为未完成。
- **随蓝图进铸造产物**：`assembleManifest` 照搬 `content.skills` 进 manifest（平台侧 4.3），`cast_fleet_template.py` 同样直搬。缺省（不写该键）= 空桶，与扩展前行为一致。
- **内容红线门**：技能正文与 persona 文本在铸造前过红线扫描（泄密类：内部仓路径 / change ID / 凭据形态 / 内网域与私网 IP；越界指令类：忽略宿主纪律、绕过批准直接写、隐藏或歪曲上报）。命中即阻断，报「技能名 + 行号 + 片段 + 修法」。

## 岗位文档型 `kind`（2026-11-08 可选扩展）

`kind` 显式声明文档是**团队**还是**单个智能体**，缺省等价 `team`（存量文档零迁移）。

| `kind` | 必填段 | 铸造产物 | 说明 |
|---|---|---|---|
| `team`（缺省） | `boundary` / `dataSources` / `fleet` / `board` / `monitoring` / `acceptance` | 团队模板包（`category: fleet`） | 今天的默认形态 |
| `agent` | `name` / `description` / `agents` 或 `skills`（至少一份内联件） | 单智能体文档（`category: agent`） | 名称与一句话描述取顶层 `content.name` / `content.description`；`boundary`/`fleet`/`acceptance` 缺席不报错 |

两种类型共用同一套校验链：`boundary`/`dataSources`/`acceptance`/凭据/清单/判据段两 kind 全跑（缺席即空跑，出现即照检），仅 `fleet` 段在 `agent` 型跳过 `parseFleetTemplate`（无编制可解）。真源=`paas/server/blueprint-validate.js` 的 `validateBlueprint` kind 分支。

## 八集群模板共性提炼注记

| 字段 | 提炼自 | 被验证的形态 |
|---|---|---|
| fleet.members[].persona 引用 L1 包 | 全部八集群模板 | 纪律包单一源、岗位文本零重复 |
| orchestration edges | 情报/政心/法规/营销四舰 | trigger+handoff 链、event 唤醒全链自驱实证 |
| cadence 错峰 ≥30min | 营销舰验收 | 密集节奏互踩 runtime 的教训 |
| kpiSlots 与报数键逐字对齐 | 情报/营销舰 | report_status 槽位纪律 |
| allowTools ⊆ 商业集 | 发布门 toolGate | wire-build 命名与窄面教训 |
| 只读+提案制 | 谦面/萬星运营舰 | 策写治理结构性排除（allowTools 级） |
| monitoring.escalate 走拍板 | 谦面舰 | 5 项拍板项真实值守 |
| 内联物件下限 | 平台发布门 | skills/agents/mcpServers/bots 至少一项非空 |

## 多语言显示（i18n overlay，paas add-pack-locale-tracks 对齐）

**中文是主轨**：本蓝图所有显示字段（`boundary.name/oneLine`、`board[].title`、`fleet.name`、
`members[].title`、`kpiSlots[].label`）的字符串即中文，必填。要出英文副轨，用叠加块——
基字段形状零改、缺键一律回落中文、不写回落表：

- **蓝图级**（铸造时编译进成品 manifest 的顶层 `i18n`；形状与 paas pack manifest 同构）：
  `"i18n": { "en": { "name": "<boundary.name 的英译>", "description": "<oneLine 的英译>" } }`
- **fleet 级**（`fleet` 段随铸造原样进入 manifest，由 `parseFleetTemplate` 校验）：
  `"fleet": { "i18n": { "en": { "name": "…", "members": { "<ref>": { "title": "…",
  "kpiSlots": { "<slotKey>": { "label": "…" } } } } } } }`

不本地化：`dataSources[].ref`、`mcpServers[].allowTools`、`fleet.members[]/cadence{}/kpiSlots[]/
orchestration.edges[]` 等结构字段，以及 `cadence.promptTemplate`、persona 等内容层文本
（主人负责权责与效果，翻译另行立项）。发布门对成品 manifest 逐字段点名校验
（未声明 ref/槽位、`zh` 块、半步散件、3:1 长度比率、en 禁辞同扫）；本蓝图书只约束构造师
编译时的对齐件，不单独过 paas 校验器。

## 历史：v1 字段对照（仅供人读迁移指引，平台不再接受）

| v1 | v2 | 迁移做法（人手改，平台不再自动转） |
|---|---|---|
| `vertical{name,tagline,non_goals[]}` | `boundary{name,oneLine,outOfScope}` | tagline→oneLine；non_goals 数组按「、」拼成字符串 |
| `sources[{registryName,purpose,allowTools}]` | `dataSources[{ref,note?}]` | registryName→ref；purpose→note；allowTools 丢弃（工具面声明走 mcpServers） |
| `boards[]` | `board[]` | 仅键名对齐，值原样 |
| `acceptance:["自由文本"]` | `acceptance:[{id,kind,…}]` | 逐条改写为五型之一；语义判据用 `llm-judged` + `rubric`（原文搬进 rubric） |

**v1 蓝图现在会被平台明确拒绝**（一条多行错误 + 上述迁移指引），不会静默转写、也不会静默变空壳。要复用 v1 资产，按上表人手改一遍。

现存 v1 样例：`cross-border-ecom-policy/blueprint.json`（L3 首单试点，真跑验收证据在案，**保持 v1 原样存档不转写**——它是历史证据，不是模板）；`sample-backfill/blueprint.json` 同。
