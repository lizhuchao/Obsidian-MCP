from __future__ import annotations

from collections import Counter
from pathlib import Path
import re


SCHEMAS_DIRNAME = "Schemas"
INDEXES_DIRNAME = "Indexes"
CONTEXT_PACKS_DIRNAME = "Context_Packs"

SCHEMAS_REL = SCHEMAS_DIRNAME
INDEXES_REL = INDEXES_DIRNAME
CONTEXT_PACKS_REL = CONTEXT_PACKS_DIRNAME

COMMON_REQUIRED_FRONTMATTER = [
    "type",
    "status",
    "created_at",
    "updated_at",
    "tags",
    "confidence",
    "owner",
]

COMMON_RECOMMENDED_FRONTMATTER = [
    "version",
    "promoted_at",
    "previous_version",
    "source",
    "related",
]

SCHEMA_SPECS = {
    "troubleshooting": {
        "title": "Troubleshooting Schema",
        "default_directory": "Knowledge/Troubleshooting",
        "purpose": "把已经定位过根因和验证过结论的排障经验沉淀成可复用知识卡。",
        "applicable_scenarios": [
            "服务异常排查",
            "部署问题复盘",
            "MCP/容器/挂载链路故障定位",
        ],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "问题",
            "环境",
            "症状",
            "根因",
            "已验证解决方案",
            "验证方式",
            "排除项",
            "适用条件",
            "不适用条件",
            "下次优先检查",
        ],
        "recommended_sections": ["相关知识", "相关工具", "后续观察点"],
        "promotion_rules": [
            "必须写清楚根因和已验证解决方案后才能晋升到 Knowledge。",
            "排除项必须保留证据，不能只写主观猜测。",
        ],
        "update_rules": [
            "新结论覆盖旧结论时使用 update_knowledge_note。",
            "持续观察类记录按时间追加时使用 append_knowledge_note。",
        ],
        "archive_rules": [
            "失效方案保留历史版本，不物理删除。",
            "确认不再适用时标记 archived 或 superseded。",
        ],
        "search_keywords": ["故障", "排障", "根因", "验证", "排除项"],
        "example_skeleton": """---
type: troubleshooting
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [mcp, troubleshooting]
confidence: verified
owner: user
---

# 示例：MCP 连接失败排查

## 问题

## 环境

## 症状

## 根因

## 已验证解决方案

## 验证方式

## 排除项

| 假设 | 证据 | 结论 |
|---|---|---|
|  |  |  |

## 适用条件

## 不适用条件

## 下次优先检查
""",
    },
    "workflow": {
        "title": "Workflow Schema",
        "default_directory": "Knowledge/Workflows",
        "purpose": "记录可重复执行的工作流、口令和操作规约，让人和 LLM 都能按同一流程执行。",
        "applicable_scenarios": [
            "知识整理流程",
            "Codex 执行 SOP",
            "手机端只读访问流程",
        ],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "目标",
            "触发条件",
            "输入",
            "步骤",
            "输出",
            "验收标准",
            "异常处理",
            "相关工具",
        ],
        "recommended_sections": ["注意事项", "示例口令", "相关知识"],
        "promotion_rules": [
            "流程在至少一个真实场景中验证过后再晋升为 verified。",
            "用户可复述执行条件和输出结果时再进入默认检索面。",
        ],
        "update_rules": [
            "步骤被替换时用 update_knowledge_note 保留旧版本。",
            "补充新例子或边界条件时可 append_knowledge_note。",
        ],
        "archive_rules": [
            "被更优流程替代时保留 superseded 版本。",
            "不再使用的流程移入 Archive/Deleted。",
        ],
        "search_keywords": ["workflow", "SOP", "步骤", "验收", "异常处理"],
        "example_skeleton": """---
type: workflow
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [workflow]
confidence: verified
owner: user
---

# 示例：总结并归档到 Obsidian

## 目标

## 触发条件

## 输入

## 步骤

## 输出

## 验收标准

## 异常处理

## 相关工具
""",
    },
    "decision": {
        "title": "Decision Schema",
        "default_directory": "Knowledge/Decisions",
        "purpose": "沉淀架构和流程决策，保留为什么选它而不是别的方案。",
        "applicable_scenarios": ["架构选型", "存储策略", "同步与移动端策略"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "背景",
            "目标",
            "候选方案",
            "权衡标准",
            "最终选择",
            "不选其他方案的原因",
            "风险",
            "后续复盘点",
        ],
        "recommended_sections": ["触发事件", "受影响系统", "相关知识"],
        "promotion_rules": [
            "需要明确最终选择和放弃其他方案的原因。",
            "只有可回溯到业务目标的决策才晋升。",
        ],
        "update_rules": [
            "决策反转时保留旧版本并说明触发条件。",
            "新增风险或复盘结论可以追加。",
        ],
        "archive_rules": [
            "过时决策不删除，标记 superseded。",
            "归档后仍保留检索能力用于解释历史状态。",
        ],
        "search_keywords": ["decision", "权衡", "方案", "风险", "复盘"],
        "example_skeleton": """---
type: decision
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [decision]
confidence: verified
owner: user
---

# 示例：NAS 作为唯一主库

## 背景

## 目标

## 候选方案

## 权衡标准

## 最终选择

## 不选其他方案的原因

## 风险

## 后续复盘点
""",
    },
    "concept": {
        "title": "Concept Schema",
        "default_directory": "Knowledge/Concepts",
        "purpose": "把概念解释成可被长期复用的知识卡，而不是一次性聊天答案。",
        "applicable_scenarios": ["术语解释", "机制说明", "模型边界说明"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "一句话解释",
            "适用场景",
            "核心机制",
            "常见误区",
            "例子",
            "相关概念",
        ],
        "recommended_sections": ["反例", "进一步阅读"],
        "promotion_rules": [
            "定义必须稳定且能脱离聊天上下文独立阅读。",
            "避免把具体决策或经验误归类成 concept。",
        ],
        "update_rules": [
            "核心定义变化时走 update_knowledge_note。",
            "新增例子或误区时可追加。",
        ],
        "archive_rules": [
            "概念过时后标记 deprecated 或 archived。",
            "保留旧解释以便理解历史知识卡。",
        ],
        "search_keywords": ["概念", "机制", "误区", "例子"],
        "example_skeleton": """---
type: concept
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [concept]
confidence: verified
owner: user
---

# 示例：LLM Knowledge Base

## 一句话解释

## 适用场景

## 核心机制

## 常见误区

## 例子

## 相关概念
""",
    },
    "prompt": {
        "title": "Prompt Schema",
        "default_directory": "Knowledge/Prompts",
        "purpose": "记录稳定可复用的提示词及其使用边界。",
        "applicable_scenarios": ["系统提示词", "标准操作口令", "模版化任务输入"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "目标",
            "适用模型",
            "输入约束",
            "提示词正文",
            "输出预期",
            "失败模式",
            "示例",
        ],
        "recommended_sections": ["变量说明", "版本变更"],
        "promotion_rules": [
            "必须说明适用模型和输出预期。",
            "没有复用价值的临时提示词不晋升。",
        ],
        "update_rules": [
            "提示词改版时保留版本历史。",
            "新增变量说明或案例可追加。",
        ],
        "archive_rules": [
            "被替换的提示词标记 superseded。",
            "保留旧版本用于回归比较。",
        ],
        "search_keywords": ["prompt", "提示词", "输入约束", "输出预期"],
        "example_skeleton": """---
type: prompt
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [prompt]
confidence: verified
owner: user
---

# 示例：知识库帮助提示词

## 目标

## 适用模型

## 输入约束

## 提示词正文

## 输出预期

## 失败模式

## 示例
""",
    },
    "code-pattern": {
        "title": "Code Pattern Schema",
        "default_directory": "Knowledge/Code_Patterns",
        "purpose": "记录已经验证过的实现套路、边界条件和适用前提。",
        "applicable_scenarios": ["代码模板", "常见接口模式", "安全写法"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER,
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "问题场景",
            "推荐模式",
            "实现要点",
            "反模式",
            "示例代码",
            "适用边界",
        ],
        "recommended_sections": ["测试要点", "相关知识"],
        "promotion_rules": [
            "必须至少有一个经过验证的示例代码片段。",
            "只记录稳定模式，不记录临时 patch。",
        ],
        "update_rules": [
            "模式变化时保留旧实现说明。",
            "补充测试要点和边界条件可以追加。",
        ],
        "archive_rules": [
            "失效写法保留为反模式证据。",
            "废弃模式标记 deprecated。",
        ],
        "search_keywords": ["pattern", "代码模式", "示例代码", "反模式"],
        "example_skeleton": """---
type: code-pattern
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [code-pattern]
confidence: verified
owner: user
---

# 示例：MCP 工具安全路径校验

## 问题场景

## 推荐模式

## 实现要点

## 反模式

## 示例代码

## 适用边界
""",
    },
    "research-note": {
        "title": "Research Note Schema",
        "default_directory": "Knowledge/References",
        "purpose": "整理外部资料、观点和待验证信息，决定是否晋升为正式 Knowledge。",
        "applicable_scenarios": ["网页摘要", "视频/公众号摘录", "外部资料整理"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER + ["source"],
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "来源",
            "摘要",
            "关键观点",
            "可采纳结论",
            "不确定点",
            "需要验证的内容",
            "是否进入 Knowledge",
        ],
        "recommended_sections": ["相关知识", "引用片段"],
        "promotion_rules": [
            "必须说明来源和是否已经验证。",
            "未分离观点与事实的资料不能直接晋升为 verified。",
        ],
        "update_rules": [
            "资料结论变化时保留旧版本。",
            "新增验证结果时可追加。",
        ],
        "archive_rules": [
            "无效来源也保留，以便解释历史判断。",
            "过时研究笔记可归档但不物理删除。",
        ],
        "search_keywords": ["research", "来源", "摘要", "验证"],
        "example_skeleton": """---
type: research-note
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
source: https://example.com
tags: [research]
confidence: inferred
owner: user
---

# 示例：Obsidian 与 MCP 资料整理

## 来源

## 摘要

## 关键观点

## 可采纳结论

## 不确定点

## 需要验证的内容

## 是否进入 Knowledge
""",
    },
    "source": {
        "title": "Source Schema",
        "default_directory": "Sources/Web",
        "purpose": "规范外部来源采集笔记，保留原始材料和附件。",
        "applicable_scenarios": ["网页抓取", "截图整理", "外部原文保存"],
        "required_frontmatter": COMMON_REQUIRED_FRONTMATTER + ["source"],
        "recommended_frontmatter": COMMON_RECOMMENDED_FRONTMATTER,
        "required_sections": [
            "来源",
            "采集时间",
            "原始内容摘要",
            "关键片段",
            "备注",
        ],
        "recommended_sections": ["是否转入 Knowledge", "附件"],
        "promotion_rules": [
            "Source 本身是原始材料，不要求直接晋升为 Knowledge。",
            "若要晋升，需要额外写 research-note 或 workflow/concept 卡片。",
        ],
        "update_rules": [
            "补充附件或补录来源时可追加。",
            "不要把分析结论直接回写成 source 的唯一内容。",
        ],
        "archive_rules": [
            "无论内容是否采用，原始来源都应可追溯。",
            "仅在明确失去参考价值时归档。",
        ],
        "search_keywords": ["source", "来源", "采集", "附件"],
        "example_skeleton": """---
type: source
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
source: https://example.com
tags: [source]
confidence: unverified
owner: user
---

# 示例：外部网页采集

## 来源

## 采集时间

## 原始内容摘要

## 关键片段

## 备注
""",
    },
}

