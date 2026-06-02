# 第一集：CLI 入口与 React 终端渲染

我先抛一个问题：你每天用的终端 Bash、Vim、Git，它们的界面是怎么渲染的？

答案很简单，`print` 一句，打印字符到 stdout 就显示在终端里。

现在，如果我告诉你 **Claude Code** 这个命令行工具，它的界面是用 **React 组件** 渲染的，你的第一反应是什么？

是不是觉得：这不是太重了吗？React 是给网页用的。

这正是今天要回答的问题——为什么 Claude Code 选择了这个看起来过重的方案，背后的工程权衡是什么。

## 研究对象

Claude Code 是 Anthropic 官方出品的编程助手 CLI。它的目标很具体：

1. 你在终端里，它也在终端里——不是打开浏览器，不是调用 API，直接在你的开发环境里工作。
2. 它能读写你的文件，直接操作本地文件系统，不需要你粘贴代码。
3. 你可以和它对话，实时流式输出，像一个有感知的命令行工具。

这和 DeepFlow Web 平台、NanoClaw 消息助手完全不同。它解决的核心问题是：**开发者在本地工作时如何让 AI 真正融入工作流**。

## 用 React 渲染终端解决了什么问题

### 问题一：流式输出的刷新

Claude 的响应是流式的，Token 一个一个来。

如果用传统方式，你需要记录光标位置，收到新 token 把光标移回去，重新打印这一行，还要处理边界情况，比如换行、终端宽度变化——这起码要 20 行代码。

但如果用 React 呢？就是这两行：

```jsx
const [text, setText] = useState("");
// 每次 token 到来
setText(prev => prev + newToken);
```

React 的虚拟 DOM 自动计算什么变了，只更新变化的部分——简单多了。

### 问题二：权限确认弹窗

当 Claude 要执行一个危险命令，比如删除目录，传统做法是暂停当前输出，显示确认框，等你输入 yes 或 no，然后恢复之前的状态。

而用 React，就是在组件树里插入一个 `PermissionDialog` 组件，用 state 控制显示/隐藏。CSS 里 z-index 的逻辑在终端里叫渲染顺序，原理是一样的。

### 问题三：工具执行进度

BashTool 执行时要显示执行了多少秒、输出了多少行，结果还要可以折叠展开。这些都是组件状态，声明式管理起来非常自然。

代价是引入了 React 的学习曲线和重渲染复杂度。但对于 Claude Code 的 UX 需求，这个代价值得。

## 四层架构

整个系统分四层：

1. **CLI 入口** — `main.tsx`，负责解析各种参数，比如 `--model`、`--cwd`、`--permission-mode`。这个文件有 4683 行，大部分是参数处理和初始化逻辑。

2. **TUI 层** — 用 React 和 Ink 渲染终端界面。有 70 多个 custom hooks 处理键盘输入、状态订阅、权限对话框。

3. **查询引擎** — `Query.ts`，负责调用 Anthropic API、处理流式响应、执行工具调用，循环直到完成。

4. **工具和服务** — 60 多个内置工具加上 MCP 集成、上下文压缩、记忆系统。

每一层之间都有明确的边界和通信方式。

## 四层之间的关键交界点

- **CLI → TUI**：靠 `launchRender` 这个函数，把解析好的配置传给 React 根组件。
- **TUI → 查询引擎**：靠 `executeQueuedInput`，把用户输入提交给 Query 模块。
- **查询引擎 → 工具**：通过 `StreamingToolExecutor` 的 `execute` 方法，可以并行执行工具。

这三个函数就是整个系统的主动脉，理解它们就理解了整个数据流。

## 一条消息的完整旅程

追踪用户按下 Enter 到 Claude 响应的完整过程：

1. 用户输入后调用 `enqueueCommand`，把消息放入内存队列，而不是直接执行。
2. `useQueueProcessor` 监听到队列变化，检查 `QueryGuard` 是否空闲。
3. 如果空闲，调用 `QueryGuard.reserve()` 锁定资源。
4. 启动异步生成器，开始调用 Anthropic API。
5. 流式接收响应的过程中，如果 Claude 决定调用工具，就并行执行工具。
6. 把结果异步地喂回去，继续下一轮循环。
7. 直到 API 返回 end_turn，才释放 `QueryGuard`，更新 UI，显示最终消息。

整个过程中，用户看到的是实时流式输出，不是等全部完成后再一次性显示。

## 核心设计：事件驱动 + 异步生成器

整个流程是事件驱动加上异步生成器。没有回调地狱，也没有 EventEmitter 传递状态。

调用方式需要用 `for await...of` 循环来消费 Query 生成的事件。如果收到 `ToolUse` 事件，就执行工具，然后继续循环。

代码逻辑是线性的，生命周期非常好管理。

## 关键目录结构

`src/` 下有几个核心文件：

- `main.tsx` — CLI 入口，4683 行
- `query.ts` — 查询循环核心，1765 行
- `QueryEngine.ts` — SDK 模式的包装，1295 行
- `tools/` — 60 多个工具实现，包括 BashTool、AgentTool、FileEditTool、MCPTool 等等
- `hooks/` — 70 多个 React Hooks
- `state/` — 状态管理

这个结构清晰地反映了分层的架构。

## 三个系统的对比

| 系统 | 定位 | 技术栈 | 状态管理 |
|------|------|--------|----------|
| Claude Code | 开发者 CLI 工具 | TypeScript + Bun | React Ink UI + 不可变 AppState Store |
| DeepFlow | 研究自动化平台 | Python + LangGraph | 状态图管理 |
| NanoClaw | 个人消息助手 | Node.js | 无 UI，SQLite 轮询触发对话 |

三个系统都在构建 AI + 工具执行，但针对不同用户、不同场景，架构选择完全不同。
