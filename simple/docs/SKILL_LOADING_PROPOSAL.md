# Skills 加载与使用机制改进方案

## 一、对话内容核实

### 1.1 已核实的技术点

| 技术点 | 来源 | 核实结论 |
|--------|------|----------|
| **渐进式披露（Progressive Disclosure）** | AgentSkills 规范、Claude Skills 文档 | ✅ 真实存在。三层：Metadata(~100 tokens/skill) 常驻 → Instruction body 按需 → Resources 按需 |
| **元数据优先** | OpenClaw 文档、agentskills.io | ✅ SKILL.md 含 name+description，metadata 用于 load-time 过滤 |
| **load-time 过滤** | OpenClaw 文档 | ✅ requires.bins/env/config 在加载时过滤，不满足则跳过 |
| **路由表 / 意图识别** | 对话描述 | ⚠️ OpenClaw 的 model-router 等是**模型路由**（选哪个 LLM），非技能路由。技能选择未见独立 intent router 的公开实现 |
| **5000+ skills 场景** | 对话描述 | ⚠️ 社区规模大，但单实例通常不会加载全部；通过 only/entries 控制 |

### 1.2 核心结论

- **渐进式披露**是业界通用做法，可显著降低 token 消耗
- **OpenClaw** 主要依赖：load 过滤 + entries 开关 + only 白名单，未见显式的「意图→技能」路由层
- **LiteClaw 现状**：全量注入 name + description + body，skills 少时可行，多时会 prompt 膨胀

---

## 二、LiteClaw 当前实现

```
load_skills() → 全量加载 name + description + body
     ↓
build_skills_prompt() → 拼接成 system 片段
     ↓
Agent 启动时注入 preserved（不截断）
```

**问题**：每多一个 skill，body 可能数百～数千 tokens，skills 增多后 prompt 易爆炸。

---

## 三、改进方案（分阶段）

### Phase 1：元数据优先模式（低风险）

**目标**：默认只注入 name + description，body 通过工具按需拉取。

| 改动 | 说明 |
|------|------|
| `skills.mode` | 新增配置：`full`（当前行为）/ `metadata_only` |
| `build_skills_prompt` | `metadata_only` 时只输出 name+description，不输出 body |
| `skill_read` 工具 | 新增：`skill_read(skill_name)` 返回该 skill 的完整 body，供模型按需调用 |

**效果**：N 个 skills 从 `N × (100 + body_tokens)` 降为 `N × 100`，body 仅在模型认为需要时加载。

**配置示例**：
```yaml
skills:
  mode: metadata_only  # full | metadata_only，默认 full 保持兼容
  load: ["skills"]
  only: ["skill-creator"]
```

---

### Phase 2：技能路由表（可选，轻量）

**目标**：用简单规则做预筛选，减少无关 skills 进入 prompt。

| 改动 | 说明 |
|------|------|
| `skills.routing` | 可选配置：`{"关键词": ["skill-name"], ...}` |
| 请求时 | 用用户消息匹配关键词，优先/仅加载命中的 skills |

**示例**：
```yaml
skills:
  routing:
    "创建skill|写skill|skill-creator": ["skill-creator"]
    "示例|测试skill": ["example"]
  # 未命中时：回退到 only/load 的默认集合
```

**局限**：依赖人工维护关键词，适合 skills 较多、意图较清晰的场景。

---

### Phase 3：相关性评估（进阶）

**目标**：用轻量模型或 embedding 做「用户消息 vs skill description」的相似度，只加载 Top-K。

| 方案 | 实现复杂度 | 说明 |
|------|------------|------|
| A. Embedding 相似度 | 中 | 对 user_message 和 description 做 embedding，取 Top-3～5 |
| B. 轻量 LLM 分类 | 高 | 小模型做 1-shot 分类，输出 1～2 个 skill 名 |
| C. 关键词 + 描述匹配 | 低 | 用 BM25/关键词在 description 中匹配 |

**建议**：Phase 1 落地后再评估是否做 Phase 3；LiteClaw 当前 skills 数量有限，Phase 1 通常足够。

---

## 四、推荐实施顺序

1. **Phase 1**：实现 `metadata_only` + `skill_read` 工具
2. **验证**：在 skills 较多时对比 token 与效果
3. **Phase 2**：若需要，再增加 routing 配置
4. **Phase 3**：仅在确有 50+ skills 且 Phase 1 不足时考虑

---

## 五、Phase 1 实现要点

### 5.1 skill_read 工具

- **输入**：`skill_name: str`
- **输出**：该 skill 的完整 body（或「未找到」）
- **实现**：从已加载的 skills 列表中按 name 查找，返回 `body` 字段
- **权限**：仅能读已加载 skills，不暴露文件系统

### 5.2 build_skills_prompt 调整

```python
# metadata_only 时
for s in skills:
    parts.append(f"### {s['name']}\n{s['description']}\n")
    if mode != "metadata_only" and s.get("body"):
        parts.append(s["body"])
# 并追加说明：需要某 skill 的详细说明时，可调用 skill_read(skill_name)
```

### 5.3 AGENTS.md 补充

- 在「工具使用」中说明：`skill_read(skill_name)` 用于获取 skill 的完整说明，当需要执行某 skill 但不清楚细节时可调用。

---

## 六、参考资料

- [AgentSkills Progressive Disclosure](https://skills.deeptoai.com/zh/docs/development/progressive-disclosure-architecture)
- [OpenClaw Skills 文档](https://openclawlab.com/en/docs/tools/skills/)
- [Progressive Disclosure - Medium](https://medium.com/@martia_es/progressive-disclosure-the-technique-that-helps-control-context-and-tokens-in-ai-agents-8d6108e09289)