SCHEMA_ALIASES = {
    "troubleshooting": "troubleshooting",
    "workflow": "workflow",
    "decision": "decision",
    "concept": "concept",
    "prompt": "prompt",
    "code-pattern": "code-pattern",
    "research-note": "research-note",
    "source": "source",
    "reference": "research-note",
}

CONTEXT_PACK_SPECS = {
    "obsidian-kb-operating-context": {
        "title": "Obsidian KB Operating Context",
        "body": """## 用途

当用户要求知识库操作时，LLM 应优先读取这份上下文。

## 触发口令

- 知识库帮助
- 总结，归档
- 确认
- 归档旧草稿
- 更新这篇
- 回滚
- 查历史知识

## 执行规则

- 草稿写入 Inbox
- 用户确认后才晋升 Knowledge
- Knowledge 更新必须归档旧版
- 不物理删除
- Archive 可回滚
- 时间戳使用 Asia/Shanghai
- 手机端只读或轻量浏览
- 默认搜索 Knowledge verified 内容
""",
    },
    "codex-operating-context": {
        "title": "Codex Operating Context",
        "body": """## 用途

给 Codex 执行工程任务前加载。

## 关键上下文

- 当前项目路径：`/Users/lizhuchao/WorkSpace/Obsidian-MCP`
- Docker Compose 结构依赖 `compose.yaml` 与 `obsidian-mcp-app` 构建上下文约束
- MCP 服务版本以 `app.py` 的 `APP_VERSION` 为准
- Cloudflare Tunnel 通过环境变量注入 token，禁止泄露
- Obsidian vault 源路径：`smb://DiskStation._smb._tcp.local/Obsidian`
- 本机 SMB 挂载路径：`/Volumes/Obsidian`
- 修改 `app.py` 后必须重建并刷新 ChatGPT Connector
- 健康检查入口：`GET /health`
""",
    },
    "troubleshooting-context": {
        "title": "Troubleshooting Context",
        "body": """## 用途

处理排障类任务前优先加载。

## 排障顺序

1. 先定位链路层级
2. 先看健康检查
3. 再看容器日志
4. 再看 volume mount
5. 再看 MCP tool response
6. 所有排除项必须保留到知识卡
""",
    },
    "writing-context": {
        "title": "Writing Context",
        "body": """## 用途

为整理知识卡、任务书、流程说明时提供统一写作规范。

## 规则

- 先写结论，再写证据
- Frontmatter 保留结构化元数据
- 正文标题尽量与 schema 对齐
- 避免把一次性聊天废话写入长期知识
""",
    },
    "mobile-reading-context": {
        "title": "Mobile Reading Context",
        "body": """## 用途

手机 Obsidian / WebDAV / Remotely Save 相关任务加载。

## 规则

- 手机不是主写入端
- NAS 是主库
- WebDAV 同步本地副本
- Remotely Save 配置路径规则必须一致
- Assets 必须同步才能显示图片
""",
    },
}

