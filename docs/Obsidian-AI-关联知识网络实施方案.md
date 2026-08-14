---
type: decision
status: draft
created_at: 2026-08-04T00:00:00+08:00
updated_at: 2026-08-04T00:00:00+08:00
tags: [obsidian, mcp, ai, knowledge-graph, roadmap]
confidence: proposed
owner: user
---

# Obsidian AI 关联知识网络实施方案

## 目标

把现有的 Obsidian MCP 从“AI 可读写 Markdown 的知识库”升级为“AI 持续维护、可追溯、可检索的关联知识网络”。

关联不是让 AI 每次读完整个 Vault，而是将 AI 已判断过的高价值关系保存为 Obsidian 原生双链和结构化元数据；以后读取一篇笔记时，AI 可以按需展开其关联上下文。

## 成功标准

- 新增或更新的正式知识卡，能自动获得少量高置信度关联。
- Obsidian 的反向链接、局部图谱和全局图谱可直接使用，不依赖专用客户端。
- 日常处理只针对变更笔记和少量候选，不把全库正文送给模型。
- 所有自动修改可预览、可审计、可回滚；低置信度关系默认不直接写入。
- AI 再次处理笔记时，能读取“当前笔记 + 一跳关系 + 必要摘要”，而不是盲目全文搜索。

## 设计原则

1. Obsidian 原生优先：正文采用 `[[相对路径|显示名]]` 双链，任何 Obsidian 客户端均可读。
2. 关系显式化：对需要机器理解的关系，在 frontmatter 中保存结构化字段；正文保留面向人的解释。
3. 增量优先：新建、更新、晋升时处理关系；定期任务仅处理变更集、待审核项和抽样质量检查。
4. 检索后判断：程序先用索引缩小候选，AI 只对当前笔记与少数候选进行语义判断。
5. 可控自动化：高置信度自动应用；其余写入待审核队列，避免图谱噪声。
6. 生命周期一致：一切正式写入沿用现有版本归档和回滚机制。

## 知识关联数据模型

每篇正式 Knowledge 笔记建议包含：

```yaml
---
type: workflow
status: verified
tags: [obsidian, mcp]
summary: 一句话说明这张知识卡解决什么问题。
keywords: [双链, 增量索引, 候选召回]
relations:
  - target: Knowledge/Concepts/Obsidian 双链
    type: explains
    confidence: high
    rationale: 本文将双链作为关联的持久化表示。
  - target: Knowledge/Decisions/AI 关联治理策略
    type: implements
    confidence: medium
    rationale: 本文落地该治理策略。
relation_checked_at: 2026-08-04T00:00:00+08:00
---
```

正文另设固定章节：

```md
## 相关笔记

- [[Knowledge/Concepts/Obsidian 双链|Obsidian 双链]] — 概念依据
- [[Knowledge/Decisions/AI 关联治理策略|AI 关联治理策略]] — 架构决策
```

建议先限制关系类型为：`explains`（解释）、`implements`（实现）、`depends_on`（依赖）、`derived_from`（来源/推导）、`contradicts`（冲突）、`supersedes`（替代）、`related_to`（一般相关）。关系必须有方向、理由和置信度。

## 总体执行流程

```text
笔记新建 / 更新 / 晋升
        ↓
提取轻量索引（标题、摘要、标签、关键词、已有链接、更新时间、内容哈希）
        ↓
候选召回（路径/标签/关键词/全文或向量索引）→ Top 5–20
        ↓
AI 读取：当前全文 + 候选摘要；必要时读取候选全文
        ↓
关系判定（类型、方向、理由、置信度）
        ↓
高置信度：预览或自动写入双链 + frontmatter
中低置信度：写入待审核队列
        ↓
更新关系索引、审计日志和笔记版本
```

## MCP 能力升级

### Phase 1：关系的安全读写

新增以下工具，所有写入均通过现有 Knowledge 版本归档机制：

