# fleet-packs — 智能体团队 pack 铸造源

跨越五条产品线的**智能体团队**（agent team）pack 铸造源。仓内是源，
[市场](https://facet.finddatatech.cloud)是唯一分发更新通道。

一个 pack 打包一支团队：岗位（谁在什么节奏上做什么）、工具面（能动用什么）、
记忆章程（记得住什么）、看板（主人能看见什么）。69 份在架 pack 全部在此铸造。

## 从这里开始

| 你想 | 看 |
|---|---|
| 了解一份 pack 长什么样 | `shared/fleet-brief-report/`（最小：单技能包）、`erp/erp-invoice-fleet/`（团队：三岗 + 编排边） |
| 自己写一份 | `tooling/cast_fleet_template.py`（模板铸造）、`tooling/validate_manifest.py`（发布前校验，与市场同一道门） |
| 知道「什么样的包算合格」 | `bash scripts/check_meta_line_all.sh` —— 十一道具名判据，每道都带负向对照（证明它真能红） |
| 看行业蓝图的形状 | `blueprints/schema.md` |

判据的立场值得说明：**每道闸都必须有负向对照**。一条判据如果从没红过，它就不是判据，
只是一句祝愿。本仓所有闸都能被注入坏输入并当场红给你看。

## 元线自检：一条命令，任何地方可跑

```bash
bash scripts/check_meta_line_all.sh          # 十一道具名判据，每道自带负向对照
```

需要 `python3` 与 `node`，**不需要网络、不需要凭据、不需要兄弟仓、不要求 clone 目录叫什么**。
脚本自己会打印本次用的是哪份裁决引擎与漂移臂跑没跑（见下）。

| 闸 | 证明什么 |
|---|---|
| E1 / E2 | 三支舰队职责互斥、批次形状、写面禁令、报数配额与口径、节奏；E2 是 E1 的 **13 臂**负向对照（含「判不了≠通过」「引用旧口径来反驳不得误判」「有 cron 无入边不得误判」三条反向臂） |
| E1' | 三份发布对象的八段结构、三支柱、`kind` 闭集、证据字段；负向对照 **5 臂** |
| E1'' | 构造师产物（v2 垂直蓝图）的结构：段齐/未知段、boundary 三段、`dataSources` 与 `mcpServers` 引用**命中条线能力目录**、board 四型闭集、monitoring 判据与拍板通道、acceptance 五型与 `rubric`/`expect`、成员 cron 错峰 ≥30 分钟、内联物件下限；v1 存档必须显式登记。负向对照 **20 臂** |
| E3 | 假宇宙：3 份 brief 好证据全绿 + 12 条坏设计各判红 + 9 条判据**边界**（等号两侧、窗口起点、空匹配）。无独立负向对照——坏设计注入本身就是它的负向对照 |
| E4 / E4' | 回放对齐器对真语料算出的数与 SQL 真值一致；E4' 是对齐器的 **3 臂**负向对照 |
| 目录门 | 条线能力目录不陈旧、无手写增补、随仓快照未被手改（按 `shared/meta-catalog/inputs/_provenance.json` 的 sha256 复核）；负向对照 **4 臂** |

**E3 的裁决引擎是随仓钉住的**（`shared/meta-catalog/pinned/blueprint-trial-engine.mjs`，
转录自 `paas@9905c54fa` 的 `evaluateTrial` / `normalizeAcceptance`，出处与 blob sha 写在文件头）。
为什么不在闸里直接 import paas：那两个函数体是纯的，但所在文件顶部 import 了 cron/catalog/
pack-store/provisioner/DB/llm-providers，import 一次就拉整棵 paas 运行图——那样这道闸只在
「有 paas 邻居的作者机」上绿，干净 clone 直接 `ERR_MODULE_NOT_FOUND`。

钉住的风险由**漂移臂**兜住：机器上能解析到 paas 检出时（`--paas-root <dir>` / `FP_PAAS_ROOT` /
同级 `paas/`），同一批例子在两份引擎上各跑一遍逐条比 verdict（含 `detail` 文案）、断言闭集、
llm-judged 上限；任何一处不同即判红并点名。**没有 paas 时脚本明确打印「漂移未验」，不当绿。**

外部验证者的标准动作（两档都要跑）：

```bash
git clone <repo> /tmp/fp && cd /tmp/fp && bash scripts/check_meta_line_all.sh   # 文件系统隔离
env -u FACET_INTERNAL_TOKEN -u KUBECONFIG HTTPS_PROXY=http://127.0.0.1:1 \
    HTTP_PROXY=http://127.0.0.1:1 bash scripts/check_meta_line_all.sh           # 再清环境
```

## 运行时一致性（需要集群访问，不在上面九道里）

上面九道全部离线可跑，因此**证明不了「包发布了、线上真有那支队」**。这一层单独一条命令：

```bash
FP_PLATFORM_BASE=http://<platform NodePort 端点>:3100 python3 scripts/check_live_fleets.py --detail
```

它比「三支元线舰队（构造/运维/评测）的线上实况」与「包里的 `fleet.members`」：
存在与 `state=active`、岗位集合逐一对位、每岗 cron 一致、失败岗点名并附最近回合的 `errorKind`。
岗位按 `kpiSlots` 对位——线上成员的 `id` 是平台 id、标题会随文案改，只有槽位键是契约。

为什么必须有这一条：**装包不建队**。2026-11-13 实测的线上状态是——构造师包已到 v21/三岗
而线上仍是两岗（缺装配岗）、优化团队包已到 v12/两岗而线上仍挂着评测岗（违反「运维与评测
不得同居一队」）、评测舰队**线上根本不存在**；且构造师的技能作者岗 4 个回合全失败
（context pressure 2–4 次压缩）、**从未有过一条报数**。这些在任何离线闸里都看不见，
因为离线闸比的都是仓里的东西。

2026-11-14：三支元线**都已对齐到包**（`live-fleets 绿`）。做的事按顺序是：重铸并发布
`meta-grader` v4/v5（清掉占位符 `{{PACK_ID:meta-grader}}`）、把盘上那份同名旧技能
`meta-eval-gold-discipline`（描述还是「gold 来源由编制声明」，排序反了）删掉再装包，让两份
技能归本包所有——否则装包会 skip、评测岗绑到旧正文上；建队 `评测舰队`（两岗，
`nextRunAt` 分别为 11:07 / 11:37 CST）；摘掉优化团队的评测岗与它那 3 条边（`reconcile` 当场
回收了它的定时任务）；把优化团队从装 v9 提到 v12、构造师从 v16 提到 v21，并把盘账岗的成员
节律改成包里那份（线上还留着 v2 时代的「落 board 草稿」——正是台账里记着「因找不存在的
board 工具烧光输出预算」的那个坑）。

**两处判据方向被实测改正，别再照旧版改**：① 「有 cron 无入边＝永不点火」是错的——平台上
`reconcileFleet` 对每个有 `cadence` 的成员建定时任务，与编导边无关；审计官更是被编排校验
显式拒绝当边端点（`auditor_not_orchestratable`：审计官不进工作图）。第一版判据据此把
meta-grader 判红、并去加了一条边，发布时被市场拒掉。② 空 `nextRunAt` 不是「不会被叫醒」
——老任务行（`task_*` 前缀）本来就不带它，其中两岗近期真的跑过。所以本脚本只判 `taskId`。

**2026-11-14 09:33 第三支柱通电（真跑证据，不是推断）**：评测岗 turn 7726 `ok`，报告落库
`r_1794620505277_20l2zu`，槽位 `teams_graded=2 / deviations=4 / insufficient_evidence=1`，
正文写明 gold 取自回合表（注明窗口与 `limit=10`）、两队均未参与自身 gold。它报出的四条偏离里
有两条是**平台自己**的故障（`turn 7717 interrupted`、`turn 7718 budget soft stop 480s/600s`）
——评测岗开始能看见运维看不见的东西，这才是它存在的理由。

**2026-11-14 15:4x 三支舰队的六岗全部真跑过一遍（同一天第三次通电）**：盘账岗 438s、
提案岗 476s、装配岗 243s、技能作者 356s（上午）、评测岗 524s、审计岗 531s，全部 `done`。
其中**装配岗是构造链第三跳的首次成功**（此前物化作业 `history` 为空、草稿里三支柱一格没有），
**提案岗是本队那个预算缺陷的自己诊断者**（见台账 v14 条）。同轮实测两条判读纪律：

- **`member.taskId` 不会自己重指**。技能作者与提案岗的 `taskId` 仍指向 11-10 的
  `task_*` 老行，所以 `evidence.nextRunAt` 恒为 null、live 闸只报 ℹ️；而**它们真正在跑的是
  同名 `job_*` 行**（两岗本轮都由 `job_*` 点火成功）。PATCH 成员 cadence 时 reconcile 只在
  需要重写任务行时才回填 `taskId`（装配岗 `updated: [job_…]`、两岗 `updated: []`），
  所以「删掉那条老任务行」不但不会让闸转绿，还会留下一个指向不存在 id 的字符串——
  **这一项的正确处置是留档，不是清理**。
- **架构师那三个自造键（`draft_complete`/`inline_agents`/`mcp_servers`）是 11-10 的旧报数**，
  不是 v22 没生效：该岗最新两条报数时间戳都是 2026-11-10，之后的轮次没有新报数落库。
  判「正文改完有没有用」先看报数的 `at`，别看闸的 ⚠️ 有没有消失。

验收这三件事，缺一条就不算通电（按顺序，前一条不成立时后一条没有意义）：
1. `GET /api/cron` 里该岗 job 的 `state` 走完 `queued → running → done`；
2. `GET /api/fleets/<id>/turns` 里出现新回合且 `status=ok`（`failed/timeout` 时看 `detail`）；
3. `GET /api/fleets/<id>/members/<mid>/reports` **非空**，且 `slots[].value` 与正文数字逐字对得上。
——只看前两条会得到「跑完了所以成了」的假绿：前三轮全是 `ok` 且 `report: null`。

**为什么前三轮跑完却零报数（三层原因，各自独立）**：① persona 要 SQL 而工具面没有（v6）；
② 回合预算默认 10 分钟、不到 480s 就被 soft stop（v7 把 `turnTimeoutMs` 写进包）；
③ **包自己把报数口筛掉了**——`effectiveMcpTools()` 对声明了 `fleet` 的包做 `allowTools ∩
role.mcpTools`，本包只写了 `auditor_submit_verdict`，`report_status` 因此不在面上（v9 补，
E1 的 M13 判据；对照证据：优化团队 `mo-inventory` 没声明 `fleet`，反而拿到全量面并成功报数）。

`check_live_fleets.py` 的四条判据现在不含「岗位 prompt 是否等于包里那份」——那一层本次是
手核的（三支 7 岗逐字对过，0 处不一致）。要把「人读新、机器跑旧」也做成闸，需要平台给出
成员 persona 的读面（现在 `/api/catalog` 只回 id/name，不回正文）。

脚本**只读**：不建队、不改 cron、不重装——修复会改变线上智能体的行为与花费，属产品侧拍板。

**2026-11-14 补第 L7 条：最近一次执行成不成（只报事实，不动退出码）。** 起因是同一天实测
到两轮**静默丢失**：评测岗 04:07:07Z 的一轮只跑了 **2ms**（`the agent runtime was restarting
when this slot was claimed — the round did not run`），审计岗 04:04:15Z `Connection error.`
（92s）——两岗当天各丢一整轮，而 `live-fleets` 全绿。为什么绿：`status` 的容差窗是 **48 小时**，
所以「今天这一轮没了」在结构面根本看不见。判据只认「该作业自己 `history` 末尾」，逐岗打印
`⚠️ … 最近一次执行**没成** —— <state>/succ/<dur>/<time>；<error>` 加连续非成功次数与上一次
成功时刻。**刻意不计入退出码**：瞬时错（连接、runtime 重启）不占红，否则闸会常年红；结构性
成因（预算软停、上下文压力）已由离线闸 M18/M15 静态拦。

写这一条时自己踩了两个坑，都留了注释防复发：
- **必须逐岗判**：首版写成舰队级（在岗位循环外），`st` 只会是循环里最后一个岗位，于是整队
  只报一个人——评测岗那轮 `interrupted` 就这么漏了。
- **全空要当「判不出」**：平台刚重启时任务索引尚未加载，`taskId` 会**全部**为空，同一刻
  `/api/cron` 也读不到同名作业行（实测 7 岗同时 ❌，30 秒后重跑全绿）。现在全空时额外打印
  一句「先当判不出看，重跑一次」，判据本身不变绿。

**同轮查清一条结构缺口（未修，属平台侧）**：`interrupted` 的补跑**不会自动发生**。
`fleet-runs.js` 只在 `sweepOpenRuns()` 里调 `onInterruptedRun`，而该 sweep 只遍历
`db.listOpenRuns({before: now - graceMs})`——**只含仍 open 的 header**；而「兑现时才被
中断」的轮次由 `settle()` 当场定稿成 `partial`，于是永远进不了 sweep。证据三条：
`run_3c22d0cac3b14732bf95`（`rootSource: cron`、`rootMemberId` 与作业 `sourceTag` 完全一致、
`turns: 1`、`durationMs: 2`、`status: partial`）；8 小时日志里**没有**该 run id 的任何判词
（而 `not made up` 共 12 条，全是别的 run）；该 fleet 之后没有新的 评测岗 run。
候选修法：在 `settle()` 定稿 cron 源 header 且 `okCount === 0` 时也调一次 `onInterruptedRun`
（或让 sweep 候选集包含宽限窗内刚定稿的 header）——这要单测 + 出片，不能混进本仓这一轮。

第二档才是外部验证者看到的形态：注册处不可达时目录门会打印具名告警（含「工具面漂移未比对，
不是无漂移」），但不虚报漂移——**弱来源比出来的差异不是漂移**。

## 目录约定

```
fleet-packs/
├── shared/                  # 跨板块共用 pack（所有舰队都可依赖，改前看下「边界」）
│   └── <pack-name>/
│       ├── skills/<skill-name>/SKILL.md   # 技能源（单文档技能）
│       └── manifest.json                  # 铸造产物（源=skills/，发布即用）
├── lex/                     # 识律线舰队模板与专属 pack（add-lex-line-fleets）
├── wire/                    # 柏讯线舰队模板与专属 pack（fd-wire 会话产出）
├── marketing/               # 营销线舰队模板与专属 pack
└── README.md                # 本文件（单一事实源）
```

**边界**：`shared/` 只放两个以上板块都要用的物；板块专属物进各自子目录，**不得**塞进 shared/（否则升级会波及全部板块）。拿不准时默认放板块子目录，等第二个板块真要用时再提升进 shared/。

## 铸造配方（每包同流程）

```bash
# 1) 技能源就位：shared/<pack>/skills/<skill>/SKILL.md（frontmatter 必须有 description）

# 2) 组装 manifest（本仓暂无生成器；五共用包为单技能包，manifest 手写后必过校验）
#    形状契约：paas lib/pack-manifest.js（PACK_LIMITS：name≤80 / description≤2000 /
#    tags≤10 / skills≤10 且 content≤64KiB / mcpServers≤5 只能 {registryName, requiredGroup?}
#    禁 url/args/env/headers / 内容下限≥1）

# 3) 真校验器对拍（发布前硬门）
python3 ~/.agents/skills/facet-pack-builder/scripts/validate_manifest.py \
  fleet-packs/shared/<pack>/manifest.json

# 3b) MCP 引用必须声明工具面（pack-server-closure 契约，发布门强制）：
#     allowTools = 技能正文点名的工具（作者补充项并入），或 fullSurface:true + 理由。
#     本地校验器可能落后于发布门——400 "declares no tool face" 时按此补声明，勿绕。

# 4) 发布上架（代理通道；发布者 email 必须在 Logto creators 组；限流 10 次/时）
python3 ~/.agents/skills/facet-pack-builder/scripts/publish_pack.py \
  fleet-packs/shared/<pack>/manifest.json        # 新建（mint packId）
#   带 --pack-id <id> 追加 vN+1（升级走版本通道，各舰队独立跟进）

# 5) 上架后把 packId 记回本文件 §packId 台账，并回填依赖它的舰队模板 {{PACK_ID}}
```

## 装包与验证

- 用户装包走壹座「设置 → 功能集」；组门/scope/计费/市场凭据四层闸见 facet-pack-builder skill §5。
- 舰队配对：模板清单 `target: {kind: "pack-preset", packId: "<这里记的 id>", presetId: "..."}`；
  铸造脚本补齐占位时依赖段必须记录 packId 与版本（可追溯）。

## 工具白名单 vs 注册处（2026-11-08 实测对账）

装包之后客户拿到的是**工具面**，所以每个包的 `mcpServers[].allowTools` 必须真的存在于注册处
对应条目的 `tool_list` 里。工具改名或下线时这里会静默失效（装出来的包少一个工具、不报错），
所以对了一次账——**用列表而不是表格写**：本文件下方有机器台账，`check_drift.py` 会逐行解析
形如 `| slug | packId | vN | 源 |` 的行，任何多出来的表格行都会被当成格式异常
（2026-11-08 我先写成表格，漂移哨立刻报了 4 条"格式异常"，已改）。

- `erp-ledger-kernel-pack` → `fd-erp-ledger-mcp`：白名单 8，注册处 19，**8 个全在**
- `supply-chain-recon-roles` → `fd-erp-ledger-mcp`：白名单 8，注册处 19，**8 个全在**
- `supply-chain-warehousing-fleet` → `fd-erp-ledger-mcp`：白名单 6，注册处 19，**6 个全在**
- `supply-chain-warehousing-fleet` → `fd-info`：白名单 4，注册处 6，**4 个全在**

注册处侧（`GET /api/servers`，`fd-erp-ledger-mcp`）19 个工具与内核
`fd-erp-ledger-mcp/tool-surface.json` 的 `tool_groups` 声明**逐项完全一致**，两个条目均
enabled + healthy。

白名单只覆盖 19 个里的 8 个是**最小授权**而非遗漏——计费 8 / 信用 3 / 提案确认门 2 属于另一层
产品面（确认门是人的动作），等真有包需要再加。同理 `fd-info` 注册处 6 个、白名单取 4 个
（`locate_evidence` / `get_body` 未授）。

### registryName 的三个来源（2026-11-08 实测）

`mcpServers[].registryName` **不全是**"AI 注册处条目"。装包时 paas 的
`pack-store.installMcpRef` 按这个名字走三步，结果写进安装报告：

1. 市场目录（facet `/api/mcp-catalog`，线上 26 条）命中 → 按条目安装，凭据走 credentialRef；
2. 目录没有、但本机已有同名本地行 → `bound`（配置文件不动）；
3. 两边都没有 → `awaiting-local-binding`，报告里如实说"还没有这个服务器，装或连一个才能激活"。

把台账 36 个包引用的 19 个名字逐个对线上目录 + 本部署基线（`paas/mcp.json` 13 行）：

- **命中市场目录 12 个**：`fd-info`、`fd-open-data-mcp`、`fd-erp-ledger-mcp`、`fd-cn-report`、
  `fd-legal-search-mcp`、`fd-zhengxin-reference`、`fd-sentiment`、`edu-catalog`、
  `infra-telemetry`、`wire-build-mcp`、`wire-ops-mcp`、`lex-ops-mcp`；
- **绑定本部署自带行 5 个**：`bench-proposal`、`facet-market`、`fleet-architect`、
  `wanxing-ops`、`wire-ops`；
- **本部署没有、但打包形态里有 2 个**：`actions-mcp`（`platform.bundle.json` 的行名就是
  `actions-mcp`，服务器自己也注册成这个名字；本部署基线把它叫 `actions`）、
  `blueprint-author`（bundle 里有行、暴露 `blueprint_drafts_list` / `blueprint_validate` /
  `blueprint_save`，本部署未启用）。

**结论：本次没有发现包的笔误。** 三类来源都是设计内的；第三类在"本部署没启用那台服务器"时
会显示 `awaiting-local-binding`，那是**如实的未接通**，不是包写错。

⚠️ 但有个真坑要记住：**同一台服务器在不同形态下名字不同** ——
打包形态 `actions-mcp` / 部署基线 `actions`；市场目录 `facet-market-mcp` / `wanxing-ops-mcp` /
`wire-ops-mcp` / 自带行 `facet-market` / `wanxing-ops` / `wire-ops`。
写包时按目标形态的名字写，混了就落到 `awaiting-local-binding`。
（2026-11-08 我先按"市场目录 ∪ mcp.json"判，把 `actions-mcp` 与 `blueprint-author` 误判为
悬空并写进了本文档，随后在 `platform.bundle.json` 里找到两者，已更正——教训：判"名字不存在"
之前要把**所有形态的清单**都查一遍。）

**自动化（2026-11-08 起）**：`check_drift.py` 已把上面这套判据做成门——
引用名必须在「市场目录 ∪ 部署基线 ∪ 打包形态」之一存在（否则判红：那是真笔误，装包会
静默落成 `awaiting-local-binding`）；白名单只在来源**声明了工具**时才比（只有市场目录带
`tool_list`）。市场目录优先读线上、读不到退回快照并打 ⚠️。负向实证：注入
`actions-mcpx` → 红并逐条指名；恢复后回绿。

**新增或改名工具后必做**：改完工具面要重新生成条线能力目录（见下节），否则构造侧会
拿着旧目录写出过时引用。

## 条线能力目录（2026-11-13 起）

`shared/meta-catalog/catalog.json` 是「哪台服务器、在哪个形态下叫什么、实际有什么工具、
哪个条线在用」的机器可读快照。构造舰队（元线）写 `dataSources` / `allowTools` 时**必须**
以它为准，而不是凭记忆——2026-10-25 试点翻车就是数据源幻觉（`news_api` / `newsdata_io`
非在册名）被校验闸抓出来的。

```bash
# 生成（默认写 shared/meta-catalog/catalog.json）
python3 scripts/gen_catalog.py                     # 线上市场+注册处
python3 scripts/gen_catalog.py --offline           # 不碰网络，退快照
python3 scripts/gen_catalog.py --print-gaps        # 看缺口明细

# 校验已落盘目录：手写增补 / 陈旧 / 工具面漂移
python3 scripts/gen_catalog.py --verify
python3 scripts/gen_catalog.py --self-test         # 证明两条硬断言能红、干净副本能绿
```

`check_drift.py` 末尾已自动调用 `--verify`（失败计红），所以发布前跑的漂移哨同时守目录。

**四个上游，各记 status**（读不到就是缺口，不静默略过）：

| 上游 | 给什么 | 现网实况（2026-11-13） |
|---|---|---|
| 注册处 `/api/servers?limit=500` | **唯一权威工具面** | 27 台带 tool_list |
| 市场目录 `/api/mcp-catalog` | 在册名 + 展示字段 | 25 条，**不带 tool_list** |
| 部署基线 `paas/mcp.json` | 行名（本地形态） | 14 |
| 打包形态 `paas/platform.bundle.json` | 行名（镜像形态） | 10 |
| allowtools 快照 `paas/docs/fleet-templates/staging/` | 弱证据（白名单并集） | 22 |

**三条纪律**：

1. **形态分列，不合并**。`actions-mcp`(打包) / `actions`(基线) / `facet-market-mcp`(市场) /
   `facet-market`(基线) 是同一台服务器的不同形态名，`-mcp` 后缀差异由
   `aliasCandidates` 报出（现网 5 对），但不自动合并。
2. **弱证据不判红**。`allowtools-snapshot` 那份是白名单并集，不是服务器真实工具面；
   拿它判「工具不存在」等于用自己的声明证伪自己的声明。只有 `toolsSource=registry`
   才参与判定，其余记 `unknown` 并注明原因。
3. **陈旧来自生成时间**，不来自感觉。超过 14 天 `--verify` 直接红并给天数；目录是生成物，
   手改一条会在下次重生成时被静默冲掉。

**这条门抓到过的真缺陷（2026-11-13 首跑）**：`wire-build-mcp` 的注册处工具面（health
自报 healthy、当天校验）只有 15 件，其中**没有** `wire_artifact_dashboard_create`
（12 个包声明）与 `wire_artifact_doc_create`（15 个包声明），而这两件在 allowtools 快照里
有、且全工作区无任何服务端实现——即包声明了一件运行时根本调不到的工具。发布门的
`PACK_TOOL_FACE_GATE` 默认 `warn`，所以这类声明一路绿着上了架（与 2026-11-10 审计官
工具面三件空名字同型）。**处置挂账在 wire 线，不在本仓修**：要么线上补齐工具，要么把
12 个包的 allowTools 改到实际存在的 `wire_artifact_record_view_create` / `wire_artifact_publish`。

> **2026-11-17 订正：上面「全工作区无任何服务端实现」这句是错的，处置方向也因此相反。**
> 两件工具在 `wire-build-mcp` 仓 `main` 上**自 2026-10-23 起就有**（`86d634a`「dashboard/doc
> 两个产物创建工具」）：`wire_build_mcp/tools_console.py:391`（dashboard）/`:411`（doc）是真实现
> （都走 `_create_with_spec("dashboard"|"doc", …)`），上游 `fd-wire/api/fd_wire_api/artifacts/doc_spec.py`
> 也在，单测在 `tests/test_tools.py:357/397`；`tool-surface.json` 两组共 **17** 件工具。
> 而在役 `wire-build-mcp:sha-0eafdfb`（pod 起于 2026-11-14T04:10Z）只暴露 **15** 件——缺的正是这两件。
> 逐文件对哈希才看出的形态：pod 的 `auth_gate.py` 与 **≥2026-11-09** 的提交同内容，而
> `tools_console.py` 与 **≤2026-10-20** 的提交同内容（= `86d634a` 的父提交 `7c926a8`）⇒
> 这个镜像**不对应任何单个提交**，是混合代次构建出来的（不是「服务端没写」，也不是「跑在分支上」——
> 该仓 `ls-remote --heads` 只有 `main` 一条）。
> 所以别照上一段去把 12/15 个包的 allowTools 降级——那会把「线上落后一版」固化成「包写错了」。
> 正确处置是**从 `gitee/main` 重出片并滚 `wire-build-mcp`**，滚完 `--all` 那 15 条红自动消失。
> 复核口径：`python3 tooling/validate_manifest.py --all`（活体读面，读 `汇总：62 份 / 有错 15 / 降级 20`
> 那 15 行）+ `kubectl exec <pod> -- sha256sum /app/wire_build_mcp/*.py` 再与各提交
> `git show <c>:<path> | shasum -a 256` 比（**别拿镜像 tag 的 7 位当提交前缀**）。

## packId 台账（上架后回填，单一事实源）

> 2026-10-30 词表 v4 改名（市场发新版本）+ 版本回填；同日「跨境电商政策盯梢团队 v6 / 蓝图构造师 v7」
> 清了对外可见字段里的内部路径与阶段词（fleet-packs 路径、L3、change ID）。

| pack | packId | 版本 | 上架日 | 备注 | 语言覆盖 |
|---|---|---|---|---|---|
| 工厂集群（infra-factory-fleet） | `bKipx4urGfO5TE6GjJ9Ihg` | v4 | 2026-10-31 | 三岗（巡检/优化/报班）+ infra-telemetry 五只读工具依赖；change add-infra-factory-fleet | en·ja·es·fr |
| 简报与报数（旧名：能力包·合成·简报与报数 / 舰队·简报与报数） | `F8gbhNaEG6c2rDz468MwaA` | v7 | 2026-10-30 | 无 MCP 依赖，平台内置工具面 | en·ja·es·fr |
| 看板投稿（旧名：能力包·产出·看板投稿） | `Mo3_aWe7tF2eFVzkqFT9mA` | v8 | 2026-10-30 | wire-build allowTools 9 | en·ja·es·fr |
| 情报采集（旧名：能力包·采集·情报采集） | `4z4dMrtNbTn2WcE-45QPfw` | v7 | 2026-10-30 | fd-info allowTools 5 | en·ja·es·fr |
| 数据问答（旧名：能力包·归账·数据问答） | `y2_QsuKxV8dZFr-39_0zRQ` | v7 | 2026-10-30 | wire-ops allowTools 4，只读 | en·ja·es·fr |
| 评测哨（旧名：能力包·闸控·评测哨） | `_Rl8i99CS5nwuARVbp0D5Q` | v8 | 2026-10-30 | wire-ops 3 + wire-build 4 | en·ja·es·fr |
| 跨境电商值守岗包（crossborder-watch-roles） | `0LVfedThOxtymbwlbdZtGw` | v2 | 2026-11-04 | 四岗（合规分流/关务要素/选品参谋/物流值守）；三服务工具面同政策盯梢团队；源头 crossborder/crossborder-watch-roles/；change add-crossborder-vertical-line | en·ja·es·fr |
| ERP 账本内核能力包（erp-ledger-kernel-pack） | 未铸造 | — | 待上架 | **待上架/未发布（packId 未铸造）**：publish-ready 源清单 + 本地校验/dry-run 证据，live 发布属独立部署步骤（挂账）；fleet 段 v2 免自指（单一成员 target.packId 指向在册真实依赖 fleet-board-contribution）；mcpServers 引用 fd-erp-ledger-mcp 并显式声明 8 件 allowTools；zh 主轨，五语显示口径在 supply-annotations；源头 erp/erp-ledger-kernel-pack/；change add-erp-domain 7.1 | zh 主轨（副轨待补） |
| **团队模板** | | | | | |
| 启材盯梢哨（edu-watch-fleet） | `kyZNb-aDEEmpUfed1kp7TA` | v2 | 2026-11-07 | 单岗盯梢哨（edu-catalog 五工具；增量三栏清单+零增量照记）；v2=清洗对外 notes（泄漏扫描命中内部 change ID 已除）；源头 qicai/edu-watch-fleet/；change add-education-content-catalog | en·ja·es·fr |
| 情报舆情团队 | `IVCGxpg9itGnLpHxT1khWQ` | v12 | 2026-10-30 | 4 岗 6 边；依赖四共用包（_casting 已 strip，追溯在本台账）；源头 wire/intel-sentinel-fleet/{template,cast}.json | en·ja·es·fr |
| 识律政心监测团队 | `1HbpcL4LaB-csKpUQgN2Ew` | v7 | 2026-10-30 | 3 岗 5 边；读面=fd-zhengxin-reference；依赖三包；源头 lex/zhengxin-watch-fleet/ | en·ja·es·fr |
| 法规追踪团队 | `ZCot-b6HsnJRTTDKr_aunQ` | v7 | 2026-10-30 | 3 岗 5 边；检索面=fd-legal-search-mcp；依赖三包；源头 lex/statute-update-fleet/ | en·ja·es·fr |
| 识律营销团队 | `Y99VUN6RdWs98ocK0ZAN8A` | v7 | 2026-10-30 | 4 岗 7 边；allowTools 不含 publish（发布走动作闸）；依赖四包；源头 lex/lex-marketing-fleet/ | en·ja·es·fr |
| 官网内容团队 | `LjTJZWqSr2AOX7WEFTVlpg` | v17 | 2026-11-19 | 5 岗 8 边（四岗链+评测哨独立）；allowTools 不含 publish；**v17=文案加料**（合成位 +web-copywriting-discipline 六条 +web-offer-clarity 价值主张四问；闸位 +web-cro-claims 作为第⑤项，⓪-④ 原样不动；评测 prompt 加「gold 口径变更必须标不可比」）+ **v12=写作岗机器可读纪律**（每节自足/术语唯一/结论先行/表格优先/标题即锚点 + 断言溯源：金额比例因果承诺必须追到可查来源，追不到就不写或改量级，推测明写待验证）+ 闸位对应红线；源头 marketing/official-site-content-fleet/ | en·ja·es·fr |
| 谦面运营团队 | `ujUI1A2caCUW7TG1Z1mlEg` | v8 | 2026-10-30 | 3 岗 5 边；facet-market 三只读（策写结构性排除）；源头 facet/platform-ops-fleet/ | en·ja·es·fr |
| 萬星机群运营团队 | `8wJVZZnk369wm0oFScdecA` | v8 | 2026-10-30 | 3 岗 5 边；wanxing-ops 三只读（治理写排除）；源头 wanxing/fleet-ops-fleet/ | en·ja·es·fr |
| 跨境电商政策盯梢团队 | `C8wx14-4h7zJ0N19-J4NwQ` | v10 | 2026-10-30 | 蓝图构造间首单试点；源头 blueprints/cross-border-ecom-policy/；⚠️ 与市场另一枚「跨境电商政策盯梢」`_PGsJcBwYoeZU0kCfU-2Qg`（构造间再铸件 v7）重名待裁 | en·ja·es·fr |
| 跨境电商行业监测团队 | `4zEIbg4APf6d7-KUtLrmRA` | v1 | 2026-11-04 | 5.3 候选先铸（用户裁：行业监测先行，复用柏讯+爬虫平台基础设施）；源头 crossborder/industry-monitor-fleet/；读面双轨=fd-open-data-mcp 观测（数字）+fd-info 信号（定性）；已铸 platform-test 舰 fleet_1793775534912_v8yg2k | en·ja·es·fr |
| 跨境电商物流值守团队 | `h4Hc53roTMdoSxyPgKXN3Q` | v1 | 2026-11-04 | 5.2 铸造（用户放行）；源头 crossborder/logistics-watch-fleet/；读面 fd-open-data-mcp port.calls 当日快照；错峰 12:30/12:55/13:20 | en·ja·es·fr |
| 跨境电商合规分流团队 | `nG61wKNe3Vg6vtDhfDC_UA` | v2 | 2026-11-04 | 5.3 铸造（用户放行）；源头 crossborder/compliance-triage-fleet/；唯一产出面=分流单/升级队列只分流不裁决；错峰 10:55/11:25/11:55 | en·ja·es·fr |
| 柏讯本体建设团队 | `YT87-2Axuu49I123zVYyhA` | v9 | 2026-11-08 | 五岗（盘账/建模/校验闸/提案/主编）自包含；**v9=草案出口迁知验台域提案箱**（提案员改用 `bench_proposal_submit`：依据必填、kind 走域模板词表、created=false 记幂等命中；wire-ops 只剩 7 个只读工具，动作闸三工具已摘；出口未接线时停在本岗不退回客户动作闸）；v3=提案幂等；源头 blueprints/ontology-builder/ | en·ja·es·fr |
| 蓝图构造师 | `O30LA92kq1D5uuNIBGKsZQ` | v28 | 2026-11-08 | 三岗（架构/技能/装配）：架构师出六段/编制/工具面，技能作者补 `skills[]` 正文并收口 diff，装配岗按「一回合补一岗一根柱」落三支柱；共用一次批准；**写面收窄**——`facet_market_pack_publish` 与 `fleet_apply` 已移出 allowTools，铸造只经 `propose_action` 提案→拍板→已批准执行通道；技能正文铸造前过红线扫描；源头 shared/harness-constructor/manifest.json；change enable-constructor-skill-authoring 3.1-3.3（v10 为并发会话同日重发，本次落地为 v11）；**v22=架构岗的两条进度槽终于上了卡片**——线上实测该岗每轮实际提交 5–7 个 metric 键（`segments_done`/`segments_left`/`draft_complete`/`inline_agents`/`mcp_servers`），而 `kpiSlots` 只声明 `drafts_landed`/`source_gaps`；服务端只把声明过的键算 `slotsAccepted`，其余静默收进 details ⇒ **「九段补完到第几段」这个本岗最核心的进度指标从来没在卡片上出现过**（草稿数在涨、段序不可见）。v22 声明 `segments_done`/`segments_left` 并把正文收窄成「只报这四个键、其余写 summary」；E1 补 M16 判据（这处缺陷就是新判据首跑查出来的）；**v23=技能作者终于有写面**——它连续 4 轮 failed（`context pressure (2/4 compactions)`、`turn timed out`，从未报过一次数），真因是**写面承诺了不存在的通道**：persona 写「补 skills[] … 收口 diff 停等批准 … 批准后由 propose_action 提交铸造提案」，而逐岗工具面只有 `fleet_spec_read` + `facet_market_pack_validate`。查源码确认两件事：`fleet_draft` 的 schema 是**舰队编制**（mode/fleetId/members[{op,ref,title,targetKind,cadence}]），根本不写 `skills[]` 正文；`blueprint_save` 才是蓝图内容写面、但本仓既定裁决是「铸造只经 propose_action 提案→拍板」（`lib/action-registry.js` 里也确实没有 blueprint 类 action）⇒ 正解不是塞蓝图工具，而是**把 persona 点名的 `propose_action` 接到它手上**，并把纪律收成一格一轮：「一回合只写一份技能正文（≤8KiB）→ 追加进 `workspace/blueprint-draft.md` → 不在回复正文复述全文」，禁止为写一份技能把整份蓝图反复拉进上下文。E1 补 M17 判据（正文承诺写面而工具面没有 → 红；只认写动词短语，第一版把裸名词「草稿」也算承诺，把「拿到草稿/读草稿」的盘账岗与装配岗判红了）；**v23 真跑证据（2026-11-14 04:51Z）**：该岗**首次跑成**——cron 作业 `done`/356s/`success:true`，报数行 `r_1794632256690_c5pwbq` 的 metrics 恰好两槽`skills_authored=1 / skills_reused=1`（无未声明键、blockers 空），summary 自证执行了新纪律：「本回合一技能… 3.1KiB≤8KiB … 复用共用层·报数包1项（只引用包名）… 剩余待编：agent-rectify、agent-review 两份」；轨迹 `turn_553520cc44f14006b20d`（338.8s、`err:false`）显示它走的是**文件交接**而不是把蓝图读进上下文：`wc`/`grep ^#{1,3}`/两次 `read` 看清草稿结构 → `bash printf '\n---\n\n## 段 10 · skills（技能桶 · hc-skill-author 追加区）…'` **只追加**新段 → 复读校验 → 恰好**一次** `report_status` → `completed`。对照同日 04:31Z 那轮（3 步、`TRANSPORT`、零报数）与 11-10 的四轮（`context pressure (4 compactions)`），病根（写面承诺无通道 + 一轮想写整桶）与诱发因素（上游 TTFB 尖峰 ~36s 撞 30s 重试预算）都已分开定性；**顺带记**：该岗线上有**三条物化行**——活的 cron 作业加两条 11-10 的「接棒 · 技能作者」任务（仍 failed、带旧压缩错误），成员 `taskId` 指向后者，live 闸因此只报 ℹ️「没带 nextRunAt」；**v24=装配岗首次跑成（2026-11-14 07:27Z）＋四语种槽位补齐**——该岗的物化作业此前 `history` 为空（**从未跑过**：构造链第三跳一直是断的，草稿里只有架构师九段与技能作者段 10，三支柱一格没落）。本轮补 `turnTimeoutMs=900000` 后真跑：cron `done`/243s/`success:true`，报数行 `r_1794641439522_k3htld` metrics 恰好四槽（`posts_chartered=1 / tools_narrowed=0 / boards_wired=0 / catalog_misses=0`，blockers 空，两个 0 都带「列入 pillars_left」的 delta）；草稿 `1da519d4c1c0bf89/workspace/blueprint-draft.md` 34285→37947 字节，**只追加**「段 11 · 三支柱」（11.1 目录命中 / 11.2 包级记忆四轴 / 11.3 巡店岗记忆章程 / 11.4 pillars_left 8 项），段 1–10 一字未动。同版修 i18n：架构岗 v22 加了 `segments_done`/`segments_left` 两槽，而 en/ja/es/fr 四份覆盖层仍只写前两槽——真校验器报 4 条 `partial overlay` 而离线闸全绿（见 M19）；**v25=一格一轮抬到两格一轮，依据是实测成本与预算**：原纪律「一回合只补一个岗的一根柱」是 480 秒软停时代的产物（2026-11-13 turn 1628 两岗一轮必死），而 v24 起本岗预算 900 秒（软停 720 秒），2026-11-14 真跑实测「一格＋落盘＋报数」只用 **243 秒**（trace `turn_7939534fed0c418fbcdf`：112 事件、6 个 `usage` 帧、totalTokens **203,115**、其中缓存读 152,768）。同样的 720 秒够跑两格，所以 9 格（三岗×三柱）从 9 轮压到约 5 轮，**不增加任何一轮的花费**；越界的代价也只是这一轮白跑（草稿只追加，已落盘的格不丢）。正文两处（persona 与 promptTemplate）同时改成「至多两格」——M12 的分批口径判据要求两处同数，否则会红；**v25 真跑验收（2026-11-14 09:08:31Z）**：同一作业 `job_1794585880486_f9sbwb` 手动触发一轮 → `done`/`success:true`/**304s**（304094ms），草稿 37947→**41190** 字节、`### 11.x` 由 4 段变 **7** 段（新增 11.5 整改岗章程、11.6 复盘岗章程，11.7 重写 pillars_left），待补由 8 项降 **6** 项、三岗记忆章程全齐——**两格在同一轮内落盘**，要解的就是这件事；报数行 `r_1794647550311_9iafte` 四槽齐、blockers 空、`catalog_misses=0`（章程只引用 11.1 在册名）。两格 304s 对 720s 软停只到 42%，第二格的边际开销远小于「一格 243s」里的整轮固定成本——但**没有三格实测，所以正文不抬到三格**。**顺带一条自报数与产物的错位**：该行 `posts_chartered=2`（累计含巡店岗，delta 写「余力格复盘岗成则下轮累加至 3」），而产物里三份章程都已落盘 ⇒ 指标比产物**慢一轮**、下轮自愈；读卡片别把 3 读成 2，也别把这种错位当成丢格；**v26=预算抬到 1200 秒（软停 960 秒）＋「每落一格立刻结账」**——v25 的两格在「工具面＋看板」这种重内容上撞到了 720 秒软停：2026-11-14 第三轮作业 `job_1794585880486_f9sbwb` 的第 1 次尝试先吃上游 429（`Upstream account queue full`，347s 白跑），第 2 次从零重走，753s 后撞 `budget soft stop (720s of 900s)`——**两格（11.11 整改岗工具面 7 件、11.12 整改岗看板三列）都落盘了，报告与 `pillars_left` 都没写成**：卡片停在「已补 3 格」而草稿里已有 5 格，11.10 那份 tracker 仍写「余 4 项」（真实余 2）。所以 v26 两条收口纪律写进正文两处（persona 与 promptTemplate，M12 两处同数不变）：① **每落一格的第一件事就是追加该格的 `pillars_left`**（按草稿实际段落重算），不留到回合末；② **回合开始先看草稿最后一段是不是 `pillars_left`**，不是就说明上一轮被截断过，先按实际段落重算补齐再动笔——否则会照旧 tracker 把已完成的格重做一遍。这两条比「再补一格」优先：账错会让下一轮做错事，少补一格只是慢一轮。顺带一条外部约束：同一时段连着点火会撞上游账号队列（429），重试是从头再走一遍 ⇒ 「一格多久」不能按最好情况估；`turnTimeoutMs` 由 900000 抬到 **1200000**；**v26 真跑验收（2026-11-14 10:21:46Z，`job_1794585880486_f9sbwb` 第三次执行）**：`done`/`success:true`/**845 s**——**比旧软停 720 s 还长 125 s**，即这一轮如果没抬预算会第二次被截断；同一轮连落两格（11.14 复盘岗工具面 4 件、11.16 复盘岗看板三列）并写出三份 tracker：11.13（开工前按实际段落重算，写明上轮欠账 `tools_narrowed 4→11`／`boards_wired 1→2`）、11.15（第一格落盘后）、11.17（终版：**三岗 9 格全齐、合计 0 项**，draft 59311 字节/17 段）。**两条收口纪律第一次真跑就都按预期生效**——开工先重算 tracker、每格一写，正是它们在上一轮缺失时造成「卡片 3 格 vs 草稿 5 格」。**这一轮没报数**：本窗口报数配额（3 条/720 分钟）被我当天连续点火用满，装配岗按纪律改用记忆笔记留痕 ⇒ 卡片上的 `posts_chartered/tools_narrowed/boards_wired` 会滞后到 2026-11-17 才追上产物，**别把它读成没干活**。另：11.11 自己纠了 11.10 的一处口径（段 4 上界「六件」实为 7 件：fd-memory 5＋fleet 2），draft 的 11 个工具名我已按 `fd-memory/src/mcp.ts`（9 件）与 `paas/server/fleet-mcp.js`（7 件）逐名核过，**全部在册**；**v27=「在册依据」改如实口径**——11.14 自陈 `fleet_spec_read` 回包**不带逐件工具清单**，而 persona/工作纪律两处都写着「名字只认 `fleet_spec_read` 回包的 server 名与工具名」，等于点名了一个给不出工具名的通道（同 v23 病灶）；改成三源（`fleet_spec_read` server 名／段 5 已核 `mcpServers`／会话可见工具名）＋「别把『我记得有』写在册」＋报数里写明依据，并查实没有可用的目录查询面（`pack_author` 的 `/api/pack-author/market-servers` 在本线返回空表）；**技能桶同一天收口 3/3**（`store-inspect-record` 3.1KiB／`rectify-ticket-track` 3.9KiB／`review-weekly-report` ≈4.4KiB，各带 frontmatter 与三要件，均 ≤8KiB，逐岗挂 agent-inspect/agent-rectify/agent-review），草稿到 **67385 字节 / 段 1–11 齐**——构造链三跳＋9 格＋技能桶全部走通，剩下一步是 `propose_action` 提请主人拍板铸造（**装配岗不执行、也不该执行**）。顺带一条实况：这半天里「兑现时撞上 runtime 重启 ⇒ 整轮丢失」又发生一次（10:47 技能作者 `state=interrupted`、草稿零字节变化），与 04:07 评测岗同形 ⇒ 未出片的池子就绪修复 `fc756465d` 不是理论风险；**v28=教会两个产物「不同形」**——草稿是九段（`blueprints/schema.md` 契约），提案单位是 META-LINE §4 的**八段**；2026-11-14 实测草稿把 `acceptance` 写成自造的「A 结构/B 行为/C 边界/D 数据/E 交付」五类，**这五个名字不在 `kind` 闭集里**，按它铸造会被发布门拒（仓里四份真蓝图全是 `{id,kind,...}` 结构条目），且八段里的 `evidence`/`cost` 在九段草稿里压根没有。修法：架构岗正文写清「acceptance 每条 `{id,kind,target?,expect?,rubric?}`、kind 只取五型闭集、自造类型名＝铸造被拒」；装配岗交对象时按八段写，三处最易错点名（`toolkit` 两层非点号串、`acceptance` 支柱条目带 `counterCase`、`evidence`/`cost` 不许省）。META-LINE 新增 §4.0 把三类错法与正法列成表；**v29–v33 与当日的收口**：v29 允许架构岗重写自己写错形状的那一段（append-only 只属装配岗的段 11）；v30 八段**分轮写**（一回合硬拼八段撞 960s 软停、一字未落；分轮后 543s/463s/747s 三轮收口）；v31 报数窗口其实是 `max(cron 周期÷2, 10 分钟)`——日频 720、**周频 5040**，三包共 24 处把常数改成公式；v32/v33 把 `kind` 写死成五个**字面量**并加「收口前 acceptance 自查」（写「五型」不够：段 8 也有一套叫「五型」的 A–E）；结果：**九段 9 格＋技能桶 3 份＋八段发布对象 8/8 齐**（draft 104884 字节），`acceptance` 24 条经 `check_release_object` 的 `validate()` 实测 **errors=0**（counterCase ≥8 字、rubric ≥10 字两条下限都过）；其中 12.6-b/12.6-c 是我**代笔**的（装配岗两轮未改、后一轮 `interrupted`），只换 kind/加柱标/counterCase、断言正文未动，已在草稿里注明；**随后已闭合（离线实证）**：`official_en_coverage_incomplete` 的真源是零依赖叶模块 `paas/lib/pack-locale-coverage.js`，en 覆盖写在**顶层 `i18n.en`、按声明路径索引**（`skills["<name>"].description` / `agents["<id>"].name`），**不是**「description 对象化」——本机两组对照与平台判词逐字一致：只写标量 `2/5`，补上 skills/agents 映射后 **`8/8`、missing=[]、`officialEnGateBody()`=null**；封装成 `tooling/check_en_coverage.py`（0/1/2 三档，自检 3/3；拿不到 paas 模块时退 2、报「判不出」而非「红」）。至此发布对象**无已知判据缺口**，只剩「谁去跑 propose_action 提审」这一件属人的事；**v34（2026-11-14，包自己的说明书在四处说错话＋提审权放错岗）**——先做了一次全量 diff：三岗 `cadence`（cron/tz/promptTemplate/turnTimeoutMs）线上与包**逐字相同**，所以 11:29Z 技能作者那轮 `budget soft stop (480s of 600s)` 不是「改了没上」，而是**包本身的缺陷**：它每轮要读 107 KiB 的整份草稿。顺着查出同一份过时事实活在**五处**——架构师 prompt 前半「六段」后半「九段齐后」、两份技能正文、四份语种覆盖层（en/ja/es/fr 全写 six segments / 六段）、top-level `notes`；以及**提审权放错岗**：架构/技能两岗都持 `propose_action`，而被设计指定交发布对象的装配岗没有 ⇒ 同一份对象可被各岗各提一次、各进一次审批队列。修法：①九段口径按 `blueprints/schema.md` 逐段列名写入五处；②架构师写 `workspace/skills-pending.md`（≤4 KiB 交接单）与 `workspace/draft-status.md`（≤1 KiB 进度账），技能作者只读交接单、不再读整稿，两岗预算 900 s；③`propose_action` 收敛到装配岗，另两岗正文明写不提。**闸这一格重写**：旧 M17 要求「正文承诺落盘 ⇒ mcpTools 里要有落盘工具」，三件候选读源码就否掉了（`fleet_draft` 干跑 `Nothing is persisted`／`blueprint_save` 属另一台 MCP 且明写审批不经过岗／`propose_action` 是提审），而岗写工作区文件走运行时**内建文件工具**（`str_replace_editor`、bash 重定向，见 `paas server/file-tool-paths-server.js`）、不在 `mcpTools` 里 ⇒ 旧判据结构上不可能绿，**唯一能让它绿的动作就是发提审权**，缺陷正是这么长出来的；改写后判 `propose_tool_without_duty`／`propose_duty_without_tool`／`multiple_proposers`，另新增 M21（段数与岗数的**陈述**必须与现行形状一致，扫 persona/prompt/技能正文/i18n/notes 五处）。红证：拿改前真包跑新判据 **命中 28 条**（21 段数／5 岗数／提审权两向）；自检 40/40；`meta-line-all` 绿；真校验器 ✓（skills 2 / mcp 3 / agents 3）；en 覆盖 9/9 门开。三岗已 PATCH 上线并**逐字核对**一致（turn **900/900/1200 s**）；顺带改正一条路由记忆：成员改配是 `PATCH /api/fleets/<fid>/members/<mid>`，**没有** `/cadence` 后缀（旧路径在这个片上返回 Express 默认 404）。 | en·ja·es·fr |
| 识律合同签署团队 | `EqlwaCssvIsqvobKMopiFQ` | v3 | 2026-11-06 | 单岗（发起与跟催岗）自包含无共用层依赖；wire-ops-mcp allowTools 恰三件自理闸面工具；红线纪律随 persona+esign-gate-discipline 技能自带；stub 内测口径；源头 lex/esign-contract-fleet/；change add-esignature-selfaction-gate | en·ja·es·fr |
| 花卉行业监测团队 | `IlHSyGGl6NP7iI7sknVc_A` | v2 | 2026-11-08 | 三岗（监视/合成/产出）自包含；两技能=flower-watch-discipline（读数四要素/取数失败四分类/缺失三分类/量纲与参考年禁忌/不可得清单）+flower-round-report-contract（日·月·季节奏/发布达成三问/状态槽位/缺位保留）；节奏按斗南 T+1 日更（06:30/07:10/07:40 CST）；读面 fd-open-data-mcp 四只读+fd-info 三只读+wire-build 三草稿工具；源头 flower/industry-watch-fleet/；change add-flower-vertical 5.5 | en·ja·es·fr |
| 优化团队 | `gkHE4CsQHC2rEV0ZWziqWg` | v15 | 2026-11-18 | 两岗（盘账/提案）；评测已迁 meta-grader（禁自证），故本队不含评测岗；技能=meta-inventory-discipline+meta-proposal-protocol；靶子＝在册团队与报数史，**写面结构性排除**（fleet-architect 三只读/facet-market 三只读/wire-build 三草稿/actions-mcp 仅 propose_action）；gold 由编制声明、被评岗不参与自身 gold（禁自证）；**visibility=public（2026-11-18 起）**：此前为 private（首轮真跑未过不上面），用户裁定元线三支必须公开后转公开；源头 blueprints/meta-optimizer/；change enable-constructor-skill-authoring 5.1-5.3；**v3=分批铁律**：盘账≤5队/回合、评测≤3队/回合、提案≤3条/回合（trace.db 实证首轮超时真因=全量取数→compaction 75s→step6 模型流挂死），promptTemplate 同步收窄 | en·ja·es·fr |；**v3=分批铁律**（盘账≤5队/评测≤3队/提案≤3条每回合，治 compaction 挂死）；**v4=成员节律同步**（cast_blueprint.py 治住「人读新、机器跑旧」）；**v5=报数优先**（取完数立刻 report_status 再补细节，并声明本岗无 board 工具——v4 实跑因找不存在的 board 工具烧光输出预算，stopReason=length 空手收尾）；**首轮已通过**：done/155.8s/85,910 token，报告 teams_tracked=5、gaps_found=10 落库（reports/first-round-live-evidence.md）；**v13=第四槽上卡片＋蓝图终于追平产物**——正文四处都写了第四槽 `pending_next_round`（排队提案数，本岗最该被看见的积压读数），而 `kpiSlots` 只声明三个 ⇒ 它一直只在 details 里；这条是 `check_live_fleets.py --slots`（产物侧对账）首跑点出来的，离线闸 M16 当场还看不见这种「报数含 A / B / C」的写法，为此补了第三种形态判据。同版修掉两处**蓝图落后于产物**：① 蓝图 agents 少了每岗 `resources.mcpTools` 与 `memory.guidance`（cast 一次就把它们从 manifest 里抹掉——当天我抹了一次又一次，靠 `git show HEAD:` 取字节还原，没用 `git checkout --`）；② 蓝图还带着已迁走的 `meta-eval-gold-discipline` 技能与四语种 i18n 里的 `mo-eval` 残留（校验器按「引用未声明的成员」拒收）。现在 cast 双向自洽（`--check` OK）、四语种补齐第四槽译名；**v14=把回合预算写进包，两岗真跑转绿（2026-11-14）**——两岗的物化作业上一轮都是 `turn stopped early — budget soft stop (480s of 600s)`（495s / 493s，零报数），而它们的 `cadence.turnTimeoutMs` 是**空的** ⇒ 继承缺省 600s，软停落在 480s。同线另一支舰队的两岗因为写了 900000 而以 524s/531s 收口——同一件事、只差这一行。v14 给两岗（agents 与 fleet.members 两处）都写 `turnTimeoutMs: 900000`，并 PATCH 线上成员 cadence；真跑验收：盘账岗 `done`/438s/`success:true`，报数行 `r_1794634754558_5x7pfg` metrics 恰好两槽（`teams_tracked=17`、`gaps_found=13`），**且它自己在 summary 里纠正了 11-13 那句「取数通道未启用」**——「取数通道本轮恢复（fleet_list/fleet_turns_read 可用…）」，即那天的「工具面不可调用」是当时那份安装物的事，v13 安装后已通；覆盖 5/17 支并逐队列出应报/缺报/连败（谦面·平台运营 应报4缺报3、萬星·集群运营 应报4缺报3连败1 岗、跨境电商政策盯梢 应报3连败2 岗、内容集群（课例）应报3连败1 岗、工厂集群 应报4缺报1连败2 岗）。E1 补 **M18**（正文指挥逐队调 `fleet_turns_read` 而没声明预算 → 红，两条臂：完全没写、写了但只有 600000）；**提案岗也同轮转绿**：`done`/476307ms/`success`，报数行四槽恰好（`proposals_raised=3 / items_cited=8 / proposals_rejected=0（未取得，非 0 拒）/ pending_next_round=5`），并且它**自己把本队那个预算缺陷诊断出来了**——P2「优化团队本队双岗 cron 预算同型失败…均 soft-stop 480s/600s…建议预算上调」，与我这轮的修法同一条；另两条 P1（评测舰队双岗连挂，判据取自最近三轮）与 P3 亦带逐条证据。**该条 P1 需分档读**：两岗最新回合都已 `ok`（评测 7688、审计 7752），失败的是加预算之前那几轮；但审计岗 **7748 撞的是 `budget soft stop (720s of 900s)`** ⇒ 900s 是**地板不是保险丝**，下一档该收窄工作或接受偶发损失，而不是继续抬预算 | zh 主轨 |
| 行业分析团队 | `8gqJKlQ7TDLtPgyWGQw0dw` | v3 | 2026-11-08 | 三岗（取数/分析/押注）自包含；三技能=industry-fetch-discipline+industry-analysis-discipline+industry-bet-line-discipline；叙述层与押注层分离，每条结论挂四要素押注行（指标/方向/时间窗/失效条件），断言形状限既有能力，不可验叙述单列并计入占比；读面 fd-open-data-mcp 四只读+fd-info 三只读+wire-build 三草稿+actions-mcp 仅 propose_action；**visibility=public（2026-11-18 起，待 v4 上架后生效）**；源头 blueprints/meta-industry-analyst/；change enable-constructor-skill-authoring 6.1-6.3；**v2=分批铁律**：取数≤8序列/回合、分析≤5结论/回合、押注≤10条到期对拍/回合（与优化团队 v3 同一处方，同源于 compaction 挂死教训） | en·ja·es·fr |；**v3=报数优先**（与优化团队 v5 同一处方：取完数立刻 report_status 再补细节，并声明本岗无 board 工具，留档写工作区文件）
| 评测团队 | `2uXkSlEByBS7Zzu4JkmdwA` | v14 | 2026-11-18 | 两岗（评测/审计）自包含，禁自证；评测岗按 gold 对拍出偏离结论，审计岗拒绝清单制只判证据自足/gold 独立/反例集；技能=meta-eval-gold-discipline+meta-audit-discipline；源头 shared/meta-grader/；**v4=清掉占位符**（`fleet.members[0].target.packId` 落成字面 id，源里仍写 `{{PACK_ID:meta-grader}}` 由铸造解析）＋成员 `promptTemplate` 同步岗位节律（蓝图源此前落后于产物、写的是弱化的「收尾必 report_status」，已从产物回填「报数每轮一次（配额 3 条/720 分钟）」）；v1–v3 带着未解析占位符上架，线上评测舰队因此一直建不起来；**v6=把 persona 承诺收回到真实工具面**——首轮实测 turn 7504 `turn_budget_exceeded`（steps=19、lastTool=`list_mcp_resource_templates`、hasOutput=false、reported=false、480s/600s）：persona 第一条要求「SQL 真值…直接用查询算」，而该岗工具面里根本没有 SQL 通道，模型在工具面里找路把整轮预算烧光。v6 改成「回合表真值：`fleet_list` → `fleet_spec_read`（＝应报）→ `fleet_turns_read`（实报/缺报/连败）」并加【工具面纪律】明令不许翻 MCP 资源模板；E1 补 M10 判据（声称有 SQL 通道而工具面没有即红）；**v7=回合预算写进包**（`fleet.members[mg-eval].cadence.turnTimeoutMs=900000`；平台 `resolveTurnBudgetMs` 夹在 [2min,30min]，写死在 2 空格或超界都会被静默夹紧，故 E1 补 M11）；**v8=报数工具点名**（首轮轨迹实测 `tool_search "report"` 回包「No effective tool matches」，故正文写入 `mcp__fleet__report_status` 与 tool_search 兜底路径、并定「报数在前、写记忆在后」）；**v9=把报数口真的打开**——包级 `fleet.allowTools` 原先只有 `auditor_submit_verdict`，经 `effectiveMcpTools()` 交集后 `report_status` 被筛掉（对照证据：优化团队 `mo-inventory` 没声明 fleet，反而拿到全量工具面并成功报数），现包级与两岗 `mcpTools` 都声明 `report_status`，E1 补 M13；同版把 persona/promptTemplate 的分批上限统一为 2 支（3 支实测 644s／900s 余量 28%），E1 补 M12；**v10=审计岗不再自己去猜舰队 id**——persona 把取数顺序钉死成两步（`fleet_list` 找 name=评测舰队 那支 → 它的 id 就是我的 fleetId），并禁止翻工作区/数据库找 id（实测那轮 16 步、720s 烧光零产出）；**v11=审计岗导航真的修好了**——首轮 v10 实跑仍是 31 步：缺 `fleet_list` 只能对 14 支舰队逐个试读，补上后同一条指令只花 2 步；E1 补 M14（有 `fleet_turns_read` 却没有 `fleet_list` 即红，只收窄到这一个工具——`fleet_spec_read` 的 id 可由 handoff 带进来，全算进去会把构造师的技能作者判红）；**v12=裁决从待决事项车道挪到报数车道**——trace 里两次 `auditor_submit_verdict` 都回「Decision not found」：该工具复核的是**待决事项**（`itemId` 必填、服务端 `getDecision` 查），而评测结论不产生待决事项（本舰队 `openDecisions: 0`），所以它结构上收不了这条结论；改为一条 `report_status` 同时出三态＋理由，`auditor_submit_verdict` 降为「`fleet_list` 显示 openDecisions>0 时」的条件分支，E1 补 M15；**v13=报数槽位与 kpiSlots 对齐**——审计岗 persona/promptTemplate 一直写着第三槽 `dissent_reasons`，而 `kpiSlots` 只声明 `verdicts`/`blocks`；服务端只把声明过的键算 `slotsAccepted`，其余回一句 "UNRECOGNIZED slot keys (kept in details, not shown on the card)" 就收进 details——**不报错、不失败**，真跑里看到的是一份三槽齐全的 metrics 和一张两槽的卡。v13 把第三槽落成可计数的 `dissent`（异议条数）、理由进 summary，评测岗的 `gold_source` 同理从槽位列表挪进 summary；E1 补 M16（两种写法各一条判据：「A / B / C 三槽」与「报 X 与 Y」）；**同版实测到这条链上的第三份副本**——报数口径在包之外还有两处落点：`member.cadence.promptTemplate`（成员行）与**定时任务行里的 `prompt`**。装包只换前两处（包→成员），任务行的 prompt 是 `fleet-reconcile.js: materializedSpec()` 从 `member.cadence.promptTemplate` 抄进去的副本，**改包不会让它变**：v13 装完、成员 kpiSlots 已补 `dissent`，任务行 prompt 仍是 v12 的「verdicts/blocks/dissent_reasons 三槽」（`/api/cron` 逐字可见）。改成员 cadence 后 reconcile 按字段漂移就地重写（实测 20s 内 `dissent_reasons` 消失、`verdicts/blocks/dissent` 出现），所以正确的改法是**改包 → PATCH 成员 cadence → 等 reconcile 重写任务行**，三步缺一就「包里新、线上旧」。另记一条判读坑：`job.error` 会跨轮留存（本轮 `state=running` 而 `error` 仍是上一轮的「cell restarted during execution」），判活要看 `state` 与最新回合，别拿 error 字串当「现在还在坏」 | zh 主轨 |

## 波 2 在架套件语言覆盖（add-pack-locale-supply，2026-11-02）

波 2 的 **35 个在架套件**（wire 20 / legal 6 / micro 3 / text 2 / vaas 1 / copilot·facet-forge·平台指南·星研·学院 4）全部补齐 en/ja/es/fr 显示副轨并发布，市场 `?locale=` 四语回读逐包通过（35/35）。逐包 packId/版本/作者与发布通道、registry 快照三修、限流处置见 `openspec/changes/add-pack-locale-supply/reports/wave2-inventory.md`。

- 星研·学院 `bQ-NlOBloZegGfkiczktDA`：v3（zh-only）→ **v4**（补四语）。其 use 层派生技能曾撞官网课程 UI 路由表 vs use 层禁用门，按「派生层脱路由」裁决解决（`docs/xingyan/packs/derive_pack_skills.py` 的 `deroute_use_level`，课程正文不动）。
- 波 3 的 7 份不在架源（4 退役 `data-*` + `crm-desk-reception-suite` + 2 baiye）已补齐仓内副轨、过发布门，**不发布**（退役/未上架）。

## 机器台账（cast 脚本 parse_ledger 与漂移哨读这张表；行首=源目录 slug）

| slug | packId | 版本 | 源目录 |
|---|---|---|---|
| fleet-brief-report | `F8gbhNaEG6c2rDz468MwaA` | v8 | shared/fleet-brief-report |
| hotel-industry-watch-fleet | `AH5iRdho7xEJIMplpL2BjA` | v4 | hotel/industry-watch-fleet |
| fleet-board-contribution | `Mo3_aWe7tF2eFVzkqFT9mA` | v9 | shared/fleet-board-contribution |
| fleet-intel-gather | `4z4dMrtNbTn2WcE-45QPfw` | v8 | shared/fleet-intel-gather |
| fleet-data-qa | `y2_QsuKxV8dZFr-39_0zRQ` | v8 | shared/fleet-data-qa |
| fleet-eval-sentinel | `_Rl8i99CS5nwuARVbp0D5Q` | v9 | shared/fleet-eval-sentinel |
| harness-constructor | `O30LA92kq1D5uuNIBGKsZQ` | v28 | shared/harness-constructor |
| meta-grader | `2uXkSlEByBS7Zzu4JkmdwA` | v15 | shared/meta-grader |
| meta-optimizer | `gkHE4CsQHC2rEV0ZWziqWg` | v16 | blueprints/meta-optimizer |
| meta-industry-analyst | `8gqJKlQ7TDLtPgyWGQw0dw` | v6 | blueprints/meta-industry-analyst |
| crossborder-watch-roles | `0LVfedThOxtymbwlbdZtGw` | v2 | crossborder/crossborder-watch-roles |
| intel-sentinel-fleet | `IVCGxpg9itGnLpHxT1khWQ` | v14 | wire/intel-sentinel-fleet |
| statute-update-fleet | `ZCot-b6HsnJRTTDKr_aunQ` | v9 | lex/statute-update-fleet |
| zhengxin-watch-fleet | `1HbpcL4LaB-csKpUQgN2Ew` | v9 | lex/zhengxin-watch-fleet |
| lex-marketing-fleet | `Y99VUN6RdWs98ocK0ZAN8A` | v9 | lex/lex-marketing-fleet |
| official-site-content-fleet | `LjTJZWqSr2AOX7WEFTVlpg` | v17 | marketing/official-site-content-fleet |
| platform-ops-fleet | `ujUI1A2caCUW7TG1Z1mlEg` | v9 | facet/platform-ops-fleet |
| fleet-ops-fleet | `8wJVZZnk369wm0oFScdecA` | v9 | wanxing/fleet-ops-fleet |
| cross-border-ecom-policy | `C8wx14-4h7zJ0N19-J4NwQ` | v11 | blueprints/cross-border-ecom-policy |
| ontology-builder | `YT87-2Axuu49I123zVYyhA` | v9 | blueprints/ontology-builder |
| infra-factory-fleet | `bKipx4urGfO5TE6GjJ9Ihg` | v5 | shared/infra-factory-fleet |
| industry-monitor-fleet | `4zEIbg4APf6d7-KUtLrmRA` | v1 | crossborder/industry-monitor-fleet |
| logistics-watch-fleet | `h4Hc53roTMdoSxyPgKXN3Q` | v1 | crossborder/logistics-watch-fleet |
| compliance-triage-fleet | `nG61wKNe3Vg6vtDhfDC_UA` | v2 | crossborder/compliance-triage-fleet |
| esign-contract-fleet | `EqlwaCssvIsqvobKMopiFQ` | v3 | lex/esign-contract-fleet |
| lex-surface-orientation | `bYnUd79CqGEHXb79cWDKGg` | v1 | lex/lex-surface-orientation |
| erp-ledger-kernel-pack | `0tTprnlfj0d13u9QNgnKOA` | v4 | erp/erp-ledger-kernel-pack | v4=2026-11-17 纪律卡改「八件商业工具」为分组 32 件（period 组 5 件不随包发）；工具面 32 件 ⊆ 注册处 37 件商业集 |
| edu-watch-fleet | `kyZNb-aDEEmpUfed1kp7TA` | v2 | qicai/edu-watch-fleet |
| supply-chain-connector-pack | `y8gnMHFMze-Mylsd6JXpkA` | v3 | fd-erp-vertical-templates/templates/supply-chain/connector-pack | v3=2026-11-08 角色绑定：`wms-snapshot`/`supplier-portal`/`carrier-portal` 三条加 `providerRole: true`（包声明角色、客户在自己的连接器里选系统；未选定报「待选系统」而不是永久能力缺口）+ note/description 改对外口径；manifest 1.1.0。v2=2026-11-08 纠正声明：`wms-snapshot` 由非 optional 降为选接 + note 改对外语气；manifest 1.0.1 |
| supply-chain-account-mapping-pack | `3AecZSztjJaBzhEwAD7nXg` | v1 | fd-erp-vertical-templates/templates/supply-chain/account-mapping |
| restaurant-chain-connector-pack | `3RZRBdc66rKdLV5I8tuKIg` | v2 | fd-erp-vertical-templates/templates/restaurant-chain/connector-pack | v1=2026-11-08 首次上架：manifest 1.1.0 补机器可读 `connector_bindings`（`pos-sales` 角色 + `payments-merchant` 微信支付带支付宝/美团备选 + `delivery-settlement` 美团外卖结算），三条均 optional（未适配 POS 走导出文件通道；支付/外卖 live 面实测后声明）+ 补 en 展示面（官方上架门要求 name/description/tags/skill desc 全覆盖）；v2=2026-11-08 面级纠错：删掉 `delivery-settlement`（meituan 在目录里只有 query_travel，是酒旅不是外卖结算）、`payments-merchant` 备选去 meituan；manifest 1.2.0 |
| crossborder-connector-pack | `cv0kj9mRHGrz6nNMA7glVg` | v2 | fd-erp-vertical-templates/templates/crossborder/connector-pack | v1=2026-11-08 首次上架：manifest 1.1.0 补 `connector_bindings`（`shopify-store` 真 id + `cn-commerce-store` 角色），两条均 optional（Shopify 客群与国内平台客群互斥，任一写必接会让另一半装不上）；拼多多上游 schema 缺失故不声明 + 补 en 展示面；v2=2026-11-08 面级纠错：`shopify-store` 的 provider 由 shopify 改为 shopify_admin（目录里 shopify 的 read 全是博客/页面，订单库存在 admin 变体；payouts 无动作故去掉承诺）；manifest 1.2.0。**源已前进到 manifest 1.4.0 / pack 1.5.0（2026-11-10）**：新增能力矩阵资产（`capability-matrix.json`，28 格，内圈全为「需一次接入」）、四语 i18n（en/ja/es/fr，状态短语由矩阵生成）、payout 只读动作已落 `shopify_admin`（绑定说法同步改为「动作在、无真实读取」）；**待上架**（市场仍为 v2，packId `cv0kj9mRHGrz6nNMA7glVg`） |
| crossborder-account-mapping-pack | packId 未铸造（待上架/未发布） | — | fd-erp-vertical-templates/templates/crossborder/account-mapping | 源 manifest 1.3.0 / pack v1.1.0（2026-11-10）：补四语 i18n（en/ja/es/fr）+ 内嵌 `account_mapping.version` 同步 1.1.0；映射只影响呈现，替换不改账 |
| crossborder-recon-fleet | packId 未铸造（待上架/未发布） | — | fd-erp-vertical-templates/templates/crossborder/recon-fleet | 源 manifest 1.3.0 / template v1.1.0（2026-11-10）：补四语 i18n（巡检/核对/拍板三岗）；复用三舰观测面，平台侧零写入 |
| supply-chain-recon-roles | `vrf6Qn8DH33E2cjTOwlXyg` | v2 | erp/supply-chain-recon-roles |
| supply-chain-warehousing-fleet | `fmvkNdaCcki8vm08SgozAQ` | v3 | erp/supply-chain-warehousing-fleet | **源已前进（2026-11-12，待升版）**：三岗补逐岗 `resources.mcpTools` 收窄（巡检只读 2 件 / 核对 5 件 / 拍板持确认门 4 件），`check_drift.py` 报 `agents` 漂移属预期；同时补 `coverage.json` 声明承担 `warehousing-logistics` 一格 |
| erp-payables-fleet | `t4XR0Y_irV8NriVcRyrJRA` | v2 | erp/erp-payables-fleet | 承担 general-ledger / receivables-payables / multi-currency；三岗错峰 06:12/06:19/06:26；依赖 `erp-payables-fleet-roles`（未铸造，先铸它再回填） |
| erp-payables-fleet-roles | `26ninvzoi590RYJWvTutlA` | v2 | erp/erp-payables-fleet-roles | 应收应付三岗 preset 源（逐岗工具面收窄） |
| erp-billing-fleet | `JYQw9Hmp2w0dreu5xSebqA` | v2 | erp/erp-billing-fleet | 承担 billing；拍板岗独占 `approve_billing_rule` |
| erp-billing-fleet-roles | `zntPCL4QwGhX0xebLg30Uw` | v2 | erp/erp-billing-fleet-roles | 计费三岗 preset 源 |
| erp-credit-fleet | `SpWTNNM00qh7FkPO4R7CVw` | v2 | erp/erp-credit-fleet | 承担 credit；本域内核无确认门，已在 `coverage.json` 的 `no_gate_reason` 声明 |
| erp-credit-fleet-roles | `DKndawIPc34oz6oWMHLgwQ` | v2 | erp/erp-credit-fleet-roles | 信用三岗 preset 源 |
| erp-fact-intake-fleet | `KvKIWCI8c82Q3y2c2jKFAg` | v2 | erp/erp-fact-intake-fleet | 承担 inventory；拍板岗独占 `decide_event`（事实确认门） |
| erp-fact-intake-fleet-roles | `XGP8Jn30F8cNgpJP2cZ8sg` | v2 | erp/erp-fact-intake-fleet-roles | 事件接入三岗 preset 源 |
| erp-posting-fleet | `iEobWfuJH_FGtck9140gmQ` | v2 | erp/erp-posting-fleet | 承担 approval-workflow；拍板岗独占 `approve_posting_rule` |
| erp-posting-fleet-roles | `axjqegkJ9pXQRTnZqR3knQ` | v2 | erp/erp-posting-fleet-roles | 过账三岗 preset 源 |
| erp-invoice-fleet | `sjyHNvcShH1sMF4IjuBpNg` | v3 | erp/erp-invoice-fleet | 承担 invoice / reconciliation |
| erp-invoice-fleet-roles | `QXTEuX5_ZHqVUDpP67FDoA` | v2 | erp/erp-invoice-fleet-roles | 发票对账三岗 preset 源 |；**v2=补齐「待审开票申请」三段纪律**（巡检岗 query_events 数待审为 0 也要报、核对岗看到积压不代拍、拍板岗 decide_event 过门）+ 逐岗 resources.mcpTools + 四语（es/fr/ja）。此前 roles 包落后模板包，而**线上跑的是 roles 包**（活舰队成员 target.packId 指向它）⇒ 三个岗位实际缺这段纪律且无过门工具。E5 双胞胎判据首跑抓到，2026-11-18
| erp-order-to-cash-fleet | `0Q_iR3TG-PYWVxUVqAeD6g` | v2 | erp/erp-order-to-cash-fleet | 承担 sales-orders / purchase / master-data |
| erp-order-to-cash-fleet-roles | `rpYJU2HUAhw-04tYkGC3yA` | v2 | erp/erp-order-to-cash-fleet-roles | 订单到收款三岗 preset 源 |
| erp-team-forge-roles | `1CaDqY9Caza75PGR2XHbcQ` | v6 | erp/erp-team-forge-roles | **元团队**岗位包：巡检（只读盘矩阵/市场）/ 设计（草案 + 干跑校验）/ 拍板（唯一 propose_action）；不认领矩阵任何格 |
| erp-team-forge | `1CUUpQbGnR6IZxPfQeGneQ` | v6 | erp/erp-team-forge | **元团队**：造 ERP 团队、补看板声明、把上架做成提案；三岗错峰 08:30/08:37/08:44；依赖 `erp-team-forge-roles`（`1CaDqY9Caza75PGR2XHbcQ` v1，已在架） |
| invest-team-forge-roles | `etQhiBm4nsDA7C6TjWS8EQ` | v5 | invest/invest-team-forge-roles | **投研铸造**三岗岗位包：巡检（只读盘在册投研团队与市场漂移）/ 设计（蓝图草稿 + 现役编制 + 干跑校验）/ 拍板（唯一 `propose_action`）；三岗并集是包级 allowTools 的真子集 |
| invest-team-forge | `TSHHDcwv52J6WYz_v5XtZQ` | v4 | invest/invest-team-forge | **投研铸造舰**：三岗错峰 08:30/08:37/08:44；写面只到草稿与提案；依赖 `invest-team-forge-roles`（`etQhiBm4nsDA7C6TjWS8EQ` v1）；三支柱本版为 rubric 过渡形态 |
| invest-team-grader-roles | `NB6qXcvdOHvVkkFT8v_6dg` | v4 | invest/invest-team-grader-roles | **投研评测**两岗岗位包：评测（账本只读三件，出 pending/overdue/hit_rate/as_of_gaps）/ 审计（复核评测结论，`auditor_submit_verdict` 单独落）；评测方零账本写面（禁自证） |
| invest-team-grader | `NBUjv3Z8HWNpEjblQzB6Ow` | v3 | invest/invest-team-grader | **投研评测舰**：两岗错峰 11:07/11:37；gold 只取账本与回合账本（不取报数）；隔离未成立必须标注；三支柱用结构断言；依赖 `invest-team-grader-roles`（`NB6qXcvdOHvVkkFT8v_6dg` v2） |
| supply-chain-pricing-rules-pack | `qU-5SrSdjig6GURibuZyxQ` | v1 | fd-erp-vertical-templates/templates/supply-chain/pricing-rules |
| supply-chain-pagesets-pack | `VXz1WDcoSkMrwjZ6TP48kQ` | v1 | fd-erp-vertical-templates/templates/supply-chain/pagesets |
| restaurant-industry-watch-fleet | `2vCK2UDKScEU8sqoG5lo0Q` | v2 | restaurant/industry-watch-fleet |
| flower-industry-watch-fleet | `IlHSyGGl6NP7iI7sknVc_A` | v2 | flower/industry-watch-fleet |

## 「提审」到底是什么（2026-11-15 读源码定死，替代此前那句含糊话）

此前包与 `META-LINE.md` §3 都写「一切生效动作经 `propose_action` → 拍板队列 → 已批准执行通道」。
读 `paas/lib/action-registry.js`：登记动作 **22 个**，与构造相关的只有 `facet.pack.publish`（上架）；
**没有任何建队动作**。`fleet_apply` 是 fleet-architect MCP 的 owner 工具（工具描述原文
`ALWAYS fleet_draft first … fleet_apply ONLY after the user explicitly approves`）。

所以落地是**三步、两个主体**：

| 步 | 谁做 | 机制 | 可提审？ |
|---|---|---|---|
| ① 上架包 | 装配岗 | `propose_action` + `facet.pack.publish`（带整份 manifest）→ 拍板 → 执行通道 | **是**（唯一一步） |
| ② 装包建队 | 主人 | 驾驶舱 `fleet_draft` → `fleet_apply` | 否（不是 action） |
| ③ 启动 | 主人 | 新队生来 **all-paused** | 否 |

两个后果必须记住：①「提案批了」只等于「包上架了」，队还没建；② 元线的权限边界因此更硬——
提审通道够不到建队，不需要靠提示词去禁止。

## 上架前的三个只读预演（每条都跑过，2026-11-15）

1. **参数闸**：`validateArgs("facet.pack.publish", {manifest})` → `{"ok":true}`；
   `impact` 文案渲染成「发布/更新包「…」v0.1.0 到市场」。`argKeys=[manifest, packId]`、`manifest` 必填。
2. **公开面扫描**：该动作 `outbound:true`、`contentArgs:["manifest"]` ⇒ **整份 manifest 会公开**。
   扫五类（内网 IP／凭据样／内部路径／集群名／邮箱）：候选包 **0 命中**。
3. **建队干跑**：`POST /api/fleet-architect/draft`（`Nothing is persisted`）只剩
   `target persona/<id> does not exist (or is not live)` —— persona 随包注册，**先上架、后建队**；
   顺带给出成本估算（3 轮/天、27–72 万 token/月）。

## 纪律：市场为真值，源与市场不得分叉（2026-10-30 建制）

1. **源 `manifest.json` 必须逐字段等于市场现行版**。发布任何新版前后都跑
   `python3 scripts/check_drift.py`（经 pod 通道实读市场，全绿才算完）。
   在市场侧改过显示字段（改名/改描述）而未回填源的——下次重发就会**冲掉市场改名**，
   2026-10-30 词表改名后曾全量分叉，即此律来由。
2. 模板包显示字段住 `cast.json`（name/description/tags/notes/i18n），agents/fleet 住
   `template.json`；改显示字段要重铸 `cast_fleet_template.py <dir>`，**勿手编 manifest.json 绕过 cast**
   （`--check` 会抓到）。cast.json 的 `i18n` 顶层块 = en 副轨，脚本已透传。
3. **对外可见字段（name/description/tags/notes/skills/agents）不得出现内部物**：
   fleet-packs 路径、schema.md 路径、openspec change ID、L3/harness 阶段词、节点/机器名。
   市场是外发货架（2026-10-29 泄漏事故后立的硬纪律，漂移哨也会扫这一项）。
4. `category` 保持登记 slug（fleet/fleet-shared/wire/legal…）不动；市场五线归位由
   paas 显示层 `lib/pack-category-lines.js` 完成（零迁移）。
5. 版本、改名、撞车（重名双胞胎包）随每次发布回填本台账；双胞胎待裁项在此追踪：
   - 跨境电商政策盯梢：`C8wx14…`（本仓源铸）vs `_PGsJcBwYoeZU0kCfU-2Qg`（构造间再铸）——留一退役一，待拍板
   - 投研晨会：`Bo2MWrbW5w…`（单岗）vs `YMSt-hRNiNWXrlLYC8PSiA`（官方种子）——同名门撞，待拍板


**市场策略旋钮**：`PACK_MAX_AGENTS=40`（fd-infra-deploy platform-config，2026-10-25 起；默认 3 挡四岗舰队，用户裁决给足模板人物库余量）。改后须 rollout restart facet（env 进进程）。

## 纪律红线（每包技能稿都要落）

1. **未取得不编造**：源/工具取不到的信息如实标注「未取得：<原因>」，禁止编条目补位；
2. **草稿优先**：产物创建/更新只落 draft，发布独立显式（引用 wire-build 的包尤其如此）；
3. **来源指针**：产物版本行必带 source_ref 三元组（部署/岗位/回合）；
4. **失败四分类**：凭据无效/网络不可达/上游错误/参数被拒，原文上报，禁止重试到假装成功；
5. **写操作提案制**：市场策展、agent 治理、对外发布一律「提案+拍板/动作闸」，岗位不持直写权。
6. **三支柱不自证不发布**（2026-11-13 元线建制）：任何团队制品（含元线自己的三个包）发布前
   必须自带 `memory` 四轴、逐岗 `resources.mcpTools`、`board` 段；缺任一柱即不合格制品。
   元线不要求别人做的事，自己先做——理由很实际：**不在纸上的东西永远是"有空再补"**
   （实测 47 个包里 0 个声明 `memory`、3 个用过逐岗工具面、0 个模板声明 `board`）。
   发布对象的完整结构与校验见 `META-LINE.md` §4 与 `scripts/check_release_object.py`。

> edu-watch-fleet（启材盯梢哨，edu-catalog 五工具；增量三栏清单+零增量照记）：facet v2 2026-11-07
> （add-education-content-catalog 7.4）——已并入上方机器台账（2026-11-07 A0 归位，parse_ledger 可读）。
