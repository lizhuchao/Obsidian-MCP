# Synology Container Manager 部署说明

本项目的线上服务部署在 Synology NAS。发布新代码时，先通过已挂载的 SMB 共享更新 Docker 构建上下文，再在 Container Manager 中删除旧 Project 并用现有 Compose YAML 重新创建。

## 已确认的部署位置

本机 SMB 挂载目录：

```text
/Volumes/docker/chatgpt-obsidian-mcp
```

NAS 上对应目录：

```text
/volume1/docker/chatgpt-obsidian-mcp
```

目录结构必须保持如下形式：

```text
chatgpt-obsidian-mcp/
  compose.yaml
  .env                         # 不提交；包含 Tunnel token 等配置
  obsidian-mcp-app/
    app.py
    kb_seed.py
    requirements.txt
    Dockerfile
    scripts/
```

`compose.yaml` 会把 NAS Vault 挂载到容器内的 `/vault`；删除 Container Manager 的 Project 不会删除 Vault 内容。

## 1. 同步运行代码到 NAS

从本机仓库根目录，将发生变化的运行文件复制到 SMB 挂载的构建目录：

```bash
cp app.py /Volumes/docker/chatgpt-obsidian-mcp/obsidian-mcp-app/app.py
```

如对应文件也有变化，再一并同步：

```text
kb_seed.py
requirements.txt
Dockerfile
scripts/
```

不要同步：

- `.env`：NAS 上的 Tunnel token 和环境配置应保留。
- `tests/`、`docs/`、`.git/`：它们不是容器运行所需内容。
- `scripts/__pycache__/`：本机 Python 测试缓存。

同步后可校验：

```bash
cmp -s app.py /Volumes/docker/chatgpt-obsidian-mcp/obsidian-mcp-app/app.py && echo "app.py copied and verified"
```

## 2. 在 Container Manager 重建 Project

在 Synology DSM 中：

1. 打开 **Container Manager → 项目（Project）**。
2. 停止并删除原来的 Obsidian MCP Project。只删除 Project/容器，不要删除 Obsidian Vault 文件夹。
3. 点击 **新增（Create）**，选择“使用 Compose 文件 / YAML 创建”。
4. 选择现有文件：`/volume1/docker/chatgpt-obsidian-mcp/compose.yaml`。
5. Project 工作目录设为：`/volume1/docker/chatgpt-obsidian-mcp`，这样 `./obsidian-mcp-app` 能正确作为构建上下文。
6. 创建并启动 Project，等待 `obsidian-mcp` 和 `cloudflared` 两个服务均为运行状态。

这会重新构建 `restricted-obsidian-mcp` 镜像，因此新复制的 `app.py` 才会进入容器；仅复制文件不会改变正在运行的旧镜像。

## 3. 发布后验收

检查健康接口：

```bash
curl https://obsidian-mcp.lizhuchao.live/health
```

确认以下内容：

- `ok: true`
- `version` 与本地 `app.py` 中的 `APP_VERSION` 一致
- `vault_root` 是 `/vault`

还应通过 MCP `tools/list` 检查本次新增工具是否存在。例如当前关系与知识摄入能力至少应包含：

```text
checkpoint_conversation
ingest_knowledge
list_note_relations
suggest_note_relations
apply_note_relations
remove_note_relation
find_broken_note_links
```

## 故障排查

若线上版本号仍是旧的，通常是以下原因之一：

- 同步到了错误的 SMB 目录，而非 `chatgpt-obsidian-mcp/obsidian-mcp-app/`。
- Container Manager 使用的 Project 工作目录不对，导致没有从 `./obsidian-mcp-app` 重建。
- 只重启了旧容器，没有删除旧 Project 并重新创建。
- Cloudflare Tunnel 指向的不是刚重建的 `obsidian-mcp` 容器。

先核对 SMB 中的 `app.py` 与本地一致，再在 Container Manager 删除旧 Project 并按本说明重新创建。Vault 不在容器镜像中，因此不会因该操作丢失笔记。
