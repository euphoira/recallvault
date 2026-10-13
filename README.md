# RecallVault

**让 Agent 记住用户偏好、识别偏好更新，并且真正停止召回已遗忘的内容。**

[![CI](https://github.com/euphoira/recallvault/actions/workflows/ci.yml/badge.svg)](https://github.com/euphoira/recallvault/actions/workflows/ci.yml)
[English](docs/README.en.md) · [API](docs/api-contract.md) · [架构](docs/architecture.md) · [安全边界](SECURITY.md)

RecallVault 是一个独立的 Agent Memory 开源工作台：从用户对话抽取带来源的记忆，跨会话检索，在偏好冲突时显式更新版本，并通过引用校验约束记忆问答。它展示的是一个可运行、可测试的本地记忆系统，不宣称具有人类记忆能力或商业生产环境能力。

![RecallVault 记忆工作台](docs/assets/dashboard.jpg)

## 能做什么

- **带来源的记忆写入**：fact / preference / episode 三种类型；Pydantic 校验结构；source turn ID + 用户原文子串校验；会话不可覆盖，避免引用被改写。
- **更新与隔离**：namespace + user 双重范围；稳定 key 表示同一记忆槽；SQLite `BEGIN IMMEDIATE` + expected revision 检测并发冲突；显式 supersession 保留版本历史；内容去重和请求幂等。
- **可解释的检索**：默认 BM25，支持英文词项及中文单字/双字切分；可选真实 `/embeddings`、余弦排序与 RRF 融合，按记忆版本缓存，校验模型标识、维度和有限非零向量。
- **可控生命周期**：TTL 到期过滤；遗忘清除同 key 的内容、来源、向量和相关会话正文，并擦除该 scope 的历史召回查询/引用；只留下墓碑元数据。演示种子每个 scope 仅初始化一次，遗忘后不会自动补回。
- **记忆 Agent**：有界 Function Calling 调用 `recall_memory` / `get_memory`；最终引用必须来自本次实际召回且仍有效的记忆版本；未知问题弃答。模型没有写入或遗忘工具。
- **独立 Web 工作台**：查看记忆、版本/有效期、来源、召回结果及执行记录；支持导入用户对话、确认偏好纠正、带引用提问和遗忘。

## 五分钟运行

需要 Python 3.11 或 3.12。无需数据库服务器、Node.js 或 API 密钥。

```bash
git clone https://github.com/euphoira/recallvault.git
cd recallvault
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\recallvault.exe serve
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/recallvault serve
```

打开 [http://127.0.0.1:8013](http://127.0.0.1:8013)。CLI 默认只绑定本机地址，单进程运行。

```bash
recallvault demo   # 初始化 demo/alice 一次并执行一次带来源问答
recallvault eval   # 独立临时数据库运行 10 个确定性回归场景
```

未激活虚拟环境时，使用对应的 `.venv/Scripts/recallvault.exe` 或 `.venv/bin/recallvault` 完整路径。`demo` 使用持久数据库，不会恢复被遗忘的数据。`eval` 始终在独立临时数据库中执行，不改变工作台数据。

## 体验路径

1. 在 `demo / alice` 初始化样例。用户偏好为 `I prefer concise answers.`。
2. 召回 `concise answers` 或向记忆助手提问，查看来源记忆 ID、版本及引用。
3. 导入新会话 `I prefer detailed answers.`，提交候选记忆；系统返回冲突并要求明确当前 revision 后再纠正，同 key 的旧版本随之失效。
4. 切到另一用户，原用户记忆不会出现。
5. 遗忘 `response.style`，旧版、新版和相关向量均退出召回；再次初始化样例也不会恢复。

离线抽取仅支持文档中列出的固定句式；任意复杂自然语言需要配置真实模型。演示问答使用词项检索及抽取式回答，不调用 LLM，也不伪造语义向量。

## 真实模型 / Embeddings

复制 `.env.example` 为 `.env`，设置 `RECALLVAULT_MODE=live`、`RECALLVAULT_API_KEY` 和 `RECALLVAULT_MODEL`。所选模型必须支持 **Chat Completions + function tools**；模型是否兼容由供应商决定，Responses-only 模型不可直接使用此适配器。

可独立开启 `RECALLVAULT_EMBEDDING_MODEL`，配置准确的返回模型标识和向量维度。开启后会把记忆正文及查询发送给该服务；默认关闭。语义模式上限为每 scope 100 条有效记忆，首次检索按需建立版本缓存，总运行时限 45 秒。超过预算会明确报错，可缩小数据集后重试；不会静默降级为假向量。

网络适配器设置 512 KiB 响应上限、单请求 15 秒超时、最多一次临时失败重试、整次操作 45 秒预算和最多 6 次工具调用。45 秒包含等待操作锁的时间；排队超时返回 504，写入、遗忘及种子初始化同样采用该排队预算。自动重定向关闭，远程服务必须 HTTPS。API key 只保留在后端环境中。

官方协议参考：[Function Calling](https://developers.openai.com/api/docs/guides/function-calling)、[Embeddings](https://developers.openai.com/api/docs/guides/embeddings)。没有 API key 的验证覆盖离线功能及 mock HTTP 协议，不代表已验证线上模型回答质量、费用或延迟。

## 验证

```bash
python -m pytest -q
python -m ruff check src tests
recallvault eval
python -m build
```

CI 在 Windows / Ubuntu × Python 3.11 / 3.12 执行测试、lint、10 个离线场景和 wheel 构建。测试关注并发纠正、范围隔离、TTL 临界点、遗忘后残留/重播、引用伪造、模型工具协议、向量格式和 HTTP 安全边界；不将离线固定样例通过率包装成真实模型准确率。

## 工程边界

- 精确来源与引用校验证明**出处和版本**，不能证明模型改写或用户陈述的语义正确性。候选记忆可在写入前审查。
- namespace/user 是数据过滤范围，**不是身份认证**。本项目为受信任单用户本机工作台；部署到网络前需加入身份授权、限流、加密与审计策略。
- 遗忘是应用层逻辑清除，不保证 SQLite WAL、备份、磁盘快照或远程供应商副本的取证级擦除。不要把它当作合规认证。
- 为防止遗忘后在途推理重新写入引用，单个服务进程使用操作锁；不支持多个 Uvicorn workers 或多个应用进程共享写入。
- 本机默认数据保存在 `.recallvault/memory.db`。每 scope 最多 1000 条 active 状态记忆、2000 个会话；召回日志保留最近 200 条。历史版本需按使用规模规划维护。

MIT License。欢迎通过 Issue / Pull Request 贡献可重现的边界场景。
