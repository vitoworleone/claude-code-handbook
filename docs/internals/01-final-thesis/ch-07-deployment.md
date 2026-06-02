# 第 7 章 部署实践

> 本章从部署视角横切 Claude ​Code 的实现。一个 Agent Runtime 要走出实验室成为生产工具，必须解决"如何打包、如何分发、如何配置、如何跨平台、如何启动得快"等一系列工程问题。Claude ​Code 在这些维度上的具体做法是本章的内容。

---

## 7.1 发布与分发

### 7.1.1 npm 包结构

Claude ​Code 通过 npm registry 发布为 `@anthropic-ai/claude-code` 包。包内主要内容（基于泄露的 build 产物推断）：

```text
@anthropic-ai/claude-code/
├── package.json
├── README.md
├── LICENSE
├── bin/
│   └── claude                     ← shebang 指向 cli.js
├── dist/
│   ├── cli.js                     ← Bun bundle 产物
│   ├── cli.js.map                 ← ⚠ 泄露的 source map
│   ├── main.js
│   ├── main.js.map                ← ⚠
│   └── ...其他打包产物
├── lib/                            ← 一些必要的资源
└── ...
```

`bin/claude` 是 npm install 时被 link 到全局 `node_modules/.bin/claude` 或全局 path 的可执行入口。它的内容大致是：

```bash
#!/usr/bin/env node
require('../dist/cli.js')
```

或在 Bun 环境下用 Bun 解释器执行。

### 7.1.2 Bun 打包产物

Claude ​Code 用 Bun bundler 把整个 TypeScript 工程打包成少量 `.js` 文件。打包策略推断：

- **多个 entry**：cli、main、init、mcp、print 等各自为独立 entry，按需 dynamic import
- **Code splitting**：通过 dynamic import 自然实现，让 fast path 不加载 main bundle
- **Build-time macros**：`feature()` 宏在打包时被展开为 if-true 或被 DCE
- **Source map**：默认生成（用于内部调试），**正常发布流程应剔除**

包大小（推断）：

- 完整 bundle：约 30-50 MB（含 React、Ink、Anthropic SDK、MCP SDK、Zod、Commander 等）
- bin 脚本：极小

### 7.1.3 sourcemap 未删除事件复盘

2026 年 3 月 31 日的 source map 泄露事件可以从工程角度做以下复盘：

**根本原因**：

打包流程中本应在发布前剔除 `.js.map` 的步骤被遗漏。可能的具体原因包括：

- CI 配置错误（如 `npm publish` 时没有运行 `prepublish` 钩子）
- `package.json` 的 `files` 字段没有显式排除 `*.map`
- `.npmignore` 缺失对应规则
- 内部 build script 与 publish script 解耦不清

**事件影响**：

| 影响维度 | 详情 |
|---------|------|
| 商业 | 算法/prompt/架构暴露 |
| 安全 | 攻击者可针对内部逻辑挖洞 |
| 法律 | 第三方依赖的 license 问题 |
| 信誉 | 工程严谨性受质疑 |
| 学术 | 罕见的工业级 Agent 研究样本（本论文的研究对象） |

**修复**：

Anthropic 在事件被报告后于次日发布了不含 source map 的新版本，并在 GitHub Discussion 中确认了事件。但泄露的源码已被多方下载和镜像，无法回收。

**对其他团队的启示**：

- 在 publish 前必须有 build-output 审计（`tar tvf claude-code-x.y.z.tgz | grep '\.map$'`）
- 关键产物的 file list 应该有显式 whitelist（`package.json` 的 `files` 字段）
- CI/CD 流程的"发布前最后一道闸门"必须独立于"构建脚本"
- Source map 应该单独上传到内部调试服务，不进 npm 包

### 7.1.4 自更新机制

Claude ​Code 内置自更新检查（推断的实现）：

- 启动时后台检查最新版本（与 fetchBootstrapData 等其他请求合并）
- 发现新版本时提示用户 `npm install -g @anthropic-ai/claude-code@latest`
- 不强制更新（让用户控制）
- 支持版本固定（避免 break change 影响生产）

源码 `cli.tsx:276-285` 的 "update rewrite 和 --bare early env" 处理涉及更新相关逻辑。

## 7.2 配置体系

### 7.2.1 settings.json 多级优先级

Claude ​Code 的配置体系是分层的：

