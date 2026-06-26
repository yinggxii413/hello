# 财报 & X 监控 & AI 简报 自动化系统 · 说明文档

> 仓库：`yinggxii413/hello`（GitHub，公开仓库 → Actions 无限免费）
> 最后更新：2026-06-26

---

## 一、总览

三套独立的自动化，都跑在 GitHub Actions 上，互不影响：

| 系统 | 作用 | 主脚本 | 工作流 |
|---|---|---|---|
| **财报系统** | 盘后抓财报实际数据 + 电话会，按板块分发到 9 个 Discord 频道 | `earnings_monitor.py` | `earnings.yml` |
| **X 监控** | 监控指定 X(Twitter)账号，翻译成中文推送 Discord | `monitor.py` | X 工作流(如 `x-to-discord.yml`) |
| **AI 简报系统** | 每早 6:13 联网生成中文「AI 基础设施每日简报」，自动发到 Discord | `generate_and_post.py` | `daily-briefing.yml` |

---

## 二、仓库文件结构

```
hello/  (仓库根目录)
├── earnings_monitor.py        财报脚本
├── earnings_state.json        财报去重状态(脚本自动维护，勿手动改)
├── monitor.py                 X 监控脚本
├── state.json                 X 去重状态(脚本自动维护，勿手动改)
├── generate_and_post.py       AI 简报脚本(生成 + 发送 + 去重)
├── briefing_state.json        AI 简报去重状态(脚本自动维护，勿手动改)
├── requirements.txt           依赖：requests + openai + anthropic
└── .github/workflows/
    ├── earnings.yml           财报定时任务
    ├── (X 工作流文件)          X 监控定时任务
    └── daily-briefing.yml      AI 简报定时任务
```

---

## 三、Secrets 清单（GitHub → Settings → Secrets and variables → Actions）

**财报系统（11 个）**
- `FINNHUB_API_KEY` — Finnhub 财经数据
- `OPENAI_API_KEY` — OpenAI(生成中文分析 / 翻译，财报与 X 两套共用)
- `WEBHOOK_EQUIP` → 频道 `1-封装设备`
- `WEBHOOK_STORAGE` → `2-存储`
- `WEBHOOK_COMPUTE` → `3-算力芯片`
- `WEBHOOK_OPTICAL` → `4-光模块`
- `WEBHOOK_NEOCLOUD` → `5-Neocloud`
- `WEBHOOK_MAG7` → `6-Mag7`
- `WEBHOOK_SPACE` → `7-航天机器人`
- `WEBHOOK_ENERGY` → `8-储能`
- `WEBHOOK_QUANTUM` → `9-量子`

**X 监控（3 个 + 共用 OpenAI）**
- `X_BEARER_TOKEN` — X API(按量付费)
- `DISCORD_WEBHOOK` → Serenity 账号频道
- `TRUMP_WEBHOOK` → Trump Truth 账号频道
- （`FINANCIAL_JUICE_WEBHOOK` — financialjuice，已停用）
- `OPENAI_API_KEY` — 与财报共用

**AI 简报系统（2 必填 + 1 可选）**
- `ANTHROPIC_API_KEY` — Anthropic API(生成简报，**与财报/X 的 OpenAI 是两套独立计费**)
- `BRIEFING_WEBHOOK_URL` → AI 基础设施简报频道(独立命名，不与现有 `WEBHOOK_*` / `DISCORD_WEBHOOK` 冲突)
- `BRIEFING_MODEL` —（可选）模型名；留空默认 `claude-sonnet-4-6`，省钱填 `claude-haiku-4-5-20251001`

> 🔐 密钥都存在 Secrets 里、不在代码中；仓库公开也不会泄露。

---

## 四、财报系统详解

### 数据流
1. 每个交易日定时跑（见下方 cron）。
2. 用 **Finnhub 财报日历** 找"已披露实际财报"的清单内公司（有 `epsActual/revenueActual`）。
3. 抓实际数据 + 关键指标 + 同业 PE → **OpenAI 生成中文报告** → 按板块发到对应频道。
4. 白名单股票额外：**免费抓 Motley Fool 电话会转录 → 提炼「电话会核心」** → 同频道追发一条。
5. `earnings_state.json` 去重（数据报告用 `post-` 键，电话会用 `call-` 键，各自独立）。

### 报告内容
- **数据报告**：①财报重点数据(实际 vs 预期 + 同比) ②盈利与估值 ③同业估值对比 ④速览
- **电话会核心**(白名单)：业绩与指引 / 业务亮点 / 隐忧 / 大客户订单 / **管理层原话** / Q&A / 风险

