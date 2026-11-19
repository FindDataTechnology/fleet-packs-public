#!/usr/bin/env node
// fleet-packs/adopted/prep_publish.mjs — 转正产物发布前处理（adopt-curated-external-skills 挂账①）
//
// 把 scripts/adopt-curated-skills.mjs 的英文基座产物，改成本仓市场的中文门面：
//   · 基座 name/description/agent/card/examples 落中文（卡面层=市场展示面）
//   · 原英文值**自动捕获**进 i18n.en 覆盖层（人抄必漂）
//   · 每份过发布门同一校验器 validatePackManifest，零错才写盘
// 产物落 adopted/anthropic-skills/<slug>/manifest.json + adopted/dev-review-team/。

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { validatePackManifest } from "/Users/chengsishi/paas/lib/pack-manifest.js";

const RAW = "/tmp/curated-raw";
const OUT = "/Users/chengsishi/finddata/fleet-packs/adopted";

const ZH = {
  "frontend-design": {
    name: "前端视觉设计",
    desc: "贴入界面代码或设计意图，给方向性的视觉判断：审美定位、字体、版式、层级与间距。转正自 anthropics/skills 的 frontend-design（Apache-2.0，钉版）；无 shell/文件触手，只读你贴进对话的内容。",
    examples: ["这份落地页的视觉方向帮我定一下：配色、字体、留白。", "这个界面太“模板脸”了，怎么改得有自己的性格？"],
  },
  "web-artifacts-builder": {
    name: "网页产物搭建",
    desc: "用现代前端技术（React/Tailwind/shadcn）搭建多组件 HTML 产物的工程指南：结构拆分、状态管理、单文件可交付。转正自 anthropics/skills 的 web-artifacts-builder（Apache-2.0，钉版）；无执行触手，代码方案由你落地。",
    examples: ["帮我搭一个多标签数据看板的页面结构（React+Tailwind）。", "这个 artifact 的组件拆分思路对吗？贴现在的结构给你。"],
  },
  "webapp-testing": {
    name: "Web 应用测试",
    desc: "用 Playwright 验证前端功能与排查 UI 行为的指南：选择器策略、等待条件、截图取证、用例编排。转正自 anthropics/skills 的 webapp-testing（Apache-2.0，钉版）；不代跑测试，给可直接落地的用例设计。",
    examples: ["给这个登录流程设计 Playwright 测试步骤。", "这个用例在 CI 里老飘，帮我找等待条件的坑。"],
  },
  "canvas-design": {
    name: "画布视觉设计",
    desc: "海报、封面、图形作品的 PNG/PDF 视觉稿创作方法：构图、层次、设计语言。转正自 anthropics/skills 的 canvas-design（Apache-2.0，钉版）；无绘图执行触手，产出可执行的设计方案。",
    examples: ["给技术沙龙做一张海报：主题、构图、配色讲一遍。", "这张封面贴给你，构图层次怎么改？"],
  },
  "theme-factory": {
    name: "主题风格工厂",
    desc: "给幻灯片、文档、报告、落地页统一套主题的工具箱：10 套预设主题 + 选主题的方法。转正自 anthropics/skills 的 theme-factory（Apache-2.0，钉版）；产出样式方案，不直接改你的文件。",
    examples: ["这份季度报告该用哪套主题？目标风格：克制、权威。", "把这套幻灯片的配色讲成可复用的主题令牌。"],
  },
  "algorithmic-art": {
    name: "算法生成艺术",
    desc: "用 p5.js 做种子随机与参数探索的生成艺术：算法构图、互动参数控制。转正自 anthropics/skills 的 algorithmic-art（Apache-2.0，钉版）；产出 sketch 思路与代码方案。",
    examples: ["用噪声场+粒子轨迹做一张海报的算法方案。", "这个生成图案太乱，帮我调参数控制复杂度。"],
  },
  "mcp-builder": {
    name: "MCP 服务构建",
    desc: "构建高质量 MCP server 的工程指南：工具设计、命名、错误语义、测试与发布面。转正自 anthropics/skills 的 mcp-builder（Apache-2.0，钉版）；产出设计与代码方案，部署在你的工程侧。",
    examples: ["我这个 server 的工具名和粒度合理吗？贴 schema 给你。", "给一个新 MCP 设计工具面：读什么、判什么、报什么错。"],
  },
  "skill-creator": {
    name: "技能创作",
    desc: "从零写技能、改技能、量技能效果的方法：触发描述、边界与验收。转正自 anthropics/skills 的 skill-creator（Apache-2.0，钉版）；产出 SKILL.md 草案与评审意见。",
    examples: ["我想做一个“周报生成”技能，帮我起草触发描述。", "这个技能老被误触发，怎么改窄 description？"],
  },
  "slack-gif-creator": {
    name: "Slack 动图制作",
    desc: "做 Slack 适用的动图：尺寸与体积约束、校验工具、动画思路。转正自 anthropics/skills 的 slack-gif-creator（Apache-2.0，钉版）；产出方案与参数建议。",
    examples: ["这个 GIF 在 Slack 被压糊了，尺寸怎么定？", "想要“打字机”效果的加载动图，给方案。"],
  },
  "internal-comms": {
    name: "内部沟通写作",
    desc: "内部沟通文体模板与写法：状态更新、周报、公告、变更通知。转正自 anthropics/skills 的 internal-comms（Apache-2.0，钉版）；产出文本草稿。",
    examples: ["把“系统迁移完成”写成一条变更通知。", "周报帮我按“进展/风险/下周”改一版。"],
  },
  "brand-guidelines": {
    name: "Anthropic 品牌规范",
    desc: "Anthropic 官方品牌色与字体的使用规范，供需要贴合其视觉语言的产物引用。转正自 anthropics/skills 的 brand-guidelines（Apache-2.0，钉版）。",
    examples: ["按 Anthropic 品牌规范给这份 deck 定色板。", "这个 banner 的字体层级和官方规范对得上吗？"],
  },
  "academy-guide": {
    name: "Claude 课程导航",
    desc: "回答“Claude 怎么用”类问题时推荐匹配的学习路径与课程材料。转正自 anthropics/skills 的 academy-guide（Apache-2.0，钉版）。",
    examples: ["新手想用 Claude 做数据分析，先看什么？", "怎么把 Claude 接进内部文档流程？给条学习路径。"],
  },
  "discernment-nudge": {
    name: "结论核验提示",
    desc: "交出可能被照做的结论前，补一句「哪些地方值得你自己再核」，防盲信。转正自 anthropics/skills 的 discernment-nudge（Apache-2.0，钉版）。",
    examples: ["这份方案里哪些结论我必须自己再验证？", "给这段建议加上核验清单，按风险排。"],
  },
};

