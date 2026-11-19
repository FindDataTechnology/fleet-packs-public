#!/usr/bin/env bash
# 元线全闸（openspec: add-meta-fleet-line / §6 总入口）
#
# 一条命令跑完本变更自带的全部可机检证据，每道闸都自带负向对照（证明它能红）。
# 为什么要有这个入口：散落的门等于没有门——没人会记得按顺序跑七个脚本，
# 而漏跑一道就会让「全绿」变成一个没人验证过的说法。
#
# 用法（在仓内任意目录 / 任意 clone 目录名下都成立）：
#   bash scripts/check_meta_line_all.sh
# 退出码：0=全绿；1=有闸红；2=不在仓内。
#
# 唯一不需要外部东西的假设：E3 用**钉住的**裁决引擎，所以没有 paas 检出也能判；
# 有 paas 检出时会顺手跑漂移臂（钉的那份 vs 线上那份逐例比）。末尾会明说跑的是哪种。

set -uo pipefail
# 定位本仓根。不要用 `../..` 再拼 `fleet-packs/`——那等于要求「clone 目录必须叫
# fleet-packs」，换任何别的目录名（`git clone … fp`）九道检查就全部找不到脚本。
cd "$(dirname "$0")/.." || exit 2
[ -f scripts/check_meta_line.py ] || { printf '不在 fleet-packs 仓内：%s\n' "$PWD"; exit 2; }

FAILED=0
run() {
  local label="$1"; shift
  printf '\n=== %s ===\n' "$label"
  if "$@"; then
    printf '✅ %s\n' "$label"
  else
    printf '❌ %s (exit %s)\n' "$label" "$?"
    FAILED=1
  fi
}

# 三态的 runner：2＝判不出。干净克隆里读不到 paas/fd-memory 源码是「没核」，不是「都合规」，
# 所以不计红——但要在结尾把「这次全绿没覆盖到哪一格」说出来，否则绿的边界就没人知道。
UNVERIFIED=""
run_optional() {
  local label="$1"; shift
  printf '\n=== %s ===\n' "$label"
  "$@"
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '✅ %s\n' "$label"
  elif [ "$rc" -eq 2 ]; then
    printf '⚠️  %s：判不出（不计红；这一格本轮没有证据）\n' "$label"
    UNVERIFIED="${UNVERIFIED}${label}; "
  else
    printf '❌ %s (exit %s)\n' "$label" "$rc"
    FAILED=1
  fi
}