### 观察清单（9 大类，产业链上游→下游）
1. **封装设备**：AMAT, ONTO, KLAC, CAMT, FORM, AMKR, AEHR, ASML
2. **存储**：MU, SNDK, KIOXIA, STX, WDC, 005930.KS(三星), 000660.KS(海力士)
3. **算力芯片**：TSM, AVGO, AMD, ARM, NOK, INTC, MRVL, QCOM, ALAB, VSH
4. **光模块**：POET, COHR, CRDO, AAOI, AXTI, LITE, FOTO, SIVEF, FN
5. **Neocloud**：IREN, NBIS, HUT, ORCL, WULF, NVTS, CRWV
6. **Mag 7**：AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA
7. **航天机器人**：DXYZ, SATS, RKLB, ASTS, FLY, LUNR, PATH, SIDE, RR, SPCE, SPCX
8. **储能**：BE, CEG, ENPH, NEE, FLNC, CSIQ, EOSE
9. **量子**：IONQ, QUBT, QBTS, LAES, QTUM

### 电话会白名单
- 默认 = 观察清单里**除以下外的全部 66 只**（自动派生，改清单会自动同步）：
  排除 `005930.KS, 000660.KS, KIOXIA`(非美上市)、`QTUM`(ETF)、`DXYZ`(封闭基金)——这些没有可抓的电话会。
- Motley Fool 覆盖主流美股；冷门小票若没转录，脚本**自动跳过、不报错**。

### 数据源
- **财报数字** → Finnhub（免费档，60 次/分钟）
- **电话会转录** → Motley Fool 网页（免费抓取，无需 key）
- **AI 分析** → OpenAI（默认 `gpt-4o-mini`）

### 定时（cron，UTC）
```
30 12 * * *   # 北京 20:30
30 20 * * *   # 北京 04:30
30 22 * * *   # 北京 06:30
```
覆盖美股盘前/盘后。电话会若晚发布，下一班自动补发（3 天回看窗口内重试）。

---

## 五、X 监控系统详解

### 数据流
1. 定时跑（默认每 10 分钟，`*/10 * * * *`）。
2. 对每个账号：用缓存的 user_id + `since_id` → **只取新推**（没新推 = 0 读 = 0 花费）。
3. 新推 → OpenAI 翻译中文 → 发对应频道（embed 卡片，含原文+译文+链接）。
4. `state.json` 记录每账号 user_id + 最新推文 id。

### 监控账号
- `aleabitoreddit`(Serenity) → `DISCORD_WEBHOOK`
- `TrumpDailyPosts`(Trump Truth) → `TRUMP_WEBHOOK`
- `financialjuice`(已停用，代码中注释保留)

### 重要说明
- **不是实时**：GitHub 定时触发，X 新推要等下一班才推，延迟约 10–20 分钟。
- 想要 1–2 分钟准实时 → 需上常驻主机（Oracle 免费 VM 等），代码已备好常驻版（`ONESHOT` 控制单次/循环）。

---

## 六、AI 基础设施简报系统详解

### 数据流
1. 每天 **6:13（北京）** 定时触发（也可在 Actions 手动 Run workflow）。
2. 读 `briefing_state.json` → 取最近 4 天发过的标题作为「禁止重复」清单。
3. 调 **Anthropic API + 联网搜索**（最多 6 次）→ 只选最近约 24 小时内的新消息。
4. 按固定排版生成简报（emoji 编号、加粗、【】、分隔线，**全文无任何链接**）。
5. 分段发到 Discord（自动处理 2000 字符上限 + 自带 User-Agent 避开 Discord 403）。
6. 成功后把当天标题写回 `briefing_state.json` 并提交（**rebase + 重试**，避开和 X 监控 `*/10` 的并发 push 冲突）。

### 简报结构
- 标题 + 约 5 条要点（每条：核心事件 +【产业链影响】+【机会/风险】）+ 今日重点观察 + 我的判断。
- 覆盖方向：存储/HBM、NVIDIA/算力平台、数据中心电力、半导体宏观面。

### 去重机制
- `briefing_state.json` 记录每天发过的标题（保留最近 10 天）；次日生成时排除最近 4 天的，叠加「只要新消息」的提示，避免重复旧闻（如重复发同一条财报）。

### 数据源 & 模型
- **新闻 + 数字** → Anthropic 内置联网搜索（`web_search`，单次调用内含若干次搜索）。
- **生成** → Anthropic（默认 `claude-sonnet-4-6`；可加 `BRIEFING_MODEL` Secret 切 Haiku 省钱）。

### 定时（cron，UTC）
```
13 22 * * *   # 北京 06:13（设早留缓冲，确保 8:00 前发出；用 :13 非整十分钟，避开 X 监控并发）
```

---

## 七、常见维护操作