| 工具 | 作用 | 默认行为 |
| --- | --- | --- |
| `extract_note_metadata` | 提取标题、摘要、标签、双链、frontmatter、内容哈希 | 只读 |
| `list_note_relations` | 返回一跳出链、入链和关系元数据 | 只读 |
| `suggest_note_relations` | 为指定笔记召回候选并给出关系建议 | 只读，不改笔记 |
| `apply_note_relations` | 写入已批准关系，更新正文 `相关笔记` 与 frontmatter | 默认 dry-run，需显式 apply |
| `remove_note_relation` | 删除一条明确关系 | 默认 dry-run，需显式 apply |
| `find_broken_note_links` | 检查 `[[...]]` 指向的 Markdown 笔记是否存在 | 只读 |
| `find_unlinked_mentions` | 找到正文中提到标题但尚未建立链接的位置 | 只读、限量返回 |

写入规则：目标采用完整 Vault 相对路径；去重；禁止自链接；每篇最多自动新增 3 条关系；保留用户手写的 `相关笔记` 条目；更新前创建版本快照。

### Phase 2：轻量关系索引与候选召回

新增 `Indexes/note-catalog.jsonl`（或 SQLite）作为可再生索引，不作为事实源。每条记录仅包含：

```json
{"path":"Knowledge/…/note.md","title":"…","summary":"…","tags":["…"],"keywords":["…"],"links":["Knowledge/…"],"modified_at":"…","content_hash":"…","indexed_at":"…"}
```

新增工具：

| 工具 | 作用 |
| --- | --- |
| `reindex_notes` | 仅重建缺失或内容哈希变化的笔记索引 |
| `search_relation_candidates` | 先按标签、关键词、路径和全文评分筛出候选 |
| `get_related_context` | 返回当前笔记、一跳关系及受 token 预算约束的摘要 |
| `relation_index_status` | 返回索引覆盖率、过期数量、最近运行时间 |

第一版不必引入向量数据库。Vault 达到数千篇、关键词召回明显不足后，再将 embedding 加入候选召回层；双链与 Markdown 仍然是最终事实源。

### Phase 3：审核、治理与可观测性

- `propose_relation_review_batch`：将中低置信度建议写进 `00_Inbox/Relation_Reviews/`，不修改正式笔记。
- `apply_relation_review_batch`：只应用用户批准的建议。
- `audit_relation_graph`：报告断链、孤儿笔记、异常高出度节点、重复关系、过期关系和未解析冲突。
- `repair_moved_note_links`：笔记移动或改名时以 dry-run 预览并批量更新引用。当前直接文件操作不会获得 Obsidian App 的自动改链能力，因此这是必需工具。
- `relation_change_log`：在 `Indexes/` 中记录每次自动/人工关系变更的时间、来源、理由和版本路径。

## 何时运行

### 实时增量：主流程

触发于 `promote_inbox_note`、`update_knowledge_note`、`append_knowledge_note` 后。处理单篇变化笔记，只读取它和 Top 5–20 候选摘要；高置信度建议可由用户确认后写入。

建议初期始终先 `suggest_note_relations`，由用户或调用 AI 明确批准 `apply_note_relations`；积累评估数据后，才允许对 `confidence=high` 自动应用。

### 定期增量：日/周任务

每天或每周执行一次，不扫描所有正文：

1. 根据内容哈希找出自上次索引后新增或变更的笔记。
2. 仅为这些笔记重建索引并生成候选关系。
3. 检查这些笔记及其一跳邻居的断链、重复关系和过期关系。
4. 将待确认项写入 Review Inbox，输出简要治理报告。

### 低频全库健康检查：月度或按规模分批

全库扫描仅用于结构性体检，且应分批执行。它优先处理路径、双链、frontmatter 和索引，而不是将全库全文发送给 AI。异常项再进入候选队列，由 AI 分批检查。

## Context 与成本控制

- 建立关联时：当前笔记全文 + 每个候选 200–500 字摘要/命中片段；只有不确定时再读候选全文。
- 使用关联时：`get_related_context` 默认一跳、最多 5 篇关联笔记、总字符/Token 上限可配置。
- 图谱遍历按预算停止：优先 `depends_on`、`implements`、`derived_from` 等强关系，而非无限展开 `related_to`。
- 大任务分批：例如每批 20–50 个变更笔记，保留 checkpoint，可随时恢复。
- AI 输出必须是结构化关系 JSON；服务端验证路径、关系类型、数量、重复和置信度，再生成 Markdown。