INDEX_TITLES = {
    "README": "Indexes README",
    "knowledge-map": "Knowledge Map",
    "active-systems": "Active Systems",
    "workflows": "Workflows Index",
    "decision-log": "Decision Log",
    "stale-notes": "Stale Notes",
    "mobile-access": "Mobile Access",
}

WORKFLOW_COMMANDS = [
    "知识库帮助",
    "总结，归档",
    "确认",
    "归档旧草稿",
    "查一下我之前有没有类似记录",
    "更新这篇，保留旧版本",
    "把这篇知识卡归档",
    "回滚这篇知识卡",
    "手机怎么看知识库",
]

DEFAULT_DECISIONS = [
    "NAS 作为主库",
    "手机只读，不作为主写入端",
    "ChatGPT Memory 只保留交互偏好",
    "长期知识进入 Obsidian",
    "不物理删除",
    "Knowledge 更新必须 Archive 旧版",
    "时间戳统一 Asia/Shanghai",
]

IMPORTANT_ENTRY_PATHS = [
    "Knowledge/Workflows/AI 与 Obsidian 知识库使用场景.md",
    "Knowledge/References/ChatGPT 与 Codex 通过 MCP 读写 Synology NAS 上 Obsidian Vault 的技术搭建.md",
]


def schema_relative_path(schema_name: str) -> str:
    return f"{SCHEMAS_REL}/{schema_name}.schema.md"


