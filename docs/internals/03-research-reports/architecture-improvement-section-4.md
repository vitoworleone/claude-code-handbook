# 第四节关键子系统拆解调研

目标文档：`08_Skill-Outputs/Agent_Runtime_架构与原理综合报告.md`

调研范围：第四节「关键子系统原理」，即 StreamingToolExecutor、Permission Gate、Context Compaction、Memory System、Agent Teams、MCP。

方法：按 `improve-codebase-architecture` skill 的语言看真实代码中的 Module、Interface、Implementation、Depth、Seam、Adapter、Leverage、Locality。当前仓库没有 `CONTEXT.md`，也没有 `docs/adr/`，所以本报告只能基于代码和现有报告，不引用项目领域词表或 ADR 决策。

验证：针对第四节相关子系统执行单测：

```text
uv run pytest tests/unit/tools/test_streaming_executor.py tests/unit/tools/test_streaming_executor_enhanced.py tests/unit/permissions tests/unit/compact tests/unit/memory tests/unit/swarm tests/unit/tools/test_team_create.py tests/unit/tools/test_team_delete.py tests/unit/tools/test_mcp_config.py -q

125 passed in 2.07s
```

## 1. 第四节到源码的对应关系

| 文档小节 | 主 Module | 关键源码 |
|---|---|---|
| 4.1 StreamingToolExecutor | 流式工具执行 Module | `cc/tools/streaming_executor.py`, `cc/tools/base.py`, `cc/tools/bash/bash_tool.py`, `cc/core/query_loop.py` |
| 4.2 Permission Gate | 权限决策 Module | `cc/permissions/gate.py`, `cc/permissions/rules.py`, `cc/tools/streaming_executor.py` |
| 4.3 Context Compaction | 上下文生存 Module | `cc/compact/compact.py`, `cc/api/token_estimation.py`, `cc/core/query_loop.py`, `cc/prompts/sections.py` |
| 4.4 Memory System | 跨会话记忆 Module | `cc/memory/session_memory.py`, `cc/memory/extractor.py`, `cc/prompts/sections.py`, `cc/main.py` |
| 4.5 Agent Teams | 团队运行 Module | `cc/swarm/*`, `cc/tools/team/*`, `cc/tools/send_message/*`, `cc/main.py` |
| 4.6 MCP | 外部工具接入 Module | `cc/mcp/config.py`, `cc/mcp/client.py`, `cc/main.py` |

## 2. 逐节拆解

### 2.1 StreamingToolExecutor

这一节描述基本准确。真实 Implementation 的核心路径是：

1. `query_loop` 在模型流式输出中收到 `ToolUseStart`。
2. `StreamingToolExecutor.add_tool()` 立即接收 `ToolUseBlock`。
3. 工具通过 `Tool.is_concurrency_safe()` 决定立即启动还是进入队列。
4. `_execute_one()` 依次经过 semaphore、PreToolUse hook、permission checker、tool execute、PostToolUse hook。
5. `get_results()` 在流结束后收集所有工具结果，并按工具到达顺序返回。

这个 Module 的 Interface 不只是 `add_tool()` 和 `get_results()`，还包括几个隐含约束：工具到达顺序决定结果顺序、非并发安全工具要求独占、hook/permission 的失败必须变成 `ToolResult(is_error=True)`，不能打断外层循环。

需要校准的点：文档里的 BashTool 白名单示例有一点陈旧。当前代码不是把 `git` 作为单词级只读命令，而是通过 `_READ_ONLY_TWO_WORD` 允许 `git status`、`git log`、`git diff` 等具体前缀。也就是说，文档表达的原则对，但示例需要改成“单词白名单 + 双词白名单”，避免读者误以为所有 `git ...` 都安全。

### 2.2 Permission Gate

文档覆盖了权限模式和只读/编辑白名单，但少讲了一个真实存在的 Module：`PermissionRules`。

真实执行顺序是：

