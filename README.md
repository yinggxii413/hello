# 财报 & X 监控 自动化系统 · 说明文档

> 仓库：`yinggxii413/hello`（GitHub，公开仓库 → Actions 无限免费）
> 最后更新：2026-06-15

---

## 一、总览

两套独立的自动化，都跑在 GitHub Actions 上，互不影响：

| 系统 | 作用 | 主脚本 | 工作流 |
|---|---|---|---|
| **财报系统** | 盘后抓财报实际数据 + 电话会，按板块分发到 9 个 Discord 频道 | `earnings_monitor.py` | `earnings.yml` |
| **X 监控** | 监控指定 X(Twitter)账号，翻译成中文推送 Discord | `monitor.py` | X 工作流(如 `x-to-discord.yml`) |

---

## 二、仓库文件结构

```
hello/  (仓库根目录)
├── earnings_monitor.py        财报脚本
├── earnings_state.json        财报去重状态(脚本自动维护，勿手动改)
├── monitor.py                 X 监控脚本
├── state.json                 X 去重状态(脚本自动维护，勿手动改)
├── requirements.txt           依赖：requests + openai
└── .github/workflows/
    ├── earnings.yml           财报定时任务
    └── (X 工作流文件)          X 监控定时任务
```

---

## 三、Secrets 清单（GitHub → Settings → Secrets and variables → Actions）

**财报系统（11 个）**
- `FINNHUB_API_KEY` — Finnhub 财经数据
- `OPENAI_API_KEY` — OpenAI(生成中文分析 / 翻译，两套共用)
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

## 六、常见维护操作

| 想做什么 | 怎么做 |
|---|---|
| **加/删财报股票** | 改 `earnings_monitor.py` 里 `CATEGORIES` 对应板块的 `tickers` 列表 |
| **改频道路由** | 改 `CATEGORIES` 里该板块的 `env`，并在 GitHub 配对应 Secret |
| **改电话会白名单** | 默认自动派生；想自定义 → 在 `earnings.yml` 的 env 加 `TRANSCRIPT_TICKERS: "MU,NVDA,AVGO"` |
| **改财报推送时间** | 改 `earnings.yml` 的 `cron`（UTC 时间） |
| **改 AI 模型** | env 加 `OPENAI_MODEL: "gpt-4o"`（更深度）或保持 mini（更省） |
| **加 X 监控账号** | 改 `monitor.py` 的 `ACCOUNTS`，加一项并配一个新 webhook Secret |
| **改 X 轮询频率** | 改 X 工作流的 `cron`（注意私有仓库分钟数；公开仓库无限） |
| **临时测试财报** | `earnings.yml` 临时加 `REPORTED_LOOKBACK: "30"` + 清空 `earnings_state.json` 为 `{}`，跑完删掉 |

---

## 七、成本

- **财报**：Finnhub 免费、Motley Fool 免费、GitHub Actions 免费(公开仓库)；只有 OpenAI 按量，每财报季约 **几毛~几十块人民币**（mini 极便宜）。
- **X 监控**：X API **按量付费**（$0.005/条读），用 `since_id` 后只为真正的新推付费；翻译费极小。高频账号(如 financialjuice)是成本大头，已停用。

---

## 八、故障排查

| 现象 | 可能原因 / 处理 |
|---|---|
| Actions 报 `KeyError: 'XXX_API_KEY'` | 对应 Secret 没配或名字打错（区分大小写） |
| 工作流成功但 Discord 没消息 | 1) 窗口内无新财报(正常静默) 2) 该公司已去重 3) webhook 指向的频道和你看的不一致 |
| 报告把公司名写错 | 已修(`fetch_company_name` 取真实名)；确认用的是最新 `earnings_monitor.py` |
| Discord 消息被截断 | 已修(自动分段)；确认最新脚本 |
| 电话会一直没出 | Motley Fool 暂未发布(会重试)，或该票不被覆盖(韩股/ETF/冷门小盘) |
| `Node.js 20 deprecated` 警告 | 无害，可忽略 |

---

## 九、未来待办（想做时回来继续）

1. **市场解读**：抓财报后的新闻/分析师观点，单独开频道。
2. **X 大V 解读**：抓指定大V 对该财报的评论（用现有 X API）。
3. **图片卡片**：把报告渲染成信息图（HTML→PNG 发 Discord）。
4. **准实时 X**：上 Oracle 免费 VM 跑常驻轮询版，延迟降到 1–2 分钟。

---

*本文档为系统存档备份。代码与配置以 GitHub 仓库 `hello` 为准。*