def context_pack_relative_path(name: str) -> str:
    return f"{CONTEXT_PACKS_REL}/{name}.md"


def index_relative_path(name: str) -> str:
    filename = "README.md" if name == "README" else f"{name}.md"
    return f"{INDEXES_REL}/{filename}"


def generated_block_start(name: str) -> str:
    return f"<!-- AUTO-GENERATED:{name}:START -->"


def generated_block_end(name: str) -> str:
    return f"<!-- AUTO-GENERATED:{name}:END -->"


def replace_generated_block(text: str, name: str, content: str) -> str:
    start = generated_block_start(name)
    end = generated_block_end(name)
    replacement = f"{start}\n{content.rstrip()}\n{end}"

    if start in text and end in text:
        pattern = re.compile(
            rf"{re.escape(start)}.*?{re.escape(end)}",
            re.DOTALL,
        )
        return pattern.sub(replacement, text)

    suffix = "" if text.endswith("\n") else "\n"
    return text + suffix + "\n" + replacement + "\n"


def _join_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_schema_markdown(schema_name: str) -> str:
    spec = SCHEMA_SPECS[schema_name]
    required_fm = "\n".join(f"- `{item}`" for item in spec["required_frontmatter"])
    recommended_fm = "\n".join(f"- `{item}`" for item in spec["recommended_frontmatter"])
    required_sections = "\n".join(f"- {item}" for item in spec["required_sections"])
    recommended_sections = "\n".join(f"- {item}" for item in spec["recommended_sections"])
    scenarios = _join_bullets(spec["applicable_scenarios"])
    promotion = _join_bullets(spec["promotion_rules"])
    updates = _join_bullets(spec["update_rules"])
    archives = _join_bullets(spec["archive_rules"])
    keywords = ", ".join(spec["search_keywords"])
    return f"""---
type: schema
status: verified
schema_name: {schema_name}
default_directory: {spec["default_directory"]}
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# {spec["title"]}

## 用途

{spec["purpose"]}

## 适用场景

{scenarios}

## 默认目录

`{spec["default_directory"]}`

## frontmatter 必填字段

{required_fm}

## frontmatter 推荐字段

{recommended_fm}

## 正文必填章节

{required_sections}

## 正文推荐章节

{recommended_sections}

## 晋升规则

{promotion}

## 更新规则

{updates}

## 归档规则

{archives}

## 检索关键词

`{keywords}`

## 示例骨架

```md
{spec["example_skeleton"].rstrip()}
```
"""