1. `PermissionContext` 先检查 `_always_allow`。
2. 如果存在 `PermissionRules`，先应用 allow/deny 规则。
3. 规则没有命中才进入 `check_permission()` 的模式判断。
4. `ASK` 在非交互环境下 fail-fast，在交互环境下提示 `y/n/a`。

所以 Permission Gate 的 Interface 包含三层事实：session 级 mode、settings 级 rules、运行时 `_always_allow`。文档现在主要讲 mode，容易让读者低估 `settings.json` 规则对安全策略的影响。

一个值得注意的 Locality 点：`StreamingToolExecutor` 只依赖一个 `permission_checker` callable，这让工具执行 Module 不需要理解权限模式细节。这个 Seam 是好的。

### 2.3 Context Compaction

文档关于 auto-compact、reactive compact、`POST_COMPACT_KEEP_TURNS = 4`、`AUTO_COMPACT_BUFFER = 13000` 的描述与代码一致。

真实代码里上下文生存逻辑分布在四处：

- `cc/api/token_estimation.py`：粗估 tokens。
- `cc/compact/compact.py`：判断是否压缩、执行压缩、保留最近轮次。
- `cc/core/query_loop.py`：决定何时触发 auto/reactive compact，并维护连续失败计数。
- `cc/prompts/sections.py`：`SUMMARIZE_TOOL_RESULTS` 作为事前防御提示。

需要补充的关键 Interface 事实：`estimate_messages_tokens()` 明确不包含 system prompt 和工具 schema，所以它不是“完整 prompt tokens”，而是“messages 粗估”。这个差异不会推翻设计，因为 13K buffer 提供了容错，但读者需要知道这个估算口径。

### 2.4 Memory System

文档对 Memory 和 Compact 的区分、四类 memory、文件结构、coalescing 策略描述基本准确。

真实 Implementation 可拆成三段：

- `session_memory.py`：项目路径哈希、memory 文件加载、单条 memory 保存、`MEMORY.md` 索引更新。
- `extractor.py`：提取提示、模型调用、解析保存，以及 `ExtractionCoordinator` 的 coalescing。
- `main.py`：在每轮结束后用后台 task 调 `ExtractionCoordinator.request_extraction()`。

这里的深度已经比较明显：`ExtractionCoordinator` 把“同一时刻只运行一个提取、运行期间新请求只标 dirty、结束后必要时重跑”的 Implementation 藏在一个较小的 Interface 后面。文档把它和 debounce 对比讲清楚了。

但 Memory Pipeline 的整体 Seam 还不够集中。调用者需要在 `main.py`、`extractor.py`、`session_memory.py`、prompt sections 之间跳转，才能完整理解“什么时候提取、提取什么、存到哪里、下次怎么加载”。

### 2.5 Agent Teams

文档对 Leader-Worker、`CLAUDE_CODE_COORDINATOR_MODE=1`、contextvars、文件系统邮箱、工具过滤的描述基本成立。

真实 Implementation 的关键拼装点比文档更分散：

- `TeamContext` 是 leader session 的团队状态。
- `TeamCreateTool` / `TeamDeleteTool` 负责进入和退出团队上下文。
- `SendMessageTool` 依赖当前 team 和 sender 信息写文件邮箱。
- `spawn_teammate()` 创建 teammate、登记 team file、登记 task registry、启动 asyncio task。
- `InProcessTeammate.run()` 重新构建子工具 registry，过滤 `Agent`、`AskUserQuestion`、`TeamCreate`、`TeamDelete`，并注入 teammate 版 `SendMessageTool`。
- `TeammateMailbox` 是文件读写邮箱，当前设计没有文件锁。

这一节适合理解“运行时拼装”的设计味道：Team Runtime 的行为不是集中在一个文件中，而是由多个 Module 协作形成。优点是每个小 Module 容易读；代价是读者要追一整条生命周期链才知道一个 teammate 如何诞生、运行、通信、结束。

另外有一个小文档债：`main.py` 的注释里出现过 `CC_COORDINATOR=1` 说法，而真实 coordinator 检查的是 `CLAUDE_CODE_COORDINATOR_MODE`。第四节正文写的是正确变量名，但源码注释存在噪音。

