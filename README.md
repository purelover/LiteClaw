# LiteClaw

Python 精简实现：飞书 IM + 豆包 LLM。参考 OpenClaw 架构设计，核心区别在于本地+云端协同：优先用本地大模型处理任务，结果不理想时自动 fallback 到云端；云端可配置多模型链，依次 fallback。作者实测可降低云端 token 消耗约 **95%**。

> **用 [Cursor](https://cursor.com/) vibe 编程实现**

项目根目录为 `LiteClaw`，主程序在 `simple/` 下。本文档以 **Ubuntu** 为运行环境。

## 架构（参考 OpenClaw）

- **Gateway 控制平面**：Session 管理、消息路由、Lane 队列调度
- **Session**：per-channel-peer 隔离，维护对话历史与状态
- **Lane 队列**：同 sessionKey 串行，不同 sessionKey 可并行
- **Agent**：LLM 推理 + 工具调用（exec、file、browser、system、automation、memory、search、im、插件）
- **存储**：SQLite 对话历史 + 工作区 Markdown 记忆（AGENTS/SOUL/USER/MEMORY.md）

## 功能

- **飞书**：接收单聊消息，默认长连接（WebSocket），可选 Webhook；经 Gateway 路由后由 Agent 处理
- **豆包**：火山引擎 ARK API（OpenAI 兼容），支持 Function Calling
- **本地+云端协同**：hybrid_loop 启用时，默认先用本地模型；若本地执行结果不理想，自动 fallback 到云端
- **工具执行**：exec、file、browser、system、automation、memory、search、im（send_image/send_file）；代码通过 exec_python/exec_bash 工具调用执行，支持从文本解析 tool_call JSON

## 环境

- Python 3.10+
- 飞书企业自建应用（App ID、App Secret）
- 豆包/火山引擎 API Key 和接入点 ID

## 环境依赖（Ubuntu 按需安装）

以下为 Ubuntu 下的安装与启动方法，根据配置按需安装。

### Python

- **用途**：运行主程序
- **安装**：
  ```bash
  sudo apt update
  sudo apt install python3 python3-pip python3-venv
  ```
- **验证**：`python3 --version`（需 3.10+）

### Ollama（本地模型，`hybrid_loop.enabled` 时需安装）

- **用途**：本地大模型，hybrid 模式下承担主推理流程，或用于简单任务
- **安装**：`curl -fsSL https://ollama.com/install.sh | sh`
- **拉取模型**：`ollama pull llama3.2` 或 `ollama pull qwen2.5:0.5b`
- **验证**：`ollama list` 或访问 http://localhost:11434

**示例：8G 显存 PC 运行 qwen3.5:4b**

在 8G 显存的 PC 上，可通过量化 KV cache 将 qwen3.5:4b 全部载入显存，实现流畅推理：

1. 设置环境变量：`export OLLAMA_KV_CACHE_TYPE=q8_0`
2. 创建 Modelfile（如 `Modelfile`）：
   ```
   FROM qwen3.5:4b
   PARAMETER num_ctx 32768
   ```
3. 创建模型：`ollama create qwen3.5-4b-32k -f Modelfile`
4. 在 `config.yaml` 的 `local.model` 中填写 `qwen3.5-4b-32k`

效果：显存占用约 6.7GB，输出约 30+ tokens/s，可流畅使用。

### Bash（代码执行，`exec.enabled: true` 时需安装）

- **用途**：执行 LLM 生成的 Bash 脚本（Python 脚本使用当前 Python 解释器）
- **说明**：Ubuntu 默认已安装 bash，一般无需额外安装
- **验证**：`bash --version`

### ngrok（本地调试飞书 Webhook 时可选）

- **用途**：将本地端口暴露为公网 URL，供飞书事件订阅回调
- **安装**：
  ```bash
  sudo snap install ngrok
  # 或从 https://ngrok.com/download 下载对应架构的二进制
  ```
- **启动**：`ngrok http 9000`，将生成的 `https://xxx.ngrok.io` 填到飞书事件订阅 URL

## 安装

从项目根目录进入 `simple/` 并安装 Python 依赖（建议使用虚拟环境）：

```bash
cd simple
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

在 `simple/` 目录下：

```bash
cd simple
cp config.example.yaml config.yaml
# 编辑 config.yaml 填写
```

| 配置项 | 说明 |
|--------|------|
| `gateway.max_concurrent_lanes` | 最大并发 lane 数 |
| `storage.db_path` | SQLite 数据库路径（相对 simple/） |
| `exec.enabled` | 是否启用 exec 工具（exec_python/exec_bash） |
| `exec.timeout_sec` | 代码执行超时（秒） |
| `tools.file` | 文件工具（read/write/edit/apply_patch），需设 workspace |
| `tools.browser` | 无头浏览器（需 playwright） |
| `tools.system` | 进程列表、系统命令 |
| `tools.automation` | cron_list、gateway_status |
| `tools.memory` | 工作区记忆（memory_get/search/append、history_search） |
| `tools.im` | 飞书发送图片/文件（send_image、send_file），飞书模式下默认启用 |
| `tools.search` | Serper 网络搜索（serper_search），需 api_key 或 SERPER_API_KEY |
| `tools.plugins` | 插件目录列表 |
| `skills.load` | Skill 目录列表（AgentSkills 格式，兼容 OpenClaw），路径相对 simple/ |
| `skills.mode` | `full`=全量注入 body，`metadata_only`=仅元数据，body 通过 skill_read 按需拉取 |
| `skills.only` | 白名单，仅过滤 openclaw 等后续目录；空表示全部加白 |
| `skills.entries` | 按 name 启用/禁用，如 `example: { enabled: false }` |
| `skills.check_requires` | 是否检查 bins/env，缺则跳过 |
| `doubao` | 豆包 API Key、endpoint_id |
| `cloud_chain` | 云端模型链，按优先级 |
| `hybrid_loop.enabled` | 是否启用本地+云端协同（需 Ollama） |
| `feishu.mode` | `ws`=长连接（默认，无需公网），`webhook`=HTTP 回调 |
| `feishu.ws_log_level` | ws 模式日志级别，`info` 可过滤 ping/pong，`debug` 输出全部 |
| `feishu` | app_id、app_secret；webhook 模式需 verification_token、encrypt_key |

## Skills（可选）

LiteClaw 支持 [AgentSkills](https://agentskills.io/) 格式，可复用 OpenClaw 的 skills。每个 skill 是含 `SKILL.md` 的目录：

```yaml
skills:
  load: ["skills"]           # 加载 simple/skills/ 下子目录
  only: []                  # 白名单，仅过滤 openclaw 等后续目录；第一个 load 目录始终全量加载
  entries: {}               # 按 name 启用/禁用
  check_requires: true      # 检查 bins/env，缺则跳过
```

`SKILL.md` 含 YAML frontmatter（name、description）和 Markdown 正文。正文会注入到 system prompt，指导模型何时、如何使用相关能力。

### 安装 OpenClaw Skills

LiteClaw 无 `clawhub` CLI，需手动将 skill 目录放入 load 路径：

**方式一：克隆社区 skills 仓库**

```bash
cd simple
# openclaw-master-skills：127+ 精选 skills，含 skills/ 子目录
git clone --depth 1 https://github.com/LeoYeAI/openclaw-master-skills.git skills-openclaw
```

在 `simple/config.yaml` 中增加 load 路径：

```yaml
skills:
  load: ["skills", "skills-openclaw/skills"]   # skills-openclaw/skills 下每个子目录为一个 skill
```

**方式二：从 ClawHub 或社区获取**

- 浏览 [clawhub.ai](https://clawhub.ai/) 或 [awesome-openclaw-skills-cn](https://github.com/AgentWorkers/awesome-openclaw-skills-cn)
- 找到目标 skill 的 `SKILL.md` 或仓库
- 在 `simple/skills/` 下新建子目录（如 `my-skill/`），放入 `SKILL.md`

**格式要求**：每个 skill 目录需含 `SKILL.md`，至少包含：

```yaml
---
name: skill-name
description: 简要描述
---
```

若 skill 声明 `metadata.openclaw.requires`（如 `bins: ["gh"]`），需在环境中安装对应命令，否则 `check_requires: true` 时会跳过。

## 飞书配置步骤

1. 打开 [飞书开放平台](https://open.feishu.cn/)
2. 创建企业自建应用，获取 App ID、App Secret
3. 开通权限：`im:message:send_as_bot`、`im:message:receive_v1`（接收消息）；群聊需额外开通「获取用户在群组中@机器人的消息」
4. 事件订阅：
   - **长连接模式**（`feishu.mode: ws`，默认）：选择「使用长连接接收事件」，添加 `im.message.receive_v1`，无需公网 URL
   - **Webhook 模式**（`feishu.mode: webhook`）：请求 URL 填 `https://你的域名/webhook/event`，本地调试可用 ngrok

## 豆包配置步骤

1. 登录 [火山引擎控制台](https://console.volcengine.com/ark)
2. 开通豆包模型服务，创建 API Key
3. 创建推理接入点，获取 `ep-xxx` 格式的 endpoint_id

## 运行

在 `simple/` 目录下：

```bash
cd simple
source .venv/bin/activate
python3 main.py
```

默认监听 `0.0.0.0:9000`，飞书事件会推送到 `/webhook/event`。

**注意**：长连接模式无需公网 URL；Webhook 模式需公网可访问的 URL，本地调试可用 [ngrok](https://ngrok.com/)。

## 目录结构

```
LiteClaw/
├── README.md
├── LICENSE
└── simple/              # 主程序目录
    ├── main.py          # 入口
    ├── config.example.yaml
    ├── requirements.txt
    ├── data/            # SQLite 存储（自动创建）
    ├── gateway/         # 控制平面
    │   ├── gateway.py   # Gateway 核心
    │   ├── session.py   # Session 管理
    │   └── queue.py    # Lane 队列
    ├── agent/           # Agent 执行平面
    │   └── agent.py    # LLM + 工具调用
    ├── storage/         # 存储层
    │   ├── db.py       # SQLite 持久化
    │   └── workspace.py # 工作区记忆（Markdown）
    ├── tools/           # 工具（插件式扩展）
    │   ├── registry.py
    │   ├── exec_tool.py
    │   ├── file_tool.py
    │   ├── browser_tool.py
    │   ├── system_tool.py
    │   ├── automation_tool.py
    │   └── memory_tool.py
    ├── data/workspace/  # 工作区（tools.memory 启用时）
    │   ├── AGENTS.md
    │   ├── SOUL.md
    │   ├── USER.md
    │   ├── MEMORY.md
    │   └── memory/      # 每日日志 memory/YYYY-MM-DD.md
    ├── plugins/
    ├── skills/         # AgentSkills 格式（兼容 OpenClaw）
    │   ├── loader.py
    │   └── example/
    ├── llm/
    ├── im/
    │   └── feishu.py
    └── exec/
```