```text
优先级（高 → 低）：

┌──────────────────────────────────┐
│ MDM Enterprise                    │  企业 IT 强制配置
│   - macOS: /Library/Managed       │
│           Preferences/...         │
│   - Windows: HKLM/HKCU registry   │
│   - Linux: /etc/ 下的策略文件     │
├──────────────────────────────────┤
│ Global User                       │  用户全局配置
│   ~/.claude/settings.json         │
├──────────────────────────────────┤
│ Project                           │  项目级配置
│   .claude/settings.json (in cwd)  │
├──────────────────────────────────┤
│ Local                             │  本地私有（.gitignore'd）
│   .claude/settings.local.json     │
└──────────────────────────────────┘
```

**为什么这个优先级**：

- MDM 最高：企业 IT 必须能强制锁定某些 settings（如禁用 BashTool）
- Local 最低：用户私有配置不应该覆盖团队约定
- Project 中等：团队共享的项目配置
- Global 次高：用户跨项目偏好

合并策略：

- 顶层字段：后覆盖前
- 数组字段（如 `permissions.allow`）：合并（高优先级追加到低优先级）
- 复杂字段（如 hooks）：复杂规则（高优先级可以禁用低优先级的 hook）

### 7.2.2 CLAUDE.md 加载与 @include

CLAUDE.md 是项目级的 system prompt 补充，用于告诉 Agent："这个项目是什么、它的约定是什么、应该如何工作"。

加载策略：

```text
1. 当前 cwd 下查找 CLAUDE.md
2. 逐级向上查找父目录，直到找到 git root 或 home
3. 找到的所有 CLAUDE.md 按层级顺序合并（外层在前，内层在后）
4. 每个 CLAUDE.md 中的 @include 递归展开
```

**@include 语法**：

```markdown
# Project CLAUDE.md

This is a Python web service.

@include ./style-guide.md
@include ./architecture.md
@include ~/.claude/personal-coding-style.md
```

`@include` 行被替换为目标文件的内容。递归展开，支持嵌套。

**安全约束**：

- `@include` 不能引用网络 URL（避免 SSRF）
- 路径必须在 trust 范围内（Trust Boundary 之外的路径会被拒绝）
- 循环 include 会被检测并报错

### 7.2.3 MDM Enterprise 配置

MDM（Mobile Device Management，但实际泛指企业设备管理）配置由企业 IT 推送到员工设备。Claude ​Code 在启动时通过平台特定接口读取：

| 平台 | 读取方式 |
|------|---------|
| macOS | `plutil -p /Library/Managed Preferences/com.anthropic.claude.plist` |
| Windows | `reg query HKLM/HKCU\Software\Anthropic\Claude` |
| Linux | 读取 `/etc/anthropic/claude.conf` 或类似策略文件 |

`src/utils/settings/mdm/rawRead.ts` 的实现（详见 7.5.2）专门为启动并行化设计，是最小依赖模块。

MDM 配置可以强制：

- 禁用某些工具（如 `WebFetchTool`）
- 强制使用企业代理
- 注入企业 CA 证书
- 禁用 telemetry
- 锁定模型为特定版本

用户的本地 settings 无法覆盖这些强制配置。

### 7.2.4 环境变量与 .env

环境变量是另一种配置来源。关键变量：

| 变量 | 用途 |
|------|------|
| `ANTHROPIC_API_KEY` | API key |
| `ANTHROPIC_BASE_URL` | API endpoint（支持 Bedrock / GCP / 第三方兼容） |
| `CLAUDE_CODE_SIMPLE` | `--bare` 模式（设为 1） |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | 禁用 auto memory |
| `CLAUDE_CODE_COORDINATOR_MODE` | 启用 coordinator mode |
| `CLAUDE_CODE_PROFILE_STARTUP` | 启动性能分析 |
| `CLAUDE_CODE_EXIT_AFTER_FIRST_RENDER` | 首次渲染后立即退出（测试用） |

`.env` 文件支持（项目根目录的 `.env` 自动加载，但 Trust Boundary 后才生效）。

## 7.3 多入口部署形态

### 7.3.1 REPL：终端交互

最常用形态。直接运行 `claude` 进入：

```bash
$ claude
> Welcome to Claude ​Code v2.5.0
> Working directory: /Users/alice/projects/my-app
>
> _
```

