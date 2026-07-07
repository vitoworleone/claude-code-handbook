# AGENTS.md — claude-code-handbook 仓库维护指南

> 本文件供 AI Agent（包括 Claude Code）和人类维护者参考。修改仓库结构前请先读此文件。

---

## 1. 项目定位

本仓库是 **Claude Code 的实战知识库 + Agent Runtime 工坊**，面向想"用得好"和"造得出" Claude Code 类 Agent 的开发者。

包含四条主线：

- **使用手册** — 从安装配置到高级技巧的完整实战指南
- **实践配方** — 可直接复用的代码、配置、Skill 模板和参考对照
- **Agent Runtime 工坊** — 手把手从零实现 Coding Agent 的教程与参考实现
- **Harness 笔记** — Agent Harness 架构学习与整理

不是学术论文集，也不是官方文档复读。内容是实战经验沉淀，长期可更新，随认知迭代持续补充。

---

## 2. 目录结构规范

### 2.1 顶层目录

```
claude-code-handbook/
├── docs/                          # 全部文档
│   ├── assets/                    # 静态资源
│   │   ├── avatar.png
│   │   ├── banner.png
│   │   └── xiaomi-images/         # 小米实践插图
│   ├── manual/                    # 使用手册
│   │   ├── README.md              # 手册总览
│   │   ├── part-01-overview/      # 认识 Claude Code
│   │   ├── part-02-basics/        # 基础配置
│   │   ├── part-03-workflow/      # 核心工作流
│   │   ├── part-04-context/       # 记忆与上下文
│   │   ├── part-05-extension/     # 扩展与自动化
│   │   ├── part-06-parallel/      # 并行与协作
│   │   ├── part-07-security/      # 安全与规范
│   │   └── part-08-advanced/      # 高级技巧
│   ├── recipes/                   # 实践配方
│   │   ├── agents/                # Agent 示例代码
│   │   ├── docs/                  # 中英学习文档
│   │   │   ├── en/
│   │   │   └── zh/
│   │   ├── skills/                # SKILL.md 模板
│   │   │   ├── agent-builder/
│   │   │   ├── code-review/
│   │   │   ├── mcp-builder/
│   │   │   └── pdf/
│   │   └── source-analysis/       # Agent Harness 对照参考
│   └── workshop/                  # Agent Runtime 工坊
│       ├── README.md              # 工坊总览
│       └── chapters/              # 10 章核心教程
│           └── ch-XX-name/
│               ├── theory.md
│               ├── python-implementation.md
│               ├── hands-on.md
│               └── images/
│
├── harness/                       # Agent Harness 学习笔记
│   └── README.md
│
├── mewcode/                       # Python 版 Agent Runtime 参考实现
│   ├── README.md
│   ├── pyproject.toml
│   ├── mewcode/
│   └── tests/
│
├── .gitignore
├── AGENTS.md                      # ← 本文件
├── LICENSE                        # MIT
└── README.md                      # GitHub 首页
```

### 2.2 命名规范

- **目录**: kebab-case（`part-01-overview`, `query-loop-deep-dive`）
- **Markdown 文件**: kebab-case（`memory-system-guide.md`）
- **Python 模块**: snake_case（`query_loop.py`, `streaming_executor.py`）
- **避免**: 中文文件名（URL 不友好）、空格（命令行不友好）

### 2.3 文档分类逻辑

| 类别 | 位置 | 内容示例 |
|------|------|----------|
| 使用手册 | `docs/manual/part-*/` | 8 部分实战指南 |
| Agent 示例 | `docs/recipes/agents/` | agent loop、tool use、subagent、teams |
| Skill 模板 | `docs/recipes/skills/` | agent-builder、code-review、mcp-builder |
| 学习文档 | `docs/recipes/docs/{en,zh}/` | Claude Code 中英教程 |
| Harness 参考 | `docs/recipes/source-analysis/` | OpenCode / Codex / DeerFlow 对照分析 |
| 工坊教程 | `docs/workshop/chapters/ch-XX-name/` | theory / python-implementation / hands-on |
| Harness 笔记 | `harness/` | Agent Harness 架构学习与整理 |
| 参考实现 | `mewcode/` | Python 版 Agent Runtime 完整实现 |
| 静态资源 | `docs/assets/` | banner、avatar、小米实践配图 |