# E1 结构门（三支柱 / 写面禁令 / 节奏错峰）
run "E1 元线结构门" python3 scripts/check_meta_line.py
# E2 结构门的负向对照（11 臂）
run "E2 结构门负向对照" python3 scripts/check_meta_line.py --self-test
# E1' 发布对象结构校验（八段 / 三支柱断言 / kind 闭集 / 证据）
run "E1' 发布对象校验（自检）" python3 scripts/check_release_object.py --self-test
# E1'-I 存量发布对象盘点（S7）：已写的那些必须持续过 S1–S6，且集合不得静默缩水。
#      覆盖面（今天 2/21 个 public+fleet 包有对象）只报数不判红——按「每个包都要有」判
#      会得到一道常年红的闸；缩水才红，因为那意味着一个可提审的包静默退出通道。
run "E1'-I 存量发布对象盘点" python3 scripts/check_release_object.py --inventory --check scripts/release-objects.snapshot.json
# E1'' 构造师产物（v2 垂直蓝图）结构门——此前没有任何闸看 blueprints/*/blueprint.json：
#      M7 扫的是 manifest，E1' 查的是另一种八段发布对象，仓里没有一份文件是那个形状。
run "E1'' 蓝图结构门" python3 scripts/check_blueprint.py
run "E1'' 蓝图结构门负向对照" python3 scripts/check_blueprint.py --self-test
# E1-T 工具名体检（声明的每件工具必须在 registry 源码里真存在；
#      真校验器对 spine 投影不认识的四个 registry 只打「未校验」就放过）
run_optional "E1-T 工具名体检" python3 tooling/check_tool_names.py
run_optional "E1-T 工具名体检负向对照" python3 tooling/check_tool_names.py --self-test
# E3 假宇宙（3 份 brief 好证据全绿 + 12 条坏设计各判红 + 9 条判据边界）
run "E3 假宇宙" node scripts/trial_fake_universe.mjs
# E5 双胞胎判据（fleet-drift-and-hygiene）：模板包与岗位包声明同名岗位时，
#      persona 与 allowTools 必须逐字一致，除非登记派生。纯仓内、不需网络，
#      所以它在干净 clone 里也成立。144 行的第四道闸此前没人看：7 对里 1 对已漂。
run "E5 双胞胎判据" python3 scripts/check_twin_packs.py
run "E5 双胞胎判据负向对照" python3 scripts/check_twin_packs.py --self-test
# E6 成员闭包判据：模板成员的 presetId 必须在装完之后真能解析到 agent id，
#      否则 fleet-provisioner 静默把岗位标 degraded（岗位照建、能力没了、不报错）。
#      按实装代码定语义：presetId 查的是运行时 catalog（已装包的 agents[] 并集），
#      target.packId 只是共享技能/工具来源，不是 preset 的住处。
run "E6 成员闭包判据" python3 scripts/check_closure.py
run "E6 成员闭包判据负向对照" python3 scripts/check_closure.py --self-test
# E7 可见性与版本判据：官方元线三支必须在公开列表可被发现（2026-11-18 裁定），
#      台账版本与市场版本的偏差要可读。需要 facet 通道 → 用 run_optional，
#      读不到时记「判不出」而不是假绿（离线纪律见 D3）。
run_optional "E7 可见性与版本判据" python3 scripts/check_visibility.py
run "E7 可见性与版本判据负向对照" python3 scripts/check_visibility.py --self-test
# E8 跨仓副本判据：同名 agent 定义在多仓各存一份会各自演化，且方向不同
#      （2026-11-18 实测四份 impeccable-* 全部不同，fd-erp 侧更完整）。
#      需要兄弟仓 → run_optional：干净 clone 里没有邻居时就记「判不出」。
run_optional "E8 跨仓副本判据" python3 scripts/check_cross_repo_copies.py
run "E8 跨仓副本判据负向对照" python3 scripts/check_cross_repo_copies.py --self-test
# E9 对外词表判据：2026-10-29/30 裁定的对外词（智能体团队 / 智能体 / 智能体市场）
#      此前只有一句叮嘱、没有读者——locale 里留着「素材市场」「已装功能集」等七处旧词。
#      白名单式：每个受管键要么取正确词，要么显式登记豁免并说明理由。
run_optional "E9 对外词表判据" python3 scripts/check_glossary.py
run "E9 对外词表判据负向对照" python3 scripts/check_glossary.py --self-test
# E10 四语覆盖判据：发布门要求 in-catalog 包带 en/ja/es/fr，但有 21 个在册包只写了 en——
#      谁去改它们中任何一个，发版都会被门直接拒，而失败原因与他要改的内容毫无关系。
#      判据把这条阻塞在动手之前说清楚。纯仓内（只读 manifest 与台账），离线可跑。
run "E10 四语覆盖判据" python3 scripts/check_locale_coverage.py
run "E10 四语覆盖判据负向对照" python3 scripts/check_locale_coverage.py --self-test
# E11 发布面泄漏判据：**manifest 的每个字段都会被原样发布**——2026-11-18 实测线上
#      能读到整段 _casting.authorNotes（我曾以为它不对外投影）。所以内部语汇、仓内
#      路径、change 名、基础设施标识、作者台账都不能放在 manifest 的任何字段里。
#      扫整个文档（不像 check_drift 只看 OUTWARD_FIELDS 那五个键，恰好漏掉 _casting）。
run "E11 发布面泄漏判据" python3 scripts/check_publish_leak.py
run "E11 发布面泄漏判据负向对照" python3 scripts/check_publish_leak.py --self-test
# E12 开源快照可构建：本仓要公开，但同一棵树里躺着运维面（ssh 隧道、内网地址、
#      发布凭据引用）。这道闸构建一次公开快照并对**产物本身**做 15 类禁品扫描——
#      任何一处命中就红，且不产出「看起来还行」的快照。它同时守住「scrub 表与
#      排除清单没跟实现漂移」（每条 scrub 带预期匹配数，失配即失败）。
run "E12 开源快照可构建" python3 scripts/make-public-snapshot.py --check
# E4 回放对齐（真语料 vs SQL 真值）
run "E4 回放对齐" python3 scripts/check_replay_alignment.py
# E4' 对齐器负向对照
run "E4' 对齐器负向对照" python3 scripts/check_replay_alignment.py --self-test
# 目录不陈旧 / 无手写增补
run "目录门" python3 scripts/gen_catalog.py --verify

# E3 的引擎来源与漂移臂跑没跑，是「这次全绿证明了什么」的一部分，不能留在滚动输出里。
# 判据与 E3 自己的解析顺序一致：--paas-root > FP_PAAS_ROOT > 同级 paas/。
PAAS_LIVE="${FP_PAAS_ROOT:-../paas}"
printf '\n────\n'
printf 'E3 引擎：钉住的转录版'
if [ -f "$PAAS_LIVE/server/blueprint-trial.js" ]; then
  printf '（另有 %s 参与漂移比对）\n' "$PAAS_LIVE"
else
  printf '；漂移臂：未验（本机无 paas 检出，给 --paas-root 或 FP_PAAS_ROOT 可比）\n'
fi

if [ -n "$UNVERIFIED" ]; then
  printf '⚠️  本轮未覆盖：%s（判不出，不计红）\n' "$UNVERIFIED"
fi
if [ "$FAILED" -eq 0 ]; then
  printf 'meta-line-all 绿\n'
else
  printf 'meta-line-all 红\n'
fi
exit "$FAILED"