特征：
- React + Ink TUI
- 流式输出
- 权限弹窗
- Slash command 补全
- 多行输入支持

### 7.3.2 Headless / SDK：CI / 脚本

`--print` 或 `-p` 模式：

```bash
$ echo "What does this repo do?" | claude -p
> This is a Next.js web application that ...
$ exit_code=0
```

或：

```bash
$ claude -p "Refactor user.py to use bcrypt"
```

特征：
- 不启动 Ink TUI
- 输出到 stdout
- 单次执行后退出
- 适合 CI 集成

**SDK 集成**：

```typescript
import { QueryEngine } from '@anthropic-ai/claude-code-sdk'

const engine = new QueryEngine({ ... })
for await (const message of engine.submitMessage("Hello")) {
  console.log(message)
}
```

### 7.3.3 MCP Server：作为后端被调用

```bash
$ claude mcp
```

启动 MCP Server，通过 stdio 与 client 通信：

```typescript
// entrypoints/mcp.ts 结构还原
async function startMcpServer() {
  const server = new McpServer({ name: 'claude-code', version })

  for (const tool of getInternalTools()) {
    server.registerTool(tool.name, tool.inputSchema, wrapToolAsMcpHandler(tool))
  }

  await server.connect(new StdioServerTransport())
}
```

用例：让其他 AI 应用（如 Cursor、Continue）复用 Claude ​Code 的工具实现（FileEdit、Bash 等），通过 MCP 协议调用。

### 7.3.4 Bridge / Remote：与云端协同

```bash
$ claude bridge --orchestrator-url=wss://...
```

Bridge 模式让本地 Claude ​Code 成为云端编排器的执行节点：

- 本地连接 WebSocket 到 orchestrator
- orchestrator 派发任务
- 本地执行 query()（权限、工具、记忆都在本地）
- 结果通过 WebSocket 回传

适用场景：
- 团队共享一个 orchestrator，让多个开发者的本地机器协同工作
- 中心化的调度与监控

### 7.3.5 Background / Daemon：长任务

```bash
$ claude --bg "重构整个 auth 模块"
> Started background session abc123

$ claude ps
> abc123  running  开始于 2 分钟前  重构整个 auth 模块

$ claude attach abc123
> [attach 到 abc123 看实时输出]

$ claude kill abc123
```

后台模式让长任务（可能跑几十分钟）不阻塞用户当前终端：

- 输出重定向到日志文件
- 状态存在 TaskRegistry
- 可以 attach / detach / kill

### 7.3.6 IDE 集成

Claude ​Code 提供 VS Code 与 JetBrains 扩展。扩展通过两种方式与 Runtime 集成：

1. **直接 spawn `claude` 子进程**：扩展把用户输入通过 stdin 传递，从 stdout 读输出
2. **MCP 协议**：扩展作为 MCP client，Claude ​Code 作为 MCP server

IDE 扩展还提供：
- 编辑器选区作为上下文
- inline diff 渲染
- 状态栏集成
- 命令面板触发

### 7.3.7 --bare 模式：最小裁剪

```bash
$ claude --bare "简单问题"
```

`--bare` 模式跳过以下初始化：

- hooks watcher
- LSP 初始化
- plugin sync
- attribution
- auto-memory
- background prefetches
- keychain reads
- CLAUDE.md auto-discovery

同时限制：

- Anthropic auth 限制为 API key 或 settings 中的 apiKeyHelper
- OAuth / keychain 不读取

用例：
- CI 环境（无 keychain，不需要 auto-memory）
- 单次脚本任务（不需要 CLAUDE.md 自动发现）
- 性能敏感场景（启动时间最小化）

源码 `cli.tsx:281-285` 在 import `main.js` **前**就设置 `CLAUDE_CODE_SIMPLE=1`，让模块 eval / Commander option building 期间的 gates 也能生效。

## 7.4 跨平台适配

### 7.4.1 macOS 适配

macOS 特有处理：

- **Keychain 集成**：通过 `security` 命令读 API key / OAuth token
- **MDM plist**：从 `/Library/Managed Preferences/` 读取
- **iTerm2 detection**：检查 iTerm2 native pane 支持
- **alt-screen 行为**：iTerm2 / Terminal.app 略有差异

`keychainPrefetch.ts` 是 macOS 专属（其他平台直接 return）。

### 7.4.2 Windows 适配

Windows 特有处理：

