# 自动科研日报架构与运行共识

## 已完成

- 新项目目录为 `auto-research-daily`，自身由 Git 管理；两个上游浅克隆位于被忽略的 `upstream/`。
- 融合审阅版本：`zotero-arxiv-daily@f3f73ce053f75ace2b15e38299890af7d530e214`，`vla-wam-daily@b6ae8dcfb059fd61bc3d2987b25507b4b8979237`。
- 运行时不依赖上游包。采用统一严格数据模型、arXiv OAI-PMH 增量抓取、Zotero 私有兴趣语料、可解释排序、最大边际相关性重排、按需 arXiv HTML 全文、兼容 OpenAI 接口的结构化分析、原子缓存与按月归档。
- 输出三层：今日必读、值得浏览、探索发现。发布数量是上限，不为满足数量降低相关性阈值。
- 正式解读包含 Setting、Motivation、Insight、Challenge、Analyze、Method、Experiments、Limitations、第一单位、通讯作者、上传年月、证据和不确定性。摘要或正文没有的信息不得推测。
- 缓存只保存论文分析与来源追踪，不保存每日排序结果。缓存键包含论文版本、内容、证据范围、研究画像、模型、提示词和模式版本。
- DeepSeek 摘要级与全文级解读均使用低成本 `deepseek-v4-flash` 非思考模式，基础地址为 `https://api.deepseek.com`，并记录输入与输出令牌。
- GitHub Actions 已在线运行：北京时间每日 12:30 调度，生成路径白名单提交，同次 GitHub Pages 部署成功后才通过 QQ 邮箱发送 Gmail 通知，并单独记录通知状态。
- 最终入选论文会从带版本号的 arXiv HTML 提取 Figure 1 和 Figure 2。图片经过来源、重定向、格式、真实解码、体积和像素数校验后写入 `site/figures/`，状态与路径写入 `data/cache/figures.json`。图片只随当次 Pages 制品部署，不进入 Git 历史；历史页缺少本地图片时自动回退到 arXiv 原图。图片失败降级为纯文字，不影响论文发布或邮件。

## 已验证

- Python 3.13 环境中 Ruff 静态检查通过。
- 28 项离线测试通过，新增覆盖 Figure 1/2 结构解析、嵌套 Figure 归属、跨论文图片拒绝、编码路径穿越、图片真实解码、缓存失败降级、全局下载预算、原子缓存与网页地址生成。
- 固定数据端到端试运行成功，不访问 arXiv、Zotero 或真实大模型。
- 真实 arXiv OAI-PMH 只读冒烟测试成功抓取 50 篇元数据；该测试未调用大模型、未读取全文、未写入数据。
- 真实 DeepSeek API 冒烟测试已通过：V4 Pro 全文路径单次输入 783、输出 2704 令牌；V4 Flash 摘要路径单次输入 651、输出 926 令牌。两次均通过严格数据与证据校验，且未落盘。

## 当前运行基线

- GitHub 仓库与 Pages 已启用，生产流水线、DeepSeek、Zotero、QQ SMTP 和 Gmail 收件链路均已验证。
- 每日任务按北京时间 12:30 启动；网页通常在模型分析、发布和部署完成后更新，邮件在网页部署成功后发送。
- 图片首版只采用 arXiv HTML 的高置信度结构提取，不做源码包解压或 PDF 自动裁剪。这两个回退路径误判面更大，只有在统计出 HTML 覆盖率不足后再决定是否增加。
