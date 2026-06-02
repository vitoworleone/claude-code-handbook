# 第18章 Worktree 隔离

> "两个子代理同时修改文件会冲突吗？""我想同时处理两个功能分支，但不想来回 stash/checkout。""子代理的隔离是软件层面的还是物理层面的？"

第17章我们讲了子代理的调度、六种内置类型和权限隔离。但权限隔离只是"软件层面"的——如果两个子代理同时被允许写不同的文件，权限再清晰也会冲突。**一个 Agent 的修改会立即出现在另一个 Agent 的文件系统视图中，导致读到半成品、产生竞态条件、甚至相互覆盖。**

本章讲 **Git worktree** 如何为并行任务提供**物理层面的文件系统隔离**——每个任务有自己的工作目录、自己的分支、自己的未提交修改，互不干扰。这也是第19章四角色流水线的运行底座。

---

## 18.1 Git worktree 并行会话

### 问题：我想同时处理两个功能分支，但不想来回 `git stash` / `git checkout`

**背景**：传统 Git 工作流中，并行处理多个分支需要频繁切换。你正在 `feature-auth` 分支写代码，产品经理突然让你修一个 `main` 上的紧急 bug。你得先提交或 stash 当前修改，切到 `main`，修完再切回来。这个过程容易出错——stash 丢东西、切换时冲突、状态混乱。

**为什么是问题**：人类并行开 2~3 个分支已经手忙脚乱，AI Agent 可能同时运行 5~10 个任务。如果每个任务都需要 stash/切换，协调成本会指数增长。更关键的是，**Agent 不会像人类那样记得自己 stash 了什么**——切换冲突对 Agent 是灾难性的。

**Git worktree 的解决机制**：Git worktree 是 Git 2.5+ 引入的内置机制。它允许同一仓库有多个工作目录，每个绑定不同分支，但**共享同一个 `.git` 对象数据库**。这意味着：
- 创建 worktree 不需要复制仓库历史（`.git` 只有一份，节省磁盘）
- 每个 worktree 有独立的工作目录文件
- 切换"分支"变成"切换目录"，零 stash 风险

**结论**：Claude Code 将 worktree 作为并行会话的物理隔离底座。它不是 Claude Code 自己发明的机制，是利用 Git 已有能力的零额外依赖方案。

**与 `/branch` 的区别**：

| | `/branch` | `--worktree` |
|--|-----------|--------------|
| 隔离层级 | 会话/上下文隔离 | **文件系统隔离** |
| 工作目录 | 同一个 | 各自独立 |
| 分支切换 | `git checkout` | `cd` 到另一个目录 |
| 未提交修改 | 需要 stash 或提交 | **各自保留，互不干扰** |
| 适用场景 | 同一任务内的分支尝试 | **真正并行的独立任务** |
| Git 操作 | 切分支 | `git worktree add` |

**实际场景对比**：

假设你在 `feature-auth` 分支有未提交的修改，需要紧急修 `main` 上的 bug：

```bash
# 传统方式（/branch）
git stash                    # 保存当前修改
git checkout main            # 切分支
# 修 bug...
git checkout feature-auth    # 切回来
git stash pop                # 恢复修改（可能冲突）

# Worktree 方式（--worktree）
claude --worktree hotfix     # 在 .claude/worktrees/hotfix/ 创建新 worktree
# 在新目录里修 bug，原目录的修改完全不受影响
# 修完直接提交，无需 stash/切换
```

**怎么做**：
- 同一任务内尝试不同分支的代码 → 用 `/branch`
- 同时跑多个独立任务（各自有未提交修改）→ 用 `--worktree`
- 子代理执行写操作 → 自动使用 worktree 隔离（见 18.3）

---

## 18.2 启动隔离会话的命令

### 问题：怎么开一个 worktree 会话？

**背景**：Claude Code 封装了 Git worktree 的底层命令（`git worktree add`），提供了用户友好的 CLI 接口。但理解底层机制有助于你判断什么时候出问题。

**为什么需要封装**：原始 `git worktree add` 需要手动指定路径、分支、基于哪个 commit。Claude Code 自动处理命名、分支创建、路径选择，降低使用门槛。