- **PATH hijacking 防护**：`NoDefaultCurrentDirectoryInExePath=1`（详见 6.6.1）
- **PowerShell 支持**：默认使用 `PowerShellTool` 而非 `BashTool`
- **Registry**：从 HKLM / HKCU 读 MDM 策略
- **路径分隔符**：自动转换 `/` 与 `\`
- **行结尾**：CRLF 处理

### 7.4.3 Linux 适配

Linux 特有处理：

- **MDM**：读 `/etc/anthropic/claude.conf` 等策略文件
- **Keychain**：通过 libsecret / GNOME Keyring（如果可用），否则降级到 plain text 存储 + warning
- **shell**：默认 bash，支持 zsh / fish

### 7.4.4 tmux / iTerm2 多面板

Agent Teams 的 tmux backend 与 iTerm2 backend 适配：

- **tmux**：通过 `tmux new-window` 创建窗格运行 teammate
- **iTerm2**：通过 AppleScript 创建 split pane

`backend/registry.ts` 的 `detectAndGetBackend()`：

```typescript
export async function detectAndGetBackend() {
  const insideTmux = await isInsideTmux()
  const inITerm2 = isInITerm2()
  if (insideTmux) return createTmuxBackend()
  if (inITerm2) {
    if (!check_it2_installed()) return needsIt2Setup
    return createITermBackend()
  }
  return createInProcessBackend()
}
```

优先级：tmux > iTerm2 > in-process。这种"detect + fallback"的模式让 Claude ​Code 在不同终端环境下都能合理工作。

## 7.5 启动优化实践

### 7.5.1 两级入口分流

第 4.2.1 节已经详述。核心结构：

```text
cli.tsx（轻量入口，处理 fast path）
   ↓ dynamic import
main.tsx（4683 行，完整应用）
```

`--version` 在 cli.tsx 中就返回，不加载 main.tsx 的 React/Ink/MCP 等重型依赖。这种分流让最常见的"看版本号"命令保持极低成本。

### 7.5.2 并行 IO 预热

`main.tsx` 顶部触发并行 IO：

```typescript
// main.tsx:1-20
profileCheckpoint('main_tsx_entry')

import { startMdmRawRead } from './utils/settings/mdm/rawRead.js'
startMdmRawRead()                       // 触发 MDM 子进程读取

import { startKeychainPrefetch } from './utils/secureStorage/keychainPrefetch.js'
startKeychainPrefetch()                 // 触发 keychain 预取
```

这两个操作在 import 评估期间就后台触发。当 Phase 2 需要这些数据时，结果已经在内存中。

**性能数据**：
- MDM raw read：节省约 135ms
- Keychain prefetch：节省约 65ms
- `setup()` 与 `getCommands()` 并行：节省约 28ms

**关键约束**：top-level 模块必须轻量，不能反过来 import 巨大的配置/认证链，否则优化会被抵消。源码中：

- `rawRead.ts` 只 import child_process / fs / constants
- `keychainPrefetch.ts` 不 import 重的 keychain storage 链

### 7.5.3 Dynamic Import 边界

Claude ​Code 在启动路径中同时使用三类动态导入：

| 场景 | 目的 | 示例 |
|------|------|------|
| fast path 按需加载 | 避免快路径加载完整应用 | `cli.tsx` 中 `await import('./main.tsx')` |
| 模式隔离 | interactive/headless 不互相承担成本 | `main.tsx` 中 `await import('./ink.js')` / `await import('./cli/print.js')` |
| 循环依赖解决 | lazy factory 模式 | `() => require('./module')` |
| 构建裁剪 | feature-gated 条件 require | `ifFeature('X', () => require('./x.js'))` |

### 7.5.4 Lazy Require 解决循环依赖

Claude ​Code 依赖链中存在循环：

```text
main.tsx → tools → AgentTool → coordinator → main.tsx  ← 循环！
```

Node.js / Bun 遇到循环依赖时被循环导入的模块可能是半初始化状态，导致 undefined 错误。

**解决方案**：

```typescript
// 用 lazy factory 替代顶层 import
const getAgentTool = () => require('./agent/agentTool')