| 想做什么 | 怎么做 |
|---|---|
| **加/删财报股票** | 改 `earnings_monitor.py` 里 `CATEGORIES` 对应板块的 `tickers` 列表 |
| **改频道路由** | 改 `CATEGORIES` 里该板块的 `env`，并在 GitHub 配对应 Secret |
| **改电话会白名单** | 默认自动派生；想自定义 → 在 `earnings.yml` 的 env 加 `TRANSCRIPT_TICKERS: "MU,NVDA,AVGO"` |
| **改财报推送时间** | 改 `earnings.yml` 的 `cron`（UTC 时间） |
| **改 AI 模型(财报)** | env 加 `OPENAI_MODEL: "gpt-4o"`（更深度）或保持 mini（更省） |
| **加 X 监控账号** | 改 `monitor.py` 的 `ACCOUNTS`，加一项并配一个新 webhook Secret |
| **改 X 轮询频率** | 改 X 工作流的 `cron`（注意私有仓库分钟数；公开仓库无限） |
| **临时测试财报** | `earnings.yml` 临时加 `REPORTED_LOOKBACK: "30"` + 清空 `earnings_state.json` 为 `{}`，跑完删掉 |
| **改简报时间** | 改 `daily-briefing.yml` 的 `cron`（UTC；北京=UTC+8，避开整十分钟），同步改脚本顶部 `hours=8` |
| **改简报内容/板块/格式** | 改 `generate_and_post.py` 里的 `build_prompt()` |
| **简报换模型/省钱** | 加 Secret `BRIEFING_MODEL = claude-haiku-4-5-20251001`；删掉即回到 Sonnet |
| **改简报搜索次数** | 改 `generate_and_post.py` 里 `max_uses`（当前 6，越大越全越贵） |
| **临时测试简报** | Actions → `Daily AI Infra Briefing` → Run workflow（注意每次都花 API 钱） |

---

## 八、成本

- **财报**：Finnhub 免费、Motley Fool 免费、GitHub Actions 免费(公开仓库)；只有 OpenAI 按量，每财报季约 **几毛~几十块人民币**（mini 极便宜）。
- **X 监控**：X API **按量付费**（$0.005/条读），用 `since_id` 后只为真正的新推付费；翻译费极小。高频账号(如 financialjuice)是成本大头，已停用。
- **AI 简报**：GitHub Actions 免费；Anthropic API 按量——
  Sonnet 约 **$0.2–0.35/天（月 $6–10）**；换 Haiku（加 `BRIEFING_MODEL`）约 **$0.1/天（月 $3）**。
  ⚠️ 这是**独立于 OpenAI 的另一个余额**：Anthropic Console 里 Auto reload 默认关闭，余额到 0 会停发，记得充值或开小额自动充值。

---

## 九、故障排查

| 现象 | 可能原因 / 处理 |
|---|---|
| Actions 报 `KeyError: 'XXX_API_KEY'` | 对应 Secret 没配或名字打错（区分大小写） |
| 工作流成功但 Discord 没消息 | 1) 窗口内无新财报(正常静默) 2) 该公司已去重 3) webhook 指向的频道和你看的不一致 |
| 报告把公司名写错 | 已修(`fetch_company_name` 取真实名)；确认用的是最新 `earnings_monitor.py` |
| Discord 消息被截断 | 已修(自动分段)；确认最新脚本 |
| 电话会一直没出 | Motley Fool 暂未发布(会重试)，或该票不被覆盖(韩股/ETF/冷门小盘) |
| `Node.js 20 deprecated` 警告 | 无害，可忽略 |
| AI 简报没按时发 | Actions 看今天的 `Daily AI Infra Briefing`：黄点=延迟(等)；红叉=看日志；无记录=schedule 未触发(新建后首跑可能晚) |
| AI 简报 403 Forbidden | 发送被 Discord 拦截——脚本已带 User-Agent；若出现先查 `BRIEFING_WEBHOOK_URL` 是否正确 |
| AI 简报重复旧闻 | 查 `briefing_state.json` 是否在回写（Actions 日志有 "update briefing state" 提交） |
| AI 简报余额报错 | Anthropic Console 充值；与 OpenAI 余额是两套，别搞混 |

---

## 十、未来待办（想做时回来继续）

1. **市场解读**：抓财报后的新闻/分析师观点，单独开频道。
2. **X 大V 解读**：抓指定大V 对该财报的评论（用现有 X API）。
3. **图片卡片**：把报告渲染成信息图（HTML→PNG 发 Discord）。
4. **准实时 X**：上 Oracle 免费 VM 跑常驻轮询版，延迟降到 1–2 分钟。
5. **简报与财报联动**：财报日把当天 AI 简报里相关板块和财报推送做交叉引用。
6. **简报双语版**：增加中英双语开关。
7. **简报降成本**：评估切到 OpenAI `gpt-4o-mini` + 联网搜索，复用现有 OpenAI 账单（月成本几毛）。