**启动命令**：

```bash
# 指定名称，基于当前分支创建 worktree-<name> 分支
claude --worktree feature-auth

# 自动生成唯一名称
claude --worktree

# 从 PR #1234 的分支创建（自动拉取 PR 分支）
claude --worktree "#1234"
```

**创建细节**：

| 属性 | 值 | 说明 |
|------|-----|------|
| **创建位置** | `.claude/worktrees/<name>/` | 项目内子目录，.gitignored |
| **分支名** | `worktree-<name>` | 自动创建，基于当前分支 |
| **Git 对象** | 共享主仓库的 `.git` | 不复制历史，节省磁盘 |
| **工作目录** | 完全独立 | 文件修改互不干扰 |

**生命周期**：

```
启动：claude --worktree <name>
  ↓
Git 操作：git worktree add .claude/worktrees/<name> -b worktree-<name>
  ↓
运行：独立会话，独立文件系统视图，独立上下文窗口
  ↓
结束：exit 或 detach
  ↓
清理：自动（条件见 18.7）或手动
```

**Worktree 的 Git 本质**：

worktree 不是 Claude Code 的虚拟概念，是真实的 Git worktree。你可以直接用原生 Git 命令操作：

```bash
# 查看所有 worktree（包括 Claude Code 创建的）
git worktree list

# 你会看到类似输出：
# /path/to/project           abc1234 [main]
# /path/to/project/.claude/worktrees/feature-auth  def5678 [worktree-feature-auth]

# 直接 cd 进去查看/操作
cd .claude/worktrees/feature-auth
git status
git log --oneline -5
```

**结论**：Claude Code 的 worktree 就是标准 Git worktree，只是帮你自动创建和管理。你随时可以用原生 Git 命令查看和操作。

**怎么做**：
- 长期并行任务（同时开发两个大功能）→ 显式命名，`claude --worktree feature-X`
- 临时探索（快速验证一个想法）→ 自动生成名称，用完即删
- 基于 PR 审查 → `claude --worktree "#1234"`，从 PR 分支启动

---

## 18.3 子代理自动 worktree 隔离

### 问题：我自己用 `--worktree` 是显式的，子代理是怎么自动隔离的？

**背景**：第17章 17.3 讲了 Agent tool 的调度机制，17.4~17.5 讲了内置和自定义子代理类型。当主会话派发一个子代理去执行任务时，这个子代理运行在什么环境里？

**为什么自动隔离必要**：如果子代理默认在主仓库运行，以下问题不可避免：
- 子代理修改的文件会立即污染主会话的视图
- 多个子代理同时运行时互相看到对方的半成品
- 子代理失败后的残留修改难以清理
- 主会话在子代理运行时读取到的文件可能是"半成品状态"

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 8.2）的隔离架构定义了三种模式：

| 隔离模式 | 实现机制 | 隔离强度 | 启动开销 | 适用场景 |
|---------|---------|---------|---------|---------|
| **in-process** | 同一进程，共享文件系统 | 无 | 零 | 只读查询、轻量任务 |
| **worktree** | 临时 git worktree | **文件系统级** | 毫秒 | **需要写文件的子代理任务** |
| **remote** | 独立进程/机器 | 进程级 | 秒级 | 需要更强边界（internal-only） |

**为什么有三种而非统一**：

- **统一为 in-process**：写操作会污染主仓库，无法安全并行。Explore agent 如果只读，用 in-process 足够；但 General-purpose agent 实现功能时必须写文件，in-process 不行。
- **统一为 worktree**：只读查询也要毫秒级创建 worktree，浪费资源。Agent teams 的总 token 消耗已经是标准会话的 7 倍，如果每个只读查询都建 worktree，开销不可接受。
- **统一为 remote**：需要额外基础设施（独立机器/容器），大多数用户没有。

**自动选择逻辑**：
- `isolation` 参数未指定 → 根据**任务类型**自动推断
- **只读探索**（`Explore` agent）→ `in-process`（零开销）
- **需要写文件**（`General-purpose` 实现功能）→ `worktree`（文件系统隔离）
- **显式指定** `isolation: worktree` → 强制使用，无视任务类型