// 使用时才执行 require，此时所有模块已完全初始化
const agentTool = getAgentTool()
```

这种模式在 `main.tsx:68-81` 大量使用。

### 7.5.5 Deferred Prefetch 推迟到 first render 后

`startDeferredPrefetches()`（`main.tsx:382-431`）：

```typescript
function startDeferredPrefetches() {
  if (process.env.CLAUDE_CODE_EXIT_AFTER_FIRST_RENDER) return
  if (process.env.CLAUDE_CODE_SIMPLE) return

  initUser().catch(() => {})
  getUserContext().catch(() => {})
  getSystemContext().catch(() => {})
  // ... 更多预取
}
```

`interactiveHelpers.tsx:98-103` 在 **first render 后**调用 `startDeferredPrefetches()`。设计意图：交互界面先出现，用户 typing 的窗口用于隐藏后续预热成本。

对比：
- **interactive 模式**：用户在 typing 时预取
- **headless 模式**：没有 typing window，所以立即 start deferred prefetches
- **`--bare` 模式**：直接跳过

这是一种按用户模式差异化的性能策略。

### 7.5.6 启动六阶段时间线

| 阶段 | 行数范围 | 职责 | 失败含义 |
|------|---------|------|---------|
| Phase 0 | `main.tsx` 前 20 行 | 并行触发重量级 IO | 操作系统级问题 |
| Phase 1 | 21-200 行 | 模块设置：延迟 require、特性门控、条件导入 | 功能模块损坏 |
| Phase 2 | 201-3700 行 | CLI 解析、配置加载、认证检查、权限加载、工具装配 | 参数格式错误 |
| Phase 3A | 3700+ 行 (interactive) | TUI 启动 | TUI 初始化错误 |
| Phase 3B | 3700+ 行 (headless) | print runtime | headless 初始化错误 |

每个阶段失败有不同含义，错误上下文精确。这比一个 `main` 函数 try-catch 所有错误要好得多。

### 7.5.7 启动性能观测体系

`src/utils/startupProfiler.ts` 定义启动指标体系。它不是事后加的日志，而是 startup path 的一等设计目标。

**Phase 定义**：

```typescript
const PHASES = {
  import_time:  ['cli_entry',              'main_tsx_imports_loaded'],
  init_time:    ['init_function_start',    'init_function_end'],
  settings_time:['eagerLoadSettings_start','eagerLoadSettings_end'],
  total_time:   ['cli_entry',              'main_after_run']
}
```

**Checkpoint 打点**散落在 `cli.tsx` 和 `main.tsx`，构成一条可观测的启动时间线。

**报告输出**：

| 环境变量 | 行为 |
|---------|------|
| `CLAUDE_CODE_PROFILE_STARTUP=1` | 启用详细启动性能分析 |
| 无 | 按 Statsig sampling 决定是否采样 |

详细模式下会记录每个 checkpoint 的时间 + memory snapshot，输出到文件供后续分析。采样模式下只发精简 log 到 telemetry。

## 7.6 升级与回滚

### 7.6.1 settings 迁移

新版本可能改变 settings.json 的 schema。Claude ​Code 通过 migration 机制处理：

- 旧字段重命名 → 自动重映射
- 字段类型改变 → 自动转换
- 字段删除 → 警告 + 备份

`runMigrations()` 在 preAction 阶段执行（`main.tsx:907-966`）。

### 7.6.2 session schema 兼容

Session JSONL 格式也可能变化。Claude ​Code 的处理：

- 旧格式 session 可以被新版本读取（向下兼容）
- 通过 schema version 字段标识
- 不兼容的字段会被 normalize_transcript 处理

### 7.6.3 工具 alias 与 deprecation

工具名可能改变（如 `Read` → `FileRead`）。Claude ​Code 通过 `aliases` 字段处理：

```typescript
const FileReadTool: Tool = {
  name: 'FileRead',
  aliases: ['Read'],  // 旧名兼容
  // ...
}
```

`findToolByName()` 在没找到精确名时尝试 alias fallback。旧 transcript 中的 `Read` tool_use 在新版本下仍能工作。

## 7.7 监控与运维

### 7.7.1 Telemetry 体系

Claude ​Code 收集以下 telemetry（用户可关闭）：

- **匿名使用数据**：哪些工具被调用、调用频率
- **性能数据**：启动时间、API latency、工具执行时长
- **错误数据**：未捕获异常、API 错误模式
- **A/B 测试**：通过 Statsig sampling

**不收集**：

- 用户的具体代码内容
- 文件路径详情（仅 hash）
- API request/response 内容
- 环境变量值

### 7.7.2 Statsig 集成

Statsig 是 feature flag 与 A/B testing 服务。Claude ​Code 用 Statsig：

- 控制实验性功能的灰度发布（如新的 compact 策略）
- 控制 telemetry 采样率
- 收集性能数据

### 7.7.3 OpenTelemetry 集成

OTel span 覆盖关键操作：

- `user_prompt` interaction span
- `query_turn` span
- `tool_call` span
- `permission_decision` span

企业用户可以配置 OTel collector 端点，接收这些 span 用于本地监控。

## 7.8 容器化与隔离部署

### 7.8.1 Docker 部署

虽然 Claude ​Code 主要面向本地开发，但也支持容器化部署：

```dockerfile
FROM node:20-slim

