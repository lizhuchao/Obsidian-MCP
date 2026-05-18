import importlib
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


def install_dependency_stubs():
    requests_module = types.ModuleType("requests")
    requests_module.get = None
    sys.modules["requests"] = requests_module

    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    transport_module = types.ModuleType("mcp.server.transport_security")

    class FastMCP:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.session_manager = types.SimpleNamespace(run=lambda: _AsyncNullContext())

        def tool(self):
            def decorator(func):
                return func
            return decorator

        def streamable_http_app(self):
            return object()

    class TransportSecuritySettings:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    fastmcp_module.FastMCP = FastMCP
    transport_module.TransportSecuritySettings = TransportSecuritySettings

    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = mcp_server_module
    sys.modules["mcp.server.fastmcp"] = fastmcp_module
    sys.modules["mcp.server.transport_security"] = transport_module

    starlette_module = types.ModuleType("starlette")
    applications_module = types.ModuleType("starlette.applications")
    middleware_module = types.ModuleType("starlette.middleware")
    cors_module = types.ModuleType("starlette.middleware.cors")
    responses_module = types.ModuleType("starlette.responses")
    routing_module = types.ModuleType("starlette.routing")

    class Starlette:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class Middleware:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class CORSMiddleware:
        pass

    class JSONResponse(dict):
        def __init__(self, payload):
            super().__init__(payload)

    class Route:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class Mount:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    applications_module.Starlette = Starlette
    middleware_module.Middleware = Middleware
    cors_module.CORSMiddleware = CORSMiddleware
    responses_module.JSONResponse = JSONResponse
    routing_module.Mount = Mount
    routing_module.Route = Route

    sys.modules["starlette"] = starlette_module
    sys.modules["starlette.applications"] = applications_module
    sys.modules["starlette.middleware"] = middleware_module
    sys.modules["starlette.middleware.cors"] = cors_module
    sys.modules["starlette.responses"] = responses_module
    sys.modules["starlette.routing"] = routing_module


