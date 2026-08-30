# Auto Research Daily

这是一个面向具身智能、世界模型、物理人工智能（Physical AI）、世界动作模型（World Action Model，WAM）和系统优化的个人化科研发现流水线。它融合了 `zotero-arxiv-daily` 的 Zotero 兴趣画像思路，以及 `vla-wam-daily` 的严格数据模型、缓存、质量门和每日发布机制，但运行时不依赖两个上游仓库。

## 为什么不是简单合并

两个上游解决的是不同问题。前者擅长从个人文献库推断兴趣，但全文抓取发生得过早；后者擅长稳定抓取、分析和发布，但主题固定且主要依赖摘要。这里重新定义了统一数据模型和成本边界：先广泛召回，再完成低成本排序，只对最高排名论文读取 arXiv HTML 全文，并按引言、方法、实验、结果和局限保留分层证据片段，最后输出三层报告。

| 层级 | 目标数量 | 证据范围 | 用途 |
|---|---:|---|---|
| 今日必读 | 最多 12 篇 | 优先全文 | 影响研究判断，需要精读 |
| 值得浏览 | 约 15 至 30 篇 | 标题与摘要 | 扩大覆盖面 |
| 探索发现 | 约 10% 至 15% | 标题与摘要 | 避免个性化过滤气泡 |

系统不会为了“每天很多”硬凑 40 篇。如果当天只有少量论文达到相关性阈值，就只发布这些论文。数量是上限，质量门是下限。

## 运行链路

```text
arXiv OAI-PMH + Zotero 私有文献库
→ 统一论文模型与版本去重
→ 主题规则、个人语料、时效性和探索性联合打分
→ 最大边际相关性重排，抑制内容重复
→ 最高排名论文按需获取 arXiv HTML
→ 兼容 OpenAI 接口的严格 JSON 解读
→ 证据范围、模型、提示词和内容哈希共同缓存
→ 每日 Markdown、JSON、RSS 和 GitHub Pages 静态站点
```

每篇正式解读包含 Setting（研究设定）、Motivation（研究动机）、Insight（核心洞察）、Challenge（技术挑战）、Analyze（问题分析）、Method（方法）、Experiments（实验）、Limitations（局限）、第一单位、通讯作者、上传年月和证据摘录。材料没有提供的信息必须写“证据未提供”或“摘要未说明”。

## 本地运行

推荐 Python 3.13，也支持 Python 3.11 及以上版本。

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/auto-research-daily validate
```

先用完全离线的固定数据验证端到端流程：

```bash
.venv/bin/auto-research-daily daily \
  --offline-fixture tests/fixtures/offline_daily.json \
  --no-llm
```

正式运行：

```bash
export LLM_API_KEY='...'
export ZOTERO_USER_ID='...'
export ZOTERO_API_KEY='...'
.venv/bin/auto-research-daily daily --lookback-days 3 --max-papers 60 --deep-limit 10
```

`--no-llm` 只用于离线持续集成（Continuous Integration，代码提交后自动检查）和流程冒烟测试，它生成的内容不能视作正式科研解读。

## 配置与凭据

研究主题、排序预算和模型默认值在 `config/research.yaml` 中版本化。以下值通过环境变量或 GitHub Secrets 注入，不会写入公开数据：

| 名称 | 是否必需 | 含义 |
|---|---|---|
| `LLM_API_KEY` | 正式运行必需 | 大模型接口密钥 |
| `LLM_BASE_URL` | 可选 | 兼容 OpenAI 接口的服务地址 |
| `LLM_BRIEF_MODEL` | 可选 | 摘要级批量解读模型，默认 `deepseek-v4-flash` |
| `LLM_DEEP_MODEL` | 可选 | 全文级解读模型，默认仍使用低成本的 `deepseek-v4-flash` |
| `ZOTERO_USER_ID` | 可选 | Zotero 用户标识 |
| `ZOTERO_API_KEY` | 可选 | Zotero 只读接口密钥 |
| `ARXIV_USER_AGENT` | 推荐 | 包含项目和联系方式的 arXiv 请求标识 |
| `SITE_URL` | 推荐 | 静态站点地址，用于 RSS |

Zotero 原始条目只在内存中参与排序，不会进入报告、缓存或 Git 历史。

## 输出

- `data/latest.json`：本次严格结构化结果。
- `data/archive/YYYY-MM.json`：按月合并的论文归档。
- `data/cache/analyses.json`：公开论文分析缓存。
- `reports/YYYY-MM-DD.md`：适合深读的中文日报。
- `site/index.html`、`site/archive/` 与 `site/feed.xml`：最新日报、历史页面和 RSS，可由 GitHub Pages 直接发布。

缓存键包含论文标识与版本、输入内容哈希、证据范围、研究画像指纹、模型、提示词版本和模式版本。任何一项发生变化都会自动重新分析；缓存只保存论文解读，当前排名和分数会在每天重新计算。

## 测试

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest --cov=auto_research_daily --cov-report=term-missing
```

持续集成完全使用固定夹具，不访问 arXiv、Zotero 或真实大模型。每日工作流在北京时间 12:30 抓取真实数据，逐篇论文统一使用低成本的 V4 Flash 非思考模式；未来的跨论文综合最多使用 V4 Flash 低强度思考，不启用 V4 Pro。执行测试后仅提交允许的生成目录，并在同一次运行中部署 GitHub Pages。运行报告会记录真实输入和输出令牌数。

## 上游来源

本地 `upstream/` 中保存了两个浅克隆供审计，目录被 Git 忽略。`UPSTREAMS.lock.json` 记录融合时审阅的确切提交，运行时不会导入或执行上游代码。