def render_context_pack_markdown(name: str) -> str:
    spec = CONTEXT_PACK_SPECS[name]
    return f"""---
type: context-pack
status: verified
context_pack_name: {name}
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# {spec["title"]}

{spec["body"].rstrip()}
"""


def render_index_markdown(name: str, note_records: list[dict] | None = None, stale_days: int = 180) -> str:
    records = note_records or []

    if name == "README":
        return """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Indexes README

## 用途

给 LLM 和人快速定位知识库结构，避免每次全文搜索整个 vault。

## 主要入口

- [[Indexes/knowledge-map]]
- [[Indexes/active-systems]]
- [[Indexes/workflows]]
- [[Indexes/decision-log]]
- [[Indexes/stale-notes]]
- [[Indexes/mobile-access]]
"""

    if name == "active-systems":
        return """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Active Systems

## 当前实际运行系统

| 系统 | 状态 | 入口 | 关键路径 | 依赖 | 维护方式 | 故障检查点 |
|---|---|---|---|---|---|---|
| Obsidian KB MCP | active | `/mcp` | ChatGPT/Codex -> MCP -> Vault | Docker, Cloudflare, Vault | 修改代码后重建服务 | `/health`、tool response |
| Synology NAS | active | `smb://DiskStation._smb._tcp.local/Obsidian` | NAS -> SMB/WebDAV -> 客户端 | NAS 存储、网络 | NAS 管理台与挂载检查 | 挂载、权限、空间 |
| Cloudflare Tunnel | active | 公开 HTTPS Host | Tunnel -> MCP | Cloudflare token | 环境变量注入 | Tunnel 连接状态 |
| WebDAV mobile reading | active | 手机 Obsidian / Remotely Save | NAS -> WebDAV -> 手机副本 | WebDAV、同步配置 | 手机端定期同步 | 路径规则、图片同步 |
| Mac mini Obsidian | active | `/Volumes/Obsidian` | SMB 挂载 -> 本地浏览/编辑 | SMB 挂载 | 本机挂载与 Obsidian 客户端 | `/Volumes/Obsidian` 是否可访问 |
| Mobile Obsidian | active | 手机本地仓 | Remotely Save 副本 | WebDAV、Assets 同步 | 移动端只读/轻写 | 同步冲突、附件缺失 |
"""

    if name == "mobile-access":
        return """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Mobile Access

## 手机访问规则

- 手机端默认作为只读或轻量浏览端
- NAS 是唯一主库
- WebDAV 同步本地副本，不直接绕开主库写入
- Remotely Save 路径规则必须与主库保持一致
- `Assets/` 必须同步，否则图片和 PDF 无法显示
"""

    if name == "knowledge-map":
        generated = render_knowledge_map_block(records)
        return replace_generated_block(
            """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Knowledge Map

## 用途

帮助 LLM 和人快速知道 Knowledge 里有哪些系统、分类和入口笔记。
""",
            "knowledge-map",
            generated,
        )

    if name == "workflows":
        generated = render_workflows_block(records)
        return replace_generated_block(
            """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Workflows Index

## 常用口令

""" + _join_bullets(WORKFLOW_COMMANDS) + "\n",
            "workflows",
            generated,
        )

    if name == "decision-log":
        generated = render_decision_log_block(records)
        return replace_generated_block(
            """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Decision Log

## 核心决策

""" + _join_bullets(DEFAULT_DECISIONS) + "\n",
            "decision-log",
            generated,
        )

    if name == "stale-notes":
        generated = render_stale_notes_block(records, stale_days)
        return replace_generated_block(
            """---
type: index
status: verified
updated_at: 2026-05-17T00:00:00+08:00
owner: user
---

# Stale Notes

## 用途

追踪长期未更新但仍被标记为 verified 的知识卡。
""",
            "stale-notes",
            generated,
        )

    raise KeyError(f"Unsupported index: {name}")