**完成且无更改时自动删除**：

临时 worktree 的自动清理基于两个条件：
1. 子代理已完成（正常结束或异常退出）
2. 工作目录与启动时一致（没有未提交的修改）

**为什么保留有更改的 worktree**：如果子代理产生了未提交修改，这些修改可能是用户需要审查的结果。自动删除会导致数据丢失。Claude Code 选择**保守策略**——宁可保留让用户手动清理，也不自动删除可能有价值的结果。

**结论**：子代理的 worktree 隔离是**自动的、按需的、保守清理的**。你通常不需要关心子代理运行在哪个 worktree 里——Claude Code 根据任务类型自动选择，并在安全时自动清理。

**怎么做**：
- 通常**无需手动管理**——Claude Code 自动选择隔离模式并自动清理
- 如果需要强制文件系统隔离 → 在 AGENT.md 中设置 `isolation: worktree`
- 如果子代理结果需要保留 → 确保它产生未提交修改，或手动复制结果到主仓库

---

## 18.4 与其他隔离方案的对比

### 问题：为什么不用 Docker？AutoGen 的上下文隔离不够吗？

**背景**：AI Agent 领域的隔离方案不止 worktree 一种。理解 Claude Code 为什么选择 worktree，需要对比其他主流方案的设计取舍。

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 8.2）提供了对比框架：

| 方案 | 隔离层级 | 外部依赖 | 启动延迟 | 调试难度 | 资源占用 | Claude Code 选择 |
|------|---------|---------|---------|---------|---------|-----------------|
| **Container-based（Docker）** | 进程+文件系统+网络 | Docker Daemon | 秒级 | 难（需进容器） | 高（容器镜像） | 未采用 |
| **Context-only（AutoGen 等）** | 仅对话历史分离 | 无 | 毫秒级 | 易 | 低 | 未采用（文件系统共享） |
| **Worktree-based** | **文件系统** | **仅需 Git** | **毫秒级** | **易（普通目录）** | **低（共享 .git）** | **采用** |

**逐项分析为什么选 worktree**：

**vs Docker**：
- Docker 提供更强的隔离（网络隔离、进程隔离、资源限制），但需要 Docker Daemon。Claude Code 的目标用户是开发者，Git 是已有前提条件，Docker 不是。
- **启动延迟是硬伤**：创建容器是秒级的，创建 worktree 是毫秒级的。Agent 任务可能只运行几十秒，如果启动就花 5 秒，用户体验不可接受。
- **调试体验差**：你需要 `docker exec` 进容器查看文件状态，worktree 就是普通目录，`cd` 进去就能看。

**vs Context-only（AutoGen 等）**：
- Context-only 只分离对话历史，**共享文件系统**。这意味着 Agent A 修改的文件会立即被 Agent B 看到。
- 对于代码生成任务，文件系统隔离是**刚需**。两个 Agent 同时写代码，如果没有文件系统隔离，必然互相干扰。

**Worktree 的局限**：

| 局限 | 说明 | 补偿机制 |
|------|------|---------|
| 无网络隔离 | 子代理仍能访问外部网络 | 权限模型控制（第17章 17.7、第20章） |
| 无进程隔离 | 子代理和主进程在同一机器 | OS 级沙箱（可选）+ 权限模型 |
| 同一文件系统 | 仍能访问机器上其他目录 | `sandbox.enabled` 配置 |
| 同一用户权限 | 子代理以同一用户身份运行 | 权限覆盖逻辑（第17章 17.7） |

**结论**：worktree 是在"隔离强度"和"部署复杂度"之间的**最优平衡点**。它用 Git 已有的能力实现了文件系统隔离，零额外依赖，启动毫秒级，调试零门槛。

**怎么做**：
- 需要**最强隔离**（不可信代码执行、安全沙箱）→ 用 Docker 或专门沙箱环境，不在 Claude Code worktree 中运行
- 需要**并行代码生成/修改** → worktree 是正确选择
- 只需要**并行查询** → in-process 足够，无需 worktree 开销

---

## 18.5 文件锁协调机制

### 问题：多个 worktree 里的 Agent 同时运行，怎么协调对共享资源的访问？