const slugs = Object.keys(ZH);
const failures = [];

for (const slug of slugs) {
  const zh = ZH[slug];
  const raw = JSON.parse(await readFile(path.join(RAW, slug, "manifest.json"), "utf8"));

  // 英文原值先捕获——覆盖层从捕获值回填，不手抄
  const en = {
    name: raw.name,
    description: raw.description,
    cardName: raw.agents[0].serving?.card?.name,
    cardDescription: raw.agents[0].serving?.card?.description,
  };

  const m = {
    ...raw,
    name: zh.name,
    description: zh.desc,
    i18n: {
      en: {
        name: en.name,
        description: en.description,
        agents: {
          [raw.agents[0].id]: {
            name: en.name,
            serving: { card: { name: en.cardName, description: en.cardDescription } },
          },
        },
      },
    },
    agents: [
      {
        ...raw.agents[0],
        name: zh.name,
        description: zh.desc,
        serving: {
          ...raw.agents[0].serving,
          card: { ...raw.agents[0].serving.card, name: zh.name, description: zh.desc, examples: zh.examples },
        },
      },
    ],
  };

  const errors = validatePackManifest(m);
  if (errors.length > 0) {
    failures.push([slug, errors.map((e) => `${e.entry || "manifest"}: ${e.error}`).join(" | ")]);
    continue;
  }
  const dir = path.join(OUT, "anthropic-skills", slug);
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, "manifest.json"), `${JSON.stringify(m, null, 2)}\n`);
  console.log(`OK ${slug.padEnd(24)} -> ${dir}`);
}

// 研发评审小队：paas 仓源直拷进 adopted/（内容基线测试已在 paas 仓绿）
{
  const src = "/Users/chengsishi/paas/packs/dev-review-team/manifest.json";
  const m = JSON.parse(await readFile(src, "utf8"));
  const errors = validatePackManifest(m);
  if (errors.length > 0) failures.push(["dev-review-team", JSON.stringify(errors)]);
  else {
    await mkdir(path.join(OUT, "dev-review-team"), { recursive: true });
    await writeFile(path.join(OUT, "dev-review-team", "manifest.json"), `${JSON.stringify(m, null, 2)}\n`);
    console.log(`OK dev-review-team          -> ${OUT}/dev-review-team`);
  }
}

if (failures.length > 0) {
  console.error(`\n校验失败 ${failures.length} 份：`);
  for (const [s, e] of failures) console.error(`  ${s}: ${e}`);
  process.exit(1);
}
console.log("\n14/14 全部过 validatePackManifest");