def note_link(relative_path: str, title: str | None = None) -> str:
    label = f"|{title}" if title else ""
    return f"[[{relative_path}{label}]]"


def render_knowledge_map_block(note_records: list[dict]) -> str:
    category_counts = Counter()
    entries = []
    workflow_titles = []

    for record in note_records:
        relative_path = record["relative_path"]
        parts = Path(relative_path).parts
        if len(parts) >= 3 and parts[0] == "Knowledge":
            category_counts[parts[1]] += 1
        if relative_path.startswith("Knowledge/Workflows/"):
            workflow_titles.append(note_link(relative_path, record["title"]))

    lines = ["## 核心系统", "", "- Obsidian KB MCP", "- Synology NAS", "- Cloudflare Tunnel", "- WebDAV mobile reading"]
    lines += ["", "## 当前有效工作流", ""]
    lines += workflow_titles or ["- 暂无 Workflow 笔记"]
    lines += ["", "## 主要 Knowledge 分类", ""]
    if category_counts:
        for category, count in sorted(category_counts.items()):
            lines.append(f"- {category}: {count}")
    else:
        lines.append("- 暂无 Knowledge 分类统计")

    lines += ["", "## 重要入口笔记", ""]
    for path in IMPORTANT_ENTRY_PATHS:
        match = next((record for record in note_records if record["relative_path"] == path), None)
        if match:
            entries.append(f"- {note_link(path, match['title'])}")
        else:
            entries.append(f"- {note_link(path, Path(path).stem)}")
    lines += entries
    lines += ["", "## 当前活跃约束", ""]
    lines += [
        "- 默认搜索 Knowledge verified 内容",
        "- 不物理删除任何知识卡",
        "- Knowledge 更新前必须归档旧版本",
        "- 时间戳统一使用 Asia/Shanghai",
    ]
    return "\n".join(lines)