---

## 3. 文件准入规则

### ✅ 应该提交的

- Markdown 文档（`.md`）
- Python 源码（`.py`）—— `mewcode/` 和 `docs/recipes/agents/` 中的示例代码
- 配置文件（`.env.example`, `pyproject.toml`, `uv.lock` 等）
- 静态图片（`.png`, `.svg`）—— 用于文档插图
- 小型脚本（`.py`, `.ps1`, `.sh`）—— 如果是配方或项目的一部分

### ❌ 不应该提交的

| 类型 | 原因 | 是否已排除 |
|------|------|------------|
| `.venv/` | 虚拟环境，通过依赖文件重建 | ✅ .gitignore |
| `.env` | 含 API Key 等敏感信息 | ✅ .gitignore |
| `__pycache__/` | 运行时缓存 | ✅ .gitignore |
| `.claude/` / `.traces/` | 工具运行时生成 | ✅ .gitignore |
| `.git/`（嵌套） | 嵌套仓库历史 | ✅ .gitignore |
| `*.zip` / `*.tar.gz` | 压缩包（冗余或可从源重建） | ✅ .gitignore |
| 原始 TS 源码映射 | 版权风险，只保留分析文档 | ⚠️ 手动处理 |

---

## 4. 更新流程

### 4.1 新增手册章节

1. 放入对应 `docs/manual/part-*/`
2. 章节编号保持连续
3. 更新 `docs/manual/README.md` 中的目录

### 4.2 新增实践配方

1. **Agent 示例**：放入 `docs/recipes/agents/`，文件命名说明用途
2. **Skill 模板**：放入 `docs/recipes/skills/{skill-name}/SKILL.md`
3. **学习文档**：按语言放入 `docs/recipes/docs/{en,zh}/`
4. **Harness 参考**：放入 `docs/recipes/source-analysis/`

### 4.3 新增 Agent Runtime 工坊内容

1. **工坊章节**：放入 `docs/workshop/chapters/ch-XX-name/`
2. 每章包含 `theory.md`、`python-implementation.md`、`hands-on.md` 和 `images/`
3. 章节编号保持 `ch-01` 到 `ch-99` 格式
4. 更新 `docs/workshop/README.md` 中的章节索引

### 4.4 新增 mewcode 项目内容

1. `mewcode/` 是 Python 参考实现，保持独立可运行
2. Python 模块放入 `mewcode/mewcode/`
3. 测试放入 `mewcode/tests/`
4. 修改后确保 `uv sync && pytest` 通过

### 4.5 新增 Harness 笔记

1. 放入 `harness/` 目录
2. 使用 kebab-case 命名
3. 在 `harness/README.md` 中维护索引

### 4.6 修改前检查清单

```
□ 新文件命名符合 kebab-case / snake_case
□ 没有意外包含 .env / __pycache__ / .venv
□ Markdown 中不出现本地文件名称引用
□ Markdown 中图片使用相对路径
□ 如果移动文件，更新 README 中的链接
□ 提交信息用中文简述变更内容
```

---

## 5. 版权注意事项

- recipes 中的 Python 代码为**原创示例**，可以上传
- 原始 TypeScript 源码映射**涉及版权**，只保留分析文档
- 分析文档需注明"基于公开资料与源码映射的分析理解"

---

## 6. 快速参考

### 本地预览

```bash
# 使用任意 Markdown 预览工具
npx serve .                  # 或 python -m http.server 8000
```

### 链接检查

修改文件路径后，建议全局搜索旧路径，确保 README 和手册目录中的链接已更新。

---

*最后更新: 2026-07-07*