**背景**：当多个 Agent 并行工作时，协调是必要的。例如：
- 两个 Implementer Agent 不能同时修改同一文件
- Merger Agent 需要等所有 Implementer 完成后才能开始合并
- Planner Agent 分配任务时需要知道哪些 Agent 还忙

**为什么不用消息中间件**：业界标准方案是用 Redis、RabbitMQ、ZooKeeper 等分布式协调服务。但 Claude Code 选择了不同的路径。

**Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems**（Section 8.3）揭示了设计理由：

> "Agent teams use file-based locking rather than message brokers. Lock files are plain-text JSON, human-readable and debuggable without additional tools."

翻译：Agent 团队使用**基于文件的锁**而非消息中间件。锁文件是纯文本 JSON，无需额外工具即可阅读和调试。

**文件锁的实现机制**：

- **位置**：`.claude/locks/` 目录下的 JSON 文件
- **格式示例**：
  ```json
  {
    "holder": "agent-explore-abc123",
    "task": "edit src/api/auth.ts",
    "since": "2026-05-29T10:00:00Z",
    "ttl_seconds": 300
  }
  ```
- **原子性**：依赖文件系统的原子创建操作（`O_EXCL` 标志），同一时刻只有一个进程能创建同名锁文件
- **释放**：Agent 完成任务后删除锁文件，或超时后由 supervisor 清理

**为什么选文件锁**：

| 考量 | 文件锁 | 消息中间件（Redis 等） |
|------|--------|----------------------|
| **外部依赖** | **零**（文件系统已有） | 需要额外服务 |
| **部署复杂度** | **无** | 需安装、配置、维护 |
| **调试体验** | **`cat .claude/locks/*.json`** | 需连 Redis CLI |
| **持久化** | **锁文件即状态**，重启后可恢复 | 需考虑 Redis 持久化 |
| **吞吐量** | 低（毫秒级竞争） | 高（微秒级） |
| **跨机器** | 不支持 | 支持 |

**为什么吞吐量代价可接受**：AI Agent 的任务粒度是**秒级**的——一次文件编辑、一次测试运行、一次代码审查都需要数秒到数分钟。毫秒级的锁竞争延迟在这个尺度上可忽略。

消息中间件的微秒级优势对 Agent 场景没有实际价值，却带来了部署负担。Claude Code 的设计哲学是"利用已有工具，不引入外部依赖"——文件锁基于文件系统已有的原子操作，零额外依赖。

**结论**：文件锁是"足够好"的协调机制。它的吞吐量对 Agent 场景足够，调试体验优秀，部署成本为零。

**怎么做**：
- 通常**无需手动管理**——Claude Code 在 Agent 调用 `Edit`/`Write` 等工具时自动获取和释放锁
- 如果需要查看当前锁状态 → `ls .claude/locks/` 或 `cat .claude/locks/*`
- 如果 Agent 崩溃导致锁未释放 → 手动删除对应锁文件，supervisor 会在超时后自动清理

---

## 18.6 .worktreeinclude：复制 gitignored 文件到新 worktree

### 问题：我的项目依赖 `.env.local` 或构建产物，但它们是 gitignored 的，worktree 里怎么获得？

**背景**：worktree 共享 Git 对象数据库，但工作目录独立。`git worktree add` 只检出 Git 跟踪的文件，**gitignored 文件不会被复制**。

**为什么是问题**：很多项目的运行依赖 gitignored 文件：
- `.env.local`（本地环境变量）
- `config/private-key.pem`（密钥文件）
- `node_modules/`（依赖目录，通常 gitignored）

如果新 worktree 缺少这些文件，Agent 可能无法正常运行测试或构建。例如，一个 Verification agent 在 worktree 里运行测试，但 `.env.local` 里有数据库连接字符串，缺少它测试会失败。

**`.worktreeinclude` 机制**：

在项目根目录创建 `.worktreeinclude` 文件，列出需要复制到 worktree 的 gitignored 文件/目录：

```
# .worktreeinclude
.env.local
config/private-key.pem
translations/cache/
```

Claude Code 创建 worktree 时自动读取此文件并复制列出的内容。