def render_workflows_block(note_records: list[dict]) -> str:
    workflow_records = [
        record
        for record in note_records
        if record["relative_path"].startswith("Knowledge/Workflows/")
    ]
    lines = ["## 相关 Workflow 笔记", ""]
    if workflow_records:
        for record in sorted(workflow_records, key=lambda item: item["title"]):
            lines.append(f"- {note_link(record['relative_path'], record['title'])}")
    else:
        lines.append("- 暂无已验证 workflow 笔记")
    return "\n".join(lines)


def render_decision_log_block(note_records: list[dict]) -> str:
    decision_records = [
        record
        for record in note_records
        if record["relative_path"].startswith("Knowledge/Decisions/")
        or record.get("type") == "decision"
    ]
    lines = ["## 自动汇总决策笔记", ""]
    if decision_records:
        for record in sorted(decision_records, key=lambda item: item["title"]):
            lines.append(f"- {note_link(record['relative_path'], record['title'])}")
    else:
        lines.append("- 暂无独立的 Decision 笔记")
    return "\n".join(lines)


def render_stale_notes_block(note_records: list[dict], stale_days: int) -> str:
    stale_records = [
        record for record in note_records
        if record.get("is_stale")
    ]
    lines = [f"## 超过 {stale_days} 天未更新的 verified 笔记", ""]
    if stale_records:
        for record in sorted(stale_records, key=lambda item: item["relative_path"]):
            lines.append(f"- {note_link(record['relative_path'], record['title'])}")
    else:
        lines.append("- 当前没有 stale verified 笔记")
    return "\n".join(lines)


def bootstrap_llm_knowledge_base_files(
    vault_root: Path,
    write_text_file,
    overwrite: bool = False,
    note_records: list[dict] | None = None,
) -> dict:
    created = []
    updated = []

    for dirname in [SCHEMAS_DIRNAME, INDEXES_DIRNAME, CONTEXT_PACKS_DIRNAME]:
        (vault_root / dirname).mkdir(parents=True, exist_ok=True)

    for schema_name in SCHEMA_SPECS:
        relative_path = schema_relative_path(schema_name)
        target = vault_root / relative_path
        if overwrite or not target.exists():
            write_text_file(target, render_schema_markdown(schema_name))
            (updated if target.exists() and overwrite else created).append(relative_path)

    for name in CONTEXT_PACK_SPECS:
        relative_path = context_pack_relative_path(name)
        target = vault_root / relative_path
        if overwrite or not target.exists():
            write_text_file(target, render_context_pack_markdown(name))
            (updated if target.exists() and overwrite else created).append(relative_path)

    for name in INDEX_TITLES:
        relative_path = index_relative_path(name)
        target = vault_root / relative_path
        if overwrite or not target.exists():
            write_text_file(target, render_index_markdown(name, note_records=note_records or []))
            (updated if target.exists() and overwrite else created).append(relative_path)

    return {
        "created_or_updated": created + updated,
        "created_count": len(created),
        "updated_count": len(updated),
    }