### 2.6 MCP

这一节需要明显校准。

`cc/mcp/config.py` 的确能解析 `stdio`、`sse`、`http` 三种配置形态；但 `cc/mcp/client.py` 当前只实现了 `stdio`。如果 config 的 transport 不是 `stdio`，连接函数会记录 warning 并返回 `None`。

因此文档“stdio 或 Streamable HTTP”的说法在当前代码里属于目标形态，不是已落地能力。按 skill 语言说，这是一个 Interface 暗示了多个 Adapter，但 Implementation 只有一个 Adapter。`sse/http` 现在更像配置层的预留 Seam。

## 3. 架构校准结论

1. 第四节的大方向可靠：这些确实是 runtime 的关键子系统。
2. 最需要修正文档的是 MCP：当前只支持 stdio 连接，HTTP/SSE 还没有对应 Adapter。
3. Permission Gate 应补充 `PermissionRules`，否则安全模型少了一层真实 Interface。
4. Context Compaction 应说明 token 估算口径不包含 system prompt 和工具 schema。
5. Agent Teams 是“生命周期分散拼装”的典型区域，适合继续做 Depth 调研。
6. StreamingToolExecutor 的设计是第四节中最成型的深 Module：小 Interface 承载了并发、安全、hook、错误转换、结果排序等大量行为。

## 4. 深度化候选

下面只列候选，不设计新的 Interface。每个候选都需要下一轮再展开约束和取舍。

### 1. Tool Execution Policy Module

**Files**：`cc/tools/streaming_executor.py`, `cc/tools/base.py`, `cc/tools/bash/bash_tool.py`, `cc/permissions/gate.py`, `cc/hooks/*`

**Problem**：`StreamingToolExecutor` 已经很深，但它的 Interface 实际承担了很多隐含规则：并发调度、独占排队、hook 顺序、permission 顺序、错误转换、结果排序。Bash 的并发安全判断又在 `BashTool` 内部，Permission 规则在另一个 Module。理解一次工具执行，需要在多个位置拼出完整行为。

**Solution**：把“工具执行策略”作为一个更明确的 Module 来审视，集中描述调度、权限、hook、错误语义这些必须同时成立的规则。当前阶段不急着添加新 Interface，先把现有 Interface 的隐含约束写成可测试的行为清单。

**Benefits**：提升 Locality：工具执行顺序和安全语义集中可查。提升 Leverage：本地工具、MCP 工具、未来工具都能通过同一套测试表面验证。

### 2. Permission Decision Module

**Files**：`cc/permissions/gate.py`, `cc/permissions/rules.py`, `cc/tools/streaming_executor.py`, `cc/swarm/in_process_runner.py`, `cc/tools/agent/agent_tool.py`

**Problem**：`PermissionContext` 是一个好 Module，但不同运行环境仍需要手动构造 mode、interactive、rules，并把它包成 callable 交给执行器。teammate 里就重新创建了非交互 `PermissionContext`。这让“权限决策的上下文”知识分散在调用者中。

**Solution**：把“运行上下文如何得到权限决策”作为候选 Depth 点，而不是只看 `check_permission()`。下一步可研究主会话、后台 agent、teammate、非交互运行是否应该共享更深的 Permission Decision Module。

**Benefits**：提升 Locality：fail-fast、always allow、rules、mode 的组合逻辑集中维护。提升 Leverage：所有工具执行路径可以通过同一权限测试表面覆盖。

### 3. Context Survival Module

**Files**：`cc/core/query_loop.py`, `cc/compact/compact.py`, `cc/api/token_estimation.py`, `cc/prompts/sections.py`

**Problem**：上下文生存策略横跨 token 估算、主动压缩、响应式压缩、连续失败计数、摘要提示、保留窗口。`query_loop` 不只是在跑模型循环，也知道很多 compact 策略细节。

**Solution**：把“上下文能否继续生存”作为一个更深的 Module 候选，研究是否可以让 `query_loop` 只表达“在模型调用前/错误后询问生存策略”，而不是直接持有所有策略状态。