**为什么用配置文件而非自动复制所有 gitignored 文件**：
- **安全和隐私**：不是所有 gitignored 文件都应该复制到每个 worktree。例如 `.env.production` 可能包含生产密钥，不应该出现在临时 worktree 中。
- **磁盘效率**：`node_modules` 可能很大（数百 MB），每个 worktree 复制一份浪费空间。
- **明确性**：`.worktreeinclude` 是**显式白名单**，避免隐式行为导致意外。

**与 `node_modules` 的处理**：

不要直接把 `node_modules` 放进 `.worktreeinclude`。推荐做法：
- 在 worktree 里重新安装依赖（`npm install`）
- 或使用符号链接（如果构建系统支持）
- 或把 `node_modules` 放在项目外，通过配置指向它

**结论**：`.worktreeinclude` 是 worktree 的"补给清单"——明确告诉 Claude Code 哪些 gitignored 文件需要复制到新 worktree，让 Agent 能正常运行。

**怎么做**：
- worktree 里的 Agent 需要环境变量 → 在 `.worktreeinclude` 中加 `.env.local`
- worktree 需要私有密钥 → 加对应密钥路径
- 不要加 `node_modules`——worktree 里应该重新安装或用符号链接
- 定期审查 `.worktreeinclude`——删除不再需要的条目，避免复制敏感文件

---

## 18.7 清理规则与手动管理

### 问题：worktree 会堆积吗？怎么清理？

**背景**：临时 worktree 如果无人清理，会不断堆积在 `.claude/worktrees/` 目录下，消耗磁盘空间。一个活跃的 Agent 团队可能一天创建几十个临时 worktree。

**自动清理策略**：

Claude Code 的自动清理基于两个条件：
1. **子代理已完成**：Agent 正常结束或异常退出
2. **无未提交更改**：worktree 的工作目录与启动时一致（没有新增、修改、删除的文件）

当两个条件同时满足时，worktree 被自动删除。

**为什么保留有更改的 worktree**：如果子代理产生了未提交修改，这些修改可能是用户需要审查的结果。自动删除会导致数据丢失。Claude Code 选择**保守策略**——宁可保留让用户手动清理，也不自动删除可能有价值的结果。

**手动清理命令**：

```bash
# 查看所有 worktree（包括 Git 原生的和 Claude Code 创建的）
git worktree list

# 你会看到：
# /path/to/project                           abc1234 [main]
# /path/to/project/.claude/worktrees/auth    def5678 [worktree-auth]
# /path/to/project/.claude/worktrees/api     ghi9012 [worktree-api]

# 删除特定 worktree（安全：会检查未提交更改）
git worktree remove .claude/worktrees/auth

# 强制删除（即使有未提交更改）
git worktree remove --force .claude/worktrees/auth

# 删除 Claude Code 创建的所有 worktree（谨慎！）
rm -rf .claude/worktrees/*
git worktree prune  # 清理已不存在的 worktree 记录

# 只查看 Claude Code 创建的 worktree
ls .claude/worktrees/
```

**手动删除的风险**：`rm -rf` 删除 worktree 目录后，Git 的 worktree 记录还在。你需要运行 `git worktree prune` 清理僵尸记录，否则 `git worktree list` 会显示一堆已不存在的路径。

**磁盘空间监控**：

worktree 本身不复制 `.git` 对象（共享），但工作目录的文件是独立的。对于大型项目（如包含大量资源文件的游戏项目），每个 worktree 可能占用数百 MB。

```bash
# 查看各 worktree 的磁盘占用
du -sh .claude/worktrees/*

# 查看总占用
du -sh .claude/worktrees/
```

**结论**：worktree 的清理是"自动为主、手动为辅"。自动清理处理大部分临时 worktree，手动清理处理特殊情况（保留的 worktree 需要归档后删除）。

**怎么做**：
- 定期运行 `git worktree list` 查看堆积情况
- 确认 worktree 内无未提交更改后再删除（或用 `--force` 明确承担风险）
- 如果 worktree 内有需要保留的修改 → 先提交或 `git diff` 复制到主仓库
- 删除后用 `git worktree prune` 清理僵尸记录