## 质量与安全护栏

- 建链阈值：只有清晰、可解释、非重复的关系可进入正式笔记。
- 反噪声限制：每次自动新增上限 3 条；一般相关关系优先进入审核队列。
- 用户优先：不得覆盖用户编写的关系说明；自动生成区块须有明确边界标记。
- 可逆：所有关系写入沿用 Knowledge 版本归档；批量操作支持 dry-run、变更清单和回滚。
- 防幻觉：目标路径必须存在；理由需引用源笔记中的实际信息；冲突关系不得自动应用。
- 隐私：若引入外部 embedding API，必须先确认 Vault 内容可发送到该服务；否则使用本地模型或仅关键词检索。

## 分阶段落地计划

### 阶段 0：基线与规范（1–2 天）

- 初始化现有 `Schemas/`、`Indexes/`、`Context_Packs/`。
- 选定关系字段和 7 种受控关系类型。
- 为 10–20 篇代表性笔记手工建立关系，作为验收样本。
- 明确自动应用阈值、每篇关系上限、审核规则与隐私边界。

验收：样本笔记能在 Obsidian 中显示正向链接和反向链接，且关系含理由。

### 阶段 1：可控关系工具（3–5 天）

- 实现 Phase 1 MCP 工具与 Markdown/frontmatter 的幂等写入。
- 实现笔记链接解析、断链检测、保护手写内容、版本归档和 dry-run。
- 补充单元测试：路径安全、重复关系、自链接、回滚、用户内容保护。

验收：对样本笔记可预览、批准、写入、撤销关系，Obsidian 图谱正确更新。

### 阶段 2：增量索引与实时建议（4–7 天）

- 实现内容哈希和可再生轻量索引。
- 为晋升/更新流程增加“更新索引 → 候选召回 → 建议”的可选后置动作。
- 实现受预算控制的关联上下文读取。

验收：单篇更新不扫描全库正文；对 1000 篇量级的模拟目录，候选生成与上下文读取保持在可接受时间和 token 预算内。

### 阶段 3：治理任务与审核队列（3–5 天）

- 实现待审核关系 Inbox、批量应用、关系审计与变更日志。
- 添加计划任务入口（cron/容器 scheduler），支持 checkpoint 和批量大小配置。
- 生成每周治理报告：处理数量、自动应用、待审核、断链、孤儿笔记、失败项。

验收：周期任务只处理变化集；所有批量写入可追踪、可恢复。

### 阶段 4：评估后引入语义检索（可选，5–10 天）

仅当关键词/标签候选召回不足时，引入 embedding。先做离线评估：以阶段 0 标注样本衡量候选召回率、人工接受率、错误关联率和平均成本，再决定是否上线。

## 指标

- 关系覆盖率：有至少一条有效关系的正式笔记占比。
- 人工接受率：建议关系中被批准的比例。
- 噪声率：被撤销或判定不相关的自动关系比例。
- 断链率与孤儿笔记数。
- 增量任务处理量、耗时、每篇 token/成本。
- 上下文命中率：AI 读取关联上下文后是否减少重复检索/重复回答。

## 首次执行清单

1. 将本方案晋升到 `Knowledge/Plans/`。
2. 创建一篇决策卡：[[Knowledge/Decisions/AI 关联治理策略|AI 关联治理策略]]，确认关系类型、自动阈值和外部模型隐私策略。
3. 选取 10–20 篇现有 Knowledge 笔记建立金标关联样本。
4. 先实现阶段 1，不引入向量数据库。
5. 以样本和真实增量数据验证后，再开启阶段 2 的自动建议。

## 相关笔记

- [[Indexes/knowledge-map|Knowledge Map]] — 知识库总入口
- [[Indexes/workflows|Workflows]] — 现有工作流索引
- [[Indexes/decision-log|Decision Log]] — 决策记录入口