**Benefits**：提升 Locality：prompt too long、auto threshold、failure cap、keep turns 的变更集中。提升 Leverage：长对话测试可以从一个 Interface 进入，而不需要模拟整段 query loop。

### 4. Memory Pipeline Module

**Files**：`cc/memory/extractor.py`, `cc/memory/session_memory.py`, `cc/prompts/sections.py`, `cc/main.py`

**Problem**：`ExtractionCoordinator` 本身有 Depth，但完整 Memory Pipeline 仍然分散：主循环负责调度，extractor 负责提取和 coalescing，session memory 负责落盘，prompt sections 负责加载提示。读者要跨四处才能理解“记忆如何从一轮对话变成下次启动的 prompt”。

**Solution**：把“turn completed 后的 memory maintenance”作为一个候选 Module，下一步研究它应该隐藏哪些 Implementation：增量判断、coalescing、extract、save、index update、prompt load。

**Benefits**：提升 Locality：记忆写入和索引更新的 bug 更容易定位。提升 Leverage：测试可以从“给定 messages 和 cwd，运行一次 maintenance”进入，覆盖完整链路。

### 5. Team Runtime Module

**Files**：`cc/swarm/*`, `cc/tools/team/*`, `cc/tools/send_message/*`, `cc/main.py`

**Problem**：Agent Teams 的生命周期知识分散最明显。TeamContext、TeamCreate、TeamDelete、SendMessage、spawn、team file、mailbox、in-process runner、TaskRegistry 都各自合理，但理解一个 team turn 需要不断跳转。这个区域的 Interface 对读者来说接近 Implementation 复杂度，偏浅。

**Solution**：把“团队生命周期 + teammate 运行 + mailbox 轮询”作为候选 Module。下一步先画出生命周期，再决定哪些 Seam 是真的，哪些只是单 Adapter 的假 Seam。

**Benefits**：提升 Locality：团队创建、通信、清理、后台失败处理集中可推理。提升 Leverage：coordinator、leader、teammate 的测试可以围绕生命周期展开，而不是每次手动拼多个低层对象。

### 6. MCP Transport Adapter Module

**Files**：`cc/mcp/config.py`, `cc/mcp/client.py`, `cc/main.py`

**Problem**：配置层接受 `stdio/sse/http`，连接层只实现 `stdio`。这会让 Interface 给读者的承诺大于 Implementation。按“一个 Adapter = 假设性 Seam；两个 Adapter = 真实 Seam”的原则，现在的 transport Seam 还没有被实现成真实多 Adapter 结构。

**Solution**：下一步有两个方向：要么收窄 Interface 和文档，只承诺 stdio；要么补齐第二个 Adapter，让 transport Seam 变成真实 Seam。当前报告不选方向，只标记这是最清晰的架构债。

**Benefits**：提升 Locality：MCP transport 能力和失败模式集中在一个地方。提升 Leverage：未来接远程 MCP 时，测试可以明确覆盖不同 Adapter，而不是依赖配置解析的假象。

## 5. 建议的阅读顺序

如果你要继续带着代码读，我建议按这个顺序：

1. `cc/tools/streaming_executor.py`：先理解 runtime 如何边流式接收边执行。
2. `cc/permissions/gate.py` + `cc/permissions/rules.py`：理解工具执行前的安全决策。
3. `cc/core/query_loop.py` 的 Phase 1/2/3：把 compact 和 streaming executor 放回主循环。
4. `cc/memory/extractor.py` 的 `ExtractionCoordinator`：看 coalescing 如何保证最终状态被扫描。
5. `cc/swarm/in_process_runner.py` + `cc/swarm/spawn.py`：看 teammate 如何复用 query loop。
6. `cc/mcp/config.py` + `cc/mcp/client.py`：看文档能力和当前 Implementation 的差距。

你可以从候选 1-6 中选一个，我们下一轮做更深的 grilling：先确认约束，再决定哪些 Depth 值得真的落成代码或文档修正。
