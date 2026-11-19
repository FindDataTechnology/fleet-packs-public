# ERP 团队铸造舰（erp-team-forge）使用说明

元团队：**造 ERP 团队、补看板声明、把上架做成提案**。它不认领 ERP 矩阵任何格——生产的是团队本身，不是账务作业。

## 文件

```
erp/erp-team-forge-roles/     岗位包（三岗 preset，无 fleet 段）
  ├── template.json            agents[] + resources.mcpTools 逐岗收窄
  ├── cast.json                包壳
  └── manifest.json            铸造产物（verify-only，未上架）
erp/erp-team-forge/           舰队模板包
  ├── template.json            agents[] + fleet{members} + console + monitoring + acceptance + memory
  ├── cast.json
  └── coverage.json            {"meta": true} —— 元团队声明，不认领矩阵格
```

## 三岗

| 岗 | 工具面（逐岗收窄） | 干什么 | 干不了什么 |
|---|---|---|---|
| 巡检岗 `erp-forge-watcher` | `fleet_list` / `fleet_spec_read` / `fleet_turns_read` / `facet_market_pack_browse` / `facet_market_pack_read` | 盘矩阵格承担者、市场未上架包、源↔市场漂移、团队看板有无 | 零写面 |
| 设计岗 `erp-forge-reconciler` | 巡检面 + `fleet_draft` + `facet_market_pack_validate` | 按缺口出草案（编制/节奏/KPI 槽位/逐岗工具面/看板声明），跑包级干跑校验 | 无上架权、无 `propose_action` |
| 拍板岗 `erp-forge-decider` | `facet_market_pack_read` + `propose_action` | 核对收口版本与源仓一致后，把上架做成**提案**送拍板队列 | 不写草案、不自行执行上架 |

节奏 08:30 / 08:37 / 08:44（Asia/Shanghai），与既有 ERP 棋盘 06:10–07:24 错开。
看板声明：`team` / `kpi` / `stream`（两面可见）+ `decisions` / `spend`（仅平台面）；headline = `decisions.open`。

## 铸造顺序（依赖未解不可上架）

```bash
cd fleet-packs
# 1) 岗位包先上架取 packId（上架是外向动作，需主人批准）
python3 tooling/publish_pack.py erp/erp-team-forge-roles/manifest.json --author <email>
# 2) 把拿到的 packId 回填 README 机器台账的本行
#    | erp-team-forge-roles | `<packId>` | v1 | erp/erp-team-forge-roles | ...
# 3) 铸舰队包（依赖已解析）+ 上架
python3 tooling/cast_fleet_template.py erp/erp-team-forge
python3 tooling/publish_pack.py erp/erp-team-forge/manifest.json --author <email>
```

铸造脚本在依赖未铸造时会 fail loud 并指名缺什么——不会带 `{{PACK_ID:...}}` 占位上架。

## 判据（全部可机检）

```bash
python3 scripts/check_erp_team_coverage.py    # 21 格矩阵 ↔ 团队编制 ↔ 工具面 + 看板声明
python3 tooling/validate_manifest.py erp/erp-team-forge/manifest.json
python3 ~/.agents/skills/facet-pack-builder/scripts/validate_manifest.py erp/erp-team-forge/manifest.json
cd ../fd-erp && python3 tools/check_all.py    # 含上面的团队覆盖闸
```

团队覆盖闸对元团队的两处豁免都是**结构性的**，不是白名单：

- `coverage.json` 里 `"meta": true` → 免「认领矩阵格」这一件，其余编制判据照跑；
- 团队 `mcpServers` 不含 `fd-erp-ledger-mcp` → 内核工具面判据（确认门/写面/工具名在册）不适用，不拿内核面去核一个不调内核的岗位。
