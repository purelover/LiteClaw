# Memory 管理对比：LiteClaw vs Manus vs Anthropic vs 开源项目

## 一、当前 LiteClaw 实现（已落实快速改进）

| 维度 | 实现 |
|------|------|
| **存储结构** | MEMORY.md（长期）+ memory/YYYY-MM-DD.md（每日）+ TODO.md + NOTES.md |
| **加载策略** | MEMORY 前 200 行 + 最近 2 天 daily；超 200 行提示 memory_get 读取 |
| **Recitation** | TODO + NOTES 放 system 末尾，引导注意力（对齐 Manus） |
| **memory_flush** | 接近 compaction 前触发，静默一轮让模型调用 memory_append 写记忆 |
| **工具** | memory_get、memory_append、memory_search（关键词匹配） |
| **截断** | preserved 完整保留，memory/summary/recitation 按 2:1:1 截断；截断时附带可 memory_get 的路径 |
| **日期** | 仅注入 `YYYY-MM-DD`，不包含时分（利于 KV-cache） |

---

## 二、Manus 最佳实践

### 1. KV-Cache 优化
- **稳定前缀**：单 token 差异即可使后续 cache 失效
- **常见错误**：在 system prompt 开头放时间戳（精确到秒）会严重破坏 cache
- **LiteClaw 现状**：日期在 preserved 末尾，但**每次请求都会变**（含分钟），可能影响 cache 命中

### 2. 文件系统即 Context
- 大内容可丢弃，**保留 URL/路径**，需要时再读
- 压缩策略应**可恢复**：删内容不删引用
- **LiteClaw 现状**：memory 截断时直接丢内容，只留「[... 记忆已截断]」，无法按需拉回

### 3. Recitation
- todo.md 放 context 末尾操纵注意力 ✅ LiteClaw 已实现

### 4. 保留错误
- 不隐藏失败，让模型看到错误并自我修正 ✅ compaction 摘要已要求保留失败/错误信息

### 5. 小上下文优于长上下文
- 「pivot」：完成阶段性任务后，只带必要信息开新会话
- **LiteClaw 现状**：单会话内 compaction，不 pivot

### 6. 工具输出摘要
- 长 tool 结果应先摘要再喂回，保留文件名/路径等标识
- **LiteClaw 现状**：TOOL_RESULT_MAX_CHARS 截断，无摘要

---

## 三、Anthropic 最佳实践

### 1. Claude Code Memory
- **CLAUDE.md**：用户写的指令，< 200 行 adherence 更好
- **Auto memory**：Claude 自己写，MEMORY.md 前 200 行加载，**topic 文件按需读取**
- **索引模式**：MEMORY.md 作为索引，详细内容放 topic 文件，启动时不加载

### 2. Memory Tool（Agent API）
- **Just-in-time 检索**：不预加载全部，模型按需 read/write
- 存储到 `/memories` 目录，支持 view/list/create/update/delete
- 模型在任务开始前自动检查 memory 目录

### 3. 指令有效性
- 具体 > 模糊（「用 2 空格缩进」优于「格式要好」）
- 结构清晰、无冲突

---

## 四、改进建议（按优先级）

### P1：日期注入对 KV-Cache 的影响 ✅ 已落实
**问题**：`当前系统日期时间：2026年03月08日 14:30` 每分钟变，破坏 cache。  
**已做**：改为仅 `当前系统日期：YYYY-MM-DD`。

### P2：MEMORY 索引 + 按需加载 ✅ 部分落实
**问题**：MEMORY.md 和 daily 全量加载，长时易占满 context。  
**已做**：load_memory_long 只读前 200 行，超则提示 `memory_get('MEMORY.md')` 读取完整。  
**待做**：拆成 MEMORY.md（索引）+ memory/topics/*.md（详情）的 topic 模式。

### P3：截断可恢复 ✅ 已落实
**问题**：memory 截断后内容丢失，模型无法再读。  
**已做**：截断时追加 `可 memory_get 读取: MEMORY.md, memory/{今日}.md, memory/{昨日}.md`。

### P4：memory_search 增强
**问题**：当前仅关键词匹配，召回有限。  
**建议**：后续可接向量检索（如 embedding + 相似度），提升相关记忆召回。

### P5：工具输出摘要
**问题**：超长 tool 结果直接截断，可能丢关键信息。  
**建议**：对超 N 字符的结果，可尝试让模型先写简短摘要再喂回；或保留含路径/关键标识的行（已有部分实现）。

### P6：Pivot 机制（可选）
**问题**：单会话无限拉长，Manus 建议阶段性 pivot。  
**建议**：任务明显完成时（如用户说「好了」「谢谢」），可触发「pivot」：写摘要到 memory，下一轮用更短历史开局。

---

## 五、头部开源项目对比

### Mem0（49k+ stars）
- **分层记忆**：Conversation（单轮）→ Session（分钟级）→ User（长期）→ Org（全局）
- **核心流程**：Retrieve（多层检索，user 优先）→ Promote（持久化到 session/user）→ Capture（消息入 conversation）
- **可借鉴**：user_id/session_id 隔离；语义检索 + 相关性排序；矛盾消解
- **LiteClaw 映射**：MEMORY.md≈User，memory/YYYY-MM-DD≈Session，当前对话≈Conversation

### MemGPT（Letta）
- **虚拟内存**：OS 式分页，多级存储扩展 context
- **自动决策**：何时 push 到向量库、何时 pull 回
- **可借鉴**：分层存储 + 按需检索；/memory、/save、/memorywarning 等交互
- **LiteClaw 映射**：compaction 可视为「换页」，memory_flush 为「写回」

### LangMem（LangChain）
- **记忆类型**：Semantic（事实/偏好）、Episodic（经历/摘要）、Procedural（行为模式）
- **工具**：create_manage_memory_tool、create_search_memory_tool
- **后台**：Background memory manager 自动提取、合并、更新
- **可借鉴**：语义 + 情节 + 程序 三类记忆；agent 主动管理 vs 后台自动提取

### OpenAI Memory / Compaction
- **Compaction**：超阈值自动压缩，保留必要状态
- **Session 协议**：retrieve/add/remove/clear
- **可借鉴**：compact_threshold 可配置；压缩后保留最新 compaction 项即可

### Memori（12k+ stars）
- **SQL 原生**：与 LLM 和框架解耦
- **实体与流程**：基于 entity 和 process 的归因
- **可借鉴**：BYODB 灵活存储；自动持久化与召回

---

## 六、可进一步借鉴的点

| 来源 | 建议 |
|------|------|
| **Mem0** | 引入 user_id/session_id 区分记忆作用域；memory_search 接向量检索 |
| **MemGPT** | 模型可主动 `/memory` 查看当前记忆；memory_warning 触发 flush |
| **LangMem** | 区分语义/情节/程序记忆；可增加后台自动提取 |
| **OpenAI** | compaction 配置项更细（threshold、保留策略） |
| **Memori** | 支持 PostgreSQL 等外部存储，便于多实例共享 |