RUN npm install -g @anthropic-ai/claude-code

ENV ANTHROPIC_API_KEY=...
ENV CLAUDE_CODE_SIMPLE=1

WORKDIR /workspace
CMD ["claude"]
```

容器化场景下通常用 `--bare` 模式（无 keychain、无 hooks）。

### 7.8.2 Worktree 隔离

对于高风险操作，可以让 Claude ​Code 在临时 git worktree 中工作：

```bash
$ claude --worktree
# 自动创建一个临时 worktree
# Agent 在其中操作
# 操作完成后用户决定是否 merge 回主分支
```

### 7.8.3 Sandbox 模式

实验性 sandbox 模式让 Agent 在受限环境中运行：

- 文件系统访问限制
- 网络访问限制
- 子进程数量限制

适用于"运行未知项目"或"评测 Agent 行为"的场景。

## 7.9 关键部署设计权衡

| 决策 | What | Why | Trade-off |
|------|------|-----|-----------|
| npm 分发 | 通过 npm registry | 开发者熟悉，全球可用 | 受 npm 安全 / 可用性限制 |
| Bun bundler | 自带打包器 | 启动快、TS 原生支持 | 与 Node-only 包的兼容偶有问题 |
| ifFeature 编译期裁剪 | 三个构建变体（OSS/Enterprise/Internal） | 减小 OSS 包体积 | 代码中散布 feature 判断 |
| 多级 settings | MDM > Global > Project > Local | 企业管理 + 个人配置兼顾 | 合并规则复杂 |
| 九种入口形态 | 覆盖各种使用场景 | 一套 Runtime 多种部署 | 入口逻辑分散 |
| 并行 IO 预热 | startup 顶部触发 | 节省 200ms+ | top-level 模块依赖必须轻 |
| Dynamic import 分流 | fast path 不加载完整应用 | 节省 200-500ms | 控制流更复杂 |
| Trust Boundary | 初始化两阶段 | 防止 config 攻击 | 启动流程更长 |
| Deferred prefetch | first render 后才启动 | 改善 perceived latency | headless 模式需要特殊处理 |

---

## 本章小结

本章从部署视角横切了 Claude ​Code 的实现：

- **发布与分发**：npm 包结构、Bun bundler、sourcemap 泄露事件复盘、自更新机制
- **配置体系**：多级 settings 优先级、CLAUDE.md 加载、MDM 配置、环境变量
- **多入口形态**：REPL / Headless / MCP Server / Bridge / Background / IDE / --bare 共 7+ 种
- **跨平台适配**：macOS / Windows / Linux 的特化处理 + tmux / iTerm2 多面板
- **启动优化**：两级入口分流、并行 IO 预热、dynamic import 边界、lazy require、deferred prefetch、六阶段时间线、startupProfiler
- **升级与回滚**：settings 迁移、session schema 兼容、工具 alias
- **监控与运维**：Telemetry、Statsig、OpenTelemetry
- **容器化与隔离**：Docker、worktree、sandbox

核心观察：

- 部署不是事后包装，而是从工程一开始就被设计
- 启动性能是一等工程目标，影响代码结构（两级入口、动态 import、并行 IO）
- 多级配置 + 多种入口形态让 Claude ​Code 能服务于差异极大的场景
- Trust Boundary 与 Fail-Closed 保证部署的安全性
- 跨平台不是"加几个 if-else"，而是设计层面的考量（如 keychainPrefetch 模块化）

第 8 章将给出 Claude ​Code 的测试与评估，包括复刻验证、竞品对比、设计决策评估。