class _AsyncNullContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@contextmanager
def loaded_app():
    with tempfile.TemporaryDirectory() as tmpdir:
        previous_env = {
            "VAULT_ROOT": os.environ.get("VAULT_ROOT"),
            "PUBLIC_HOST": os.environ.get("PUBLIC_HOST"),
        }

        os.environ["VAULT_ROOT"] = tmpdir
        os.environ["PUBLIC_HOST"] = "localhost"
        install_dependency_stubs()

        if "app" in sys.modules:
            module = importlib.reload(sys.modules["app"])
        else:
            module = importlib.import_module("app")

        try:
            yield module, Path(tmpdir)
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class LlmKnowledgeBaseToolsTests(unittest.TestCase):
    def test_bootstrap_creates_phase_one_docs(self):
        with loaded_app() as (app, vault_root):
            result = app.bootstrap_llm_knowledge_base()

            self.assertTrue(result["ok"])
            self.assertTrue((vault_root / "Schemas").is_dir())
            self.assertTrue((vault_root / "Indexes").is_dir())
            self.assertTrue((vault_root / "Context_Packs").is_dir())
            self.assertTrue((vault_root / "Schemas" / "workflow.schema.md").exists())
            self.assertTrue((vault_root / "Indexes" / "knowledge-map.md").exists())
            self.assertTrue((vault_root / "Context_Packs" / "codex-operating-context.md").exists())

    def test_list_and_fetch_support_docs(self):
        with loaded_app() as (app, _vault_root):
            app.bootstrap_llm_knowledge_base()

            schemas = app.list_schemas()
            self.assertTrue(any(item["schema_name"] == "workflow" for item in schemas))

            workflow_schema = app.fetch_schema("workflow")
            self.assertIn("## 正文必填章节", workflow_schema["content"])
            self.assertIn("Workflow", workflow_schema["title"])

            context_packs = app.list_context_packs()
            self.assertTrue(any(item["context_pack_name"] == "codex-operating-context" for item in context_packs))

            codex_context = app.fetch_context_pack("codex-operating-context")
            self.assertIn("Docker Compose", codex_context["content"])

    def test_validate_note_against_schema_reports_missing_items_and_valid_note_passes(self):
        with loaded_app() as (app, vault_root):
            app.bootstrap_llm_knowledge_base()

            invalid = app.create_inbox_note(
                title="工作流草稿",
                content="# 工作流草稿\n\n只有摘要，没有结构。",
            )
            invalid_result = app.validate_note_against_schema(
                invalid["relative_path"],
                schema_name="workflow",
            )

            self.assertFalse(invalid_result["is_valid"])
            self.assertIn("type", invalid_result["missing_required_frontmatter"])
            self.assertIn("目标", invalid_result["missing_required_sections"])

            valid_note = """---
type: workflow
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [workflow]
confidence: verified
owner: user
---

# 工作流校验

## 目标

让 MCP 执行固定工作流。

## 触发条件

用户要求执行预定义工作流。

## 输入

任务书与当前 vault 状态。

## 步骤

1. 读取上下文。
2. 执行工具。

## 输出

更新后的知识或报告。

## 验收标准

结果可复查。

## 异常处理

失败时保留原因。

## 相关工具

- fetch_context_pack
"""
            valid_path = vault_root / "Knowledge" / "Workflows" / "workflow-valid.md"
            app.write_text_file(valid_path, valid_note)

            valid_result = app.validate_note_against_schema(
                "Knowledge/Workflows/workflow-valid.md",
                schema_name="workflow",
            )

            self.assertTrue(valid_result["is_valid"])
            self.assertEqual(valid_result["missing_required_frontmatter"], [])
            self.assertEqual(valid_result["missing_required_sections"], [])

    def test_generate_indexes_is_idempotent(self):
        with loaded_app() as (app, vault_root):
            app.bootstrap_llm_knowledge_base()

            reference = """---
type: reference
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [reference]
confidence: verified
owner: user
---

# ChatGPT 与 Codex 通过 MCP 读写 Synology NAS 上 Obsidian Vault 的技术搭建
"""
            workflow = """---
type: workflow
status: verified
created_at: 2026-05-17T13:00:00+08:00
updated_at: 2026-05-17T13:00:00+08:00
tags: [workflow]
confidence: verified
owner: user
---

# AI 与 Obsidian 知识库使用场景
"""
            app.write_text_file(
                vault_root / "Knowledge" / "References" / "ChatGPT 与 Codex 通过 MCP 读写 Synology NAS 上 Obsidian Vault 的技术搭建.md",
                reference,
            )
            app.write_text_file(
                vault_root / "Knowledge" / "Workflows" / "AI 与 Obsidian 知识库使用场景.md",
                workflow,
            )

            first = app.generate_indexes()
            first_content = (vault_root / "Indexes" / "knowledge-map.md").read_text(encoding="utf-8")
            second = app.generate_indexes()
            second_content = (vault_root / "Indexes" / "knowledge-map.md").read_text(encoding="utf-8")

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(first_content, second_content)
            self.assertIn("AI 与 Obsidian 知识库使用场景", first_content)
            self.assertIn("ChatGPT 与 Codex 通过 MCP 读写 Synology NAS 上 Obsidian Vault 的技术搭建", first_content)

    def test_audit_reports_issues_without_mutation(self):
        with loaded_app() as (app, vault_root):
            app.bootstrap_llm_knowledge_base()

            duplicate_one = """---
type: concept
status: verified
created_at: 2025-01-01T00:00:00+08:00
updated_at: 2025-01-01T00:00:00+08:00
tags: [concept]
confidence: verified
owner: user
---

# 重复标题

## 一句话解释

解释一。

## 适用场景

场景一。

## 核心机制

机制一。

## 常见误区

误区一。

## 例子

例子一。

## 相关概念

概念一。

![[Assets/ChatGPT/2026-05/missing.png]]
"""
            duplicate_two = duplicate_one.replace("解释一", "解释二")
            missing_metadata = """# 缺少元数据

## 一句话解释

没有 frontmatter。
"""

            first_path = vault_root / "Knowledge" / "Concepts" / "duplicate-one.md"
            second_path = vault_root / "Knowledge" / "Concepts" / "duplicate-two.md"
            missing_path = vault_root / "Knowledge" / "Concepts" / "missing-metadata.md"

            app.write_text_file(first_path, duplicate_one)
            app.write_text_file(second_path, duplicate_two)
            app.write_text_file(missing_path, missing_metadata)

            stale_timestamp = 1704067200
            os.utime(first_path, (stale_timestamp, stale_timestamp))

            original_missing = missing_path.read_text(encoding="utf-8")
            audit = app.audit_knowledge_base(stale_days=30, inbox_backlog_threshold=1)

            self.assertTrue(audit["ok"])
            self.assertGreaterEqual(audit["summary"]["duplicate_title_count"], 1)
            self.assertGreaterEqual(audit["summary"]["broken_asset_link_count"], 1)
            self.assertGreaterEqual(audit["summary"]["schema_violation_count"], 1)
            self.assertGreaterEqual(audit["summary"]["missing_metadata_count"], 1)
            self.assertEqual(original_missing, missing_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
