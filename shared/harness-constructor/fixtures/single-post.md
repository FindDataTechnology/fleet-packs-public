# 合成 brief A · 单岗节奏型

> 用途：最小可用形状。验证「一岗 + 只读面 + 一块板」这条底线能不能被构造出来，
> 以及三支柱在最小形状下是否仍然齐全（**最小不等于可以省**）。

## 一、自然语言 brief

> 帮我盯一下海关总署的公告，每天早上出一份日报，重要异动马上告诉我，顺手做一块板汇总。
> 不用做商品行情，也不用做汇率预测。

## 二、期望发布对象的形状

| 段 | 期望 |
|---|---|
| `manifest.agents` | 恰好 1 岗（监视岗），`resources.mcpTools` 非空且只读 |
| `org` | 1 个 member；`parentRef: null`；cron 落在 06:30–08:00 CST |
| `memory` | 四轴齐；该岗 `guidance` 写明「记异动判据与已排除项，不记正文」 |
| `toolkit` | 该岗仅持读面工具；不得含 publish / apply / 启停 |
| `dashboard` | 一块板，列含「日期 / 条目 / 是否异动 / 证据指针」 |
| `acceptance` | ≥4 条；`post-materialized` + `first-round-fired` + `report-landed` + 三支柱各 1 条 |
| `cost` | 1 回合/日，tokensPerTurn 有取值 |

## 三、坏设计清单（每条都必须让某个断言判红）

1. **去掉唯一岗位的 `target`** → `post-materialized` 判红（岗位未落位）。
2. **让该岗不报数**（promptTemplate 里删掉 report_status） → `report-landed` 判红。
3. **看板段写空**（`dashboard` 显式空值但 `posts` 也没有接线人） → dashboard 支柱断言判红。
4. **把 cron 定成每 5 分钟** → 不判红，但应被 `cost` 预估拦下（记为告警，不算绿）。
5. **`memory.guidance` 写「记全部原文」** → memory 支柱断言判红（章程与上下文预算矛盾）。
