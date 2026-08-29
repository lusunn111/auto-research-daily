# 自动科研日报架构与运行共识

## 已完成

- 新项目目录为 `auto-research-daily`，自身由 Git 管理；两个上游浅克隆位于被忽略的 `upstream/`。
- 融合审阅版本：`zotero-arxiv-daily@f3f73ce053f75ace2b15e38299890af7d530e214`，`vla-wam-daily@b6ae8dcfb059fd61bc3d2987b25507b4b8979237`。
- 运行时不依赖上游包。采用统一严格数据模型、arXiv OAI-PMH 增量抓取、Zotero 私有兴趣语料、可解释排序、最大边际相关性重排、按需 arXiv HTML 全文、兼容 OpenAI 接口的结构化分析、原子缓存与按月归档。
- 输出三层：今日必读、值得浏览、探索发现。发布数量是上限，不为满足数量降低相关性阈值。
- 正式解读包含 Setting、Motivation、Insight、Challenge、Analyze、Method、Experiments、Limitations、第一单位、通讯作者、上传年月、证据和不确定性。摘要或正文没有的信息不得推测。
- 缓存只保存论文分析与来源追踪，不保存每日排序结果。缓存键包含论文版本、内容、证据范围、研究画像、模型、提示词和模式版本。
- DeepSeek 已按 2026 年 8 月官方接口更新：摘要级批量解读使用 `deepseek-v4-flash`，全文级深读使用 `deepseek-v4-pro`，基础地址为 `https://api.deepseek.com`，并记录输入与输出令牌。
- GitHub Actions 包含离线持续集成、北京时间每日 12:30 调度、生成路径白名单提交、运行摘要制品和同次 GitHub Pages 部署。

## 已验证

- Python 3.13 环境中 Ruff 静态检查通过。
- 9 项离线测试通过，覆盖 OAI 解析、去重、确定性排序、缓存失效、证据原文校验、分层全文截取、缓存复用、原子持久化和不写入的试运行。
- 固定数据端到端试运行成功，不访问 arXiv、Zotero 或真实大模型。
- 真实 arXiv OAI-PMH 只读冒烟测试成功抓取 50 篇元数据；该测试未调用大模型、未读取全文、未写入数据。
- 真实 DeepSeek API 冒烟测试已通过：V4 Pro 全文路径单次输入 783、输出 2704 令牌；V4 Flash 摘要路径单次输入 651、输出 926 令牌。两次均通过严格数据与证据校验，且未落盘。

## 尚未激活

- 当前没有配置 GitHub 远程仓库，因此 Actions 尚未在线运行。
- 正式运行需要 `LLM_API_KEY`；Zotero 个性化还需要 `ZOTERO_USER_ID` 和只读 `ZOTERO_API_KEY`。
- 尚未用真实大模型和当天 arXiv 数据做质量标定。默认阈值与数量预算属于第一版基线，应根据一至两周反馈调整。
