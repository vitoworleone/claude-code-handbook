# AGENTS.md — ClaudeCode-Complete 仓库维护指南

> 本文件供 AI Agent（包括 Claude Code）和人类维护者参考。修改仓库结构前请先读此文件。

---

## 1. 项目定位

本仓库是 **Claude Code 的完整知识库**，包含两条互补主线：

- **理解内核** — Agent Runtime 机制的源码拆解与重建
- **高效使用** — 从使用者视角出发的实战指南与工作流

不是业务产品，而是机制理解与实战经验的沉淀。长期可更新，随认知迭代持续补充。

---

## 2. 目录结构规范

### 2.1 顶层目录

```
claude-code-complete/
├── docs/                          # 全部文档
│   ├── internals/                 # 内核知识库（论文 + 分析 + 调研）
│   │   ├── 00-overview.md         # 项目定位与架构分层
│   │   ├── 01-final-thesis/       # 完整论文 9 章
│   │   ├── 02-system-analysis/    # 源码级中文分析
│   │   ├── 03-research-reports/   # 独立深度调研报告
│   │   ├── source-analysis/       # 源码映射精华分析
│   │   ├── transcripts/           # 文字记录
│   │   └── learn-claude-code-*/   # 教程材料
│   ├── manual/                    # 使用手册
│   │   ├── part-01-overview/      # 认识 Claude Code
│   │   ├── part-02-basics/        # 基础配置
│   │   ├── part-03-workflow/      # 核心工作流
│   │   ├── part-04-context/       # 记忆与上下文
│   │   ├── part-05-extension/     # 扩展与自动化
│   │   ├── part-06-parallel/      # 并行与协作
│   │   ├── part-07-security/      # 安全与规范
│   │   └── part-08-advanced/      # 高级技巧
│   └── assets/                    # 静态资源
│       ├── images/                # 通用插图
│       └── xiaomi-images/         # 小米实践插图
│
├── src/                           # Python 运行时源码
│   └── cc-python-runtime/         # Python 复刻实现
│       ├── cc/                    # 主源码包
│       └── tests/
│
├── scripts/                       # 维护与验证脚本
│   ├── clean-for-github.ps1
│   └── check-size.ps1
│
├── .gitignore
├── AGENTS.md                      # ← 本文件
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
| 项目总览 | `docs/internals/00-overview.md` | 定位、边界、架构分层 |
| 论文 | `docs/internals/01-final-thesis/` | 9 章完整论文 |
| 系统分析 | `docs/internals/02-system-analysis/` | query loop、工具、权限、记忆 |
| 调研报告 | `docs/internals/03-research-reports/` | 缓存优化、上下文连续性 |
| 源码分析 | `docs/internals/source-analysis/` | 源码映射精华分析 |
| 文字记录 | `docs/internals/transcripts/` | 讲解稿、复杂机制补充 |
| 使用手册 | `docs/manual/part-*/` | 8 部分实战指南 |
| 运行时源码 | `src/cc-python-runtime/` | Python 复刻实现 |

---

## 3. 文件准入规则

### ✅ 应该提交的

- Markdown 文档（`.md`）
- Python 源码（`.py`）
- 配置文件（`pyproject.toml`, `.env.example`, `uv.lock`）
- 静态图片（`.png`, `.svg`）—— 用于文档插图
- 脚本（`.py`, `.ps1`, `.sh`）

### ❌ 不应该提交的

| 类型 | 原因 | 是否已排除 |
|------|------|------------|
| `.venv/` | 虚拟环境，通过 pyproject.toml 重建 | ✅ .gitignore |
| `.env` | 含 API Key 等敏感信息 | ✅ .gitignore |
| `__pycache__/` | 运行时缓存 | ✅ .gitignore |
| `.claude/` / `.traces/` | 工具运行时生成 | ✅ .gitignore |
| `.git/`（嵌套） | 嵌套仓库历史 | ✅ .gitignore |
| `*.zip` / `*.tar.gz` | 压缩包（冗余或可从源重建） | ✅ .gitignore |
| `feishu-export/` | 飞书导出中间产物 | ✅ .gitignore |
| `.backup-summaries/` | 备份摘要 | ✅ .gitignore |
| HTML 归档 | 大且可重建 | ✅ .gitignore |
| 原始 TS 源码映射 | 版权风险，只保留分析文档 | ⚠️ 手动处理 |

---

## 4. 更新流程

### 4.1 新增内核文档

1. 确定文档类别（final-thesis / system-analysis / research-reports / source-analysis）
2. 使用 kebab-case 命名文件
3. 内容中**不要出现本地文件名称**（如 `02-Report-Chapter2-Section1.md`）

### 4.2 新增手册章节

1. 放入对应 `docs/manual/part-*/`
2. 章节编号保持连续
3. 更新 `docs/manual/README.md` 中的目录

### 4.3 新增源码模块

1. 放入 `src/cc-python-runtime/cc/` 对应子包
2. 必须包含 `__init__.py`
3. 配套单元测试放入 `tests/unit/对应路径/`

### 4.4 修改前检查清单

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

- `src/` 中的 Python 代码是**原创复刻**，可以上传
- 原始 TypeScript 源码映射（`src.zip` 和解压后的 `src/`）**涉及版权**，只保留分析文档
- 分析文档需注明"基于 Claude Code 源码映射的分析理解"

---

## 6. 快速参考

### 重建开发环境

```bash
cd src/cc-python-runtime
uv sync        # 或 pip install -e .
pytest tests/
```

### 清理非仓库文件

```powershell
.\scripts\clean-for-github.ps1 -DryRun   # 预览
.\scripts\clean-for-github.ps1           # 执行
```

### 检查上传大小

```powershell
.\scripts\check-size.ps1
```

---

*最后更新: 2026-06-02*
