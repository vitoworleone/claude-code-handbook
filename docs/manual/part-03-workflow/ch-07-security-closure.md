# 第7章 五层安全开发闭环

> 本章内容源自徐文浩（前 CTO、AI for Shopping 首席技术官）在播客《AI 炼金术》中分享的实战方法论。他的公司内部全面推 Claude Code，年后强制所有同事（包括非研发）使用。这套安全闭环是他在管理员权限下，既能放手让 AI 干活、又不炸掉生产环境的实践经验。

---

AI 写代码如果不加限制，可能写错方向、搞坏代码库，甚至搞炸生产环境。而如果你身为 CTO 或管理员，权限太高——随便让 AI 用你的身份操作，风险巨大。同时，反复跟 AI 交互、盯着它写代码也很烦，"你不能一下子让它看个几个小时的活"。

徐文浩的解法是：**搭建一个五层安全架构，让 AI 在"安全边界内拥有最大自由"**。

用他的原话说：

> "你干任何事都是安全的，因为它破坏不了线上环境。你就在这个盒子里面玩，反正你把盒子搞了，最多也就盒子完蛋了。它也不是说能碰到线上环境，就是还有一个审批。"

---

## 7.1 第一层：Dev Container 沙箱隔离

### 要解决的问题

AI 生成的代码可能包含恶意依赖、删除命令、意外的网络请求。如果直接在宿主机上跑，一次 `rm -rf` 或 `DROP TABLE` 就可能造成灾难。

但沙箱的意义远不止"防破坏"。一篇对 Agent 执行环境的全面综述（CMU/Yale/Amazon 等联合出品）指出，Agent 时代的沙箱同时服务于三个目的，而三者结合才把沙箱从"运维细节"升级为 Agent 基础设施的一等公民：

**目的 1：安全（Security）。** Agent 沙箱面临传统多租户代码执行不具备的挑战。LLM 生成的代码在规模上既不可审计也不可预测，排除了静态审查作为主要防线。Agent 在多个步骤中自主执行，操作发生时无法人工干预。提示注入攻击可以把一个善意的 Agent 变成沙箱定向攻击的载体，模糊了"可信用户意图"和"恶意输入"之间的边界。

**目的 2：可复现性（Reproducibility）。** 长程 Agent 任务和测量它们的评估基准需要将执行状态重置到已知基线。Docker 容器或 microVM 可以按需销毁和重建，而开发者的工作站做不到。当单一任务可能跨并行轨迹重放数百次时，缺乏廉价重置机制本身就是一个可扩展性瓶颈。

**目的 3：活性（Liveness）——这是 Agent 时代独有的。** 没有沙箱时，Agent 想执行的每个潜在风险操作（文件写入、包安装、出站网络调用）都必须通过显式权限弹窗向人类请求许可。在大规模下，这产生两种失效模式：用户因沮丧放弃 Agent，或条件反射式地全部批准，撤销了弹窗的安全理由。

**沙箱打破了这个死结。** 它定义了一个有界区域，在此区域内 Agent 被授权自由行动，把权限从一个"操作级问题"转变为"会话配置级问题"。综述引用 Anthropic 官方数据：**引入沙箱后，Claude Code 的权限弹窗减少了 84%，同时保持了安全性。**

综述论文用了一句精辟的话描述沙箱的双重性：

> "A sandbox is simultaneously a cage and a license."

**沙箱同时是牢笼和许可证。** 牢笼限制了 AI 能造成的破坏；许可证赋予了 AI 自由行动的权利，使得长程自主执行成为可能。

### 具体做法

徐文浩在本机和云端都开 Dev Container——可以理解为 VS Code 的"开发沙箱环境"。沙箱里预装好开发所需的全部工具，一个沙箱对应一个代码库。

> "搞坏了我也无所谓，我本机上也会开沙箱环境，我云端也会开沙箱环境。"

### 操作层面

**1. 创建 Dev Container 配置**

在项目根目录创建 `.devcontainer/devcontainer.json`：

```jsonc
{
  "name": "Claude Code Sandbox",
  "image": "mcr.microsoft.com/devcontainers/typescript-node:20",
  "features": {
    "ghcr.io/devcontainers/features/docker-in-docker:2": {}
  },
  "mounts": [
    // 只挂载项目目录，不暴露宿主机其他路径
    "source=${localWorkspaceFolder},target=/workspace,type=bind"
  ],
  "postCreateCommand": "npm install -g @anthropic-ai/claude-code",
  // 限制容器权限
  "runArgs": [
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges"
  ]
}
```

**2. 沙箱内运行 Claude Code**

```bash
# 在 Dev Container 中打开项目
code --dev-container-path .devcontainer/devcontainer.json .

# Claude Code 的所有操作都在容器内执行
# 即使 AI 执行 rm -rf /，也只影响容器，宿主机毫发无损
```

**3. 云端沙箱**

除了本地 Dev Container，云端也开沙箱环境。这样即使 AI 在远程机器上搞出问题，影响的也是隔离环境，不会波及生产服务器。

### 关键原则

- **一个沙箱对应一个代码库**，不要混用
- 沙箱里预装好开发所需的全部工具，AI 不需要额外安装
- 沙箱的网络权限要明确：允许访问包管理器镜像源，但禁止访问生产环境内网

---

## 7.2 第二层：Workspace 分支隔离

### 要解决的问题

AI 在 `main` 分支上直接改代码，一旦改错方向，回滚成本高。而且多个 AI 任务同时进行时，互相干扰。

### 具体做法

Claude Code 支持 workspace 功能——让它开一个独立分支，在分支上自动做开发。每个任务一个分支，互不干扰。

### 操作层面

**1. 启动新任务时自动切分支**

在 CLAUDE.md 或任务 prompt 中明确规则：

```markdown
# 分支规则
- 每个新功能/修复必须在新分支上开发
- 分支命名：feature/<描述> 或 fix/<描述>
- 永远不要在 main 分支上直接修改代码
```

**2. 使用 Worktree 实现物理隔离（进阶）**

Git worktree 比单纯切分支更强——每个 worktree 有独立的文件系统目录，多个任务可以真正并行：

```bash
# 创建独立 worktree
git worktree add -b feature/api-refactor ../project-api-refactor main

# 在 worktree 里启动 Claude Code
cd ../project-api-refactor
claude
```

**3. Worktree 的自动清理**

Claude Code 的子代理模式支持自动 worktree 隔离——创建临时目录和分支，任务完成后自动删除：

```
Use subagents with worktree isolation to implement the payment module.
```

任务完成 → 分支合并 → worktree 自动清理。整个过程不污染你的主工作目录。

### 为什么这层重要

分支隔离 + worktree 隔离意味着：AI 的误操作可以随时丢弃。搞错了？`git worktree remove` 然后重来。不影响主干，不影响其他并行任务。

---

## 7.3 第三层：禁止直接合并主干

### 要解决的问题

即使有分支隔离，如果 AI（或人）能把代码直接合进 `main`，那前面的防线就可能被绕过。

### 具体做法

**连 CTO 自己都不能直接合并。** 徐文浩明确说：

> "我自己也不能说直接合并。那这意味着它可以随便搞。"

所有代码必须提 Code Review，由人审批后才能合并进主干。这意味着 Claude Code 也没有办法直接改线上代码。

### 操作层面

**1. GitHub 分支保护规则**

在 GitHub 仓库设置 `Settings → Branches → Add classic branch protection rule`：

| 规则 | 配置 |
|------|------|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ 勾选 |
| Require approvals | ✅ 至少 1 人审批 |
| Dismiss stale pull request approvals | ✅ 有新提交时旧审批失效 |
| Require review from Code Owners | ✅ 关键路径必须 CODEOWNERS 审批 |
| Require status checks to pass before merging | ✅ 测试/CI 必须通过 |
| Require conversation resolution | ✅ 所有评论必须解决 |
| Do not allow bypassing the above settings | ✅ **包括管理员** |

**2. GitLab 等效配置**

在 `Settings → Repository → Protected branches`：

```yaml
# .gitlab-ci.yml 中的合并前检查
merge_requests:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  before_merge:
    - approvals_required: 2
    - status_checks_passed: true
```

**3. 给 Claude Code 的明确指令**

在 CLAUDE.md 中写死：

```markdown
# 合并规则（不可违反）
- 永远不要直接 push 到 main 分支
- 永远不要执行 git push --force
- 永远不要绕过分支保护规则
- 代码提交后，告诉我 PR 链接，由我审批合并
```

### 为什么管理员也要被限制

徐文浩的权限是管理员级别，如果 AI 用他的身份操作，理论上可以绕过所有保护。但通过**在平台上设置连管理员都不可绕过的规则**，即使 AI 以 CTO 身份操作，也无法直接改线上代码。

这层的关键不是"信任 AI"，而是"不信任任何人"——规则高于权限。

---

## 7.4 第四层：提交自动触发测试

### 要解决的问题

代码能通过 PR 审批不代表逻辑正确。人的审查可能遗漏边界情况，AI 生成的代码尤其容易出现"看起来对但实际有 bug"的情况。

### 具体做法

所有提交自动触发自动化测试。徐文浩的说法很直接：

> "第一个是你所有的提交都会触发自动化的测试。"

### 操作层面

**1. GitHub Actions 自动测试配置**

```yaml
# .github/workflows/test-on-push.yml
name: Run Tests on Push

on:
  push:
    branches:
      - '**'        # 所有分支
  pull_request:
    branches:
      - main

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Type check
        run: npx tsc --noEmit

      - name: Lint
        run: npx eslint .

      - name: Unit tests
        run: npm test -- --coverage

      - name: Integration tests
        run: npm run test:integration

      - name: Build check
        run: npm run build
```

**2. 在 Claude Code 中跑测试再提交**

好的习惯是让 AI 在提交前**本地先跑通测试**：

```markdown
# 提交前检查清单（在 CLAUDE.md 中）
- 本地运行 npm test 并确保全部通过
- 本地运行 npm run typecheck 无错误
- 如果有新功能，确保写了对应的测试
- 不跳过任何测试
```

**3. 测试失败的处理**

CI 测试失败 → PR 自动标记为 "changes requested" → AI（或你）必须修复后才能重新请求审查。这个循环由 CI 强制执行，不需要人工干预。

### 为什么测试要在"提交后"而不是"提交前"

徐文浩把测试放在第四层而非第一层，是有意为之：
- 前三层保证"搞不坏线上"
- 第四层保证"搞错了能被发现"
- 如果 AI 本地跑不过测试它自己会发现并修复
- 即使 AI 没跑测试就提交了，CI 也会拦住

这个设计遵循 **defense in depth（纵深防御）** 原则：不依赖任何单一防线。

---

## 7.5 第五层：提交自动触发 AI Code Review

### 要解决的问题

人的审查有盲区——精力有限、速度有限、可能漏掉安全问题。需要第二双"AI 眼睛"在所有提交上做系统性的安全检查。

### 具体做法

所有提交自动触发 AI 做代码审核，AI 审核完给出结论：approve 还是 reject。

> "它会最后觉得你可以 approve 还是不能 approve。不能 approve 的话，你就得自己去看是为什么去修复。"

### 操作层面

**1. GitHub Actions + Claude API 自动审查**

```yaml
# .github/workflows/ai-review.yml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  ai-review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Get diff
        id: diff
        run: |
          git diff origin/${{ github.base_ref }}...HEAD > diff.txt
          echo "diff_size=$(wc -l < diff.txt)" >> $GITHUB_OUTPUT

      - name: AI Review
        if: steps.diff.outputs.diff_size != '0'
        uses: anthropics/claude-code-action@v1
        with:
          prompt: |
            Review the following PR diff for:
            1. **Security issues**: SQL injection, XSS, hardcoded secrets, unsafe shell commands
            2. **Logic errors**: off-by-one, null/undefined access, race conditions
            3. **Performance issues**: N+1 queries, memory leaks, unnecessary re-renders
            4. **Breaking changes**: API contract changes, database schema changes

            Output your review in this format:
            - **APPROVE** if no issues found
            - **REQUEST_CHANGES** with specific file:line references if issues found

            Diff:
            $(cat diff.txt)
```

**2. 多模型分层审查（进阶）**

可以配置多个 AI Reviewer，各司其职：

| Reviewer | 模型 | 关注点 |
|----------|------|--------|
| Security Reviewer | Claude Opus | 安全漏洞、敏感信息、权限问题 |
| Logic Reviewer | Claude Sonnet | 逻辑错误、边界情况、类型安全 |
| Style Reviewer | ESLint/Prettier | 代码风格（确定性规则用工具不用 AI） |

**3. AI Review 与人工 Review 的分工**

| 人工 Review 关注 | AI Review 关注 |
|-----------------|---------------|
| 业务逻辑是否正确 | 安全漏洞模式 |
| 架构设计是否合理 | 性能陷阱 |
| 是否满足产品需求 | 边界情况覆盖 |
| 代码可维护性 | 依赖是否安全 |
| 命名是否表达意图 | 类型是否正确 |

### 实际效果

徐文浩的经验是：AI Reviewer 不是橡皮图章，它真的会 reject：

> "还是会偶尔遇到一些情况它不 approve 的，它会觉得你写的有问题。"

而且他不让 AI Reviewer 直接修复，而是把结果给你看——"不 approve，你就得自己看为什么，去修复。"因为如果 AI 审完 AI 自己修，可能会在修复中引入新问题，滚雪球越滚越大。

---

## 7.6 第六层：AI 写 + AI 审互掐闭环

> 严格来说这是第五层的延伸，但其重要性足以单独成节。它是徐文浩方法论中最核心的创新。

### 要解决的问题

同一个 AI 在写完代码后立刻审查自己的代码，存在本质缺陷——它和写代码时共享同一个上下文，**带着同样的盲区、同样的偏见、同样的"作者思维"**。这就像让作家给自己的文章当编辑，效果打折扣。

徐文浩一针见血：

> "审核的跟写的它不是同一个 context。"

### 为什么"同一个 context"不行

这跟 Matt Pocock（见第6章）提出的 **Smart Zone vs Dumb Zone** 理论直接相关：

- AI 写完一个复杂功能后，上下文窗口已经塞满了实现细节、来回对话、错误修复过程
- 此时上下文接近或超过约 100K token 的 Smart Zone 临界点
- 用这个已经"累了"的上下文去审查代码，审查质量必然下降
- 更致命的是：**它带着"我是作者"的偏见**——它知道每个决策背后的意图，所以倾向于认为自己的代码是对的

解法很简单但很有效：**审查用全新的上下文**。

### 具体流程

```
┌─────────────────────────────────────────────────────┐
│                    Writer Agent                       │
│  (Sonnet, 有上下文, 加载 Skills)                      │
│                                                       │
│  1. 理解需求 → 设计方案 → 写代码                      │
│  2. 本地跑测试 → 确认通过                             │
│  3. git commit → git push → 创建 PR                  │
└────────────┬────────────────────────────────────────┘
             │ PR 创建事件
             ▼
┌─────────────────────────────────────────────────────┐
│                   Reviewer Agent                      │
│  (Opus, 全新上下文, 无偏见)                           │
│                                                       │
│  4. 被 PR 事件自动触发                                │
│  5. 在独立 worktree 中 checkout PR 分支               │
│  6. 阅读 diff → 逐文件审查 → 输出审查意见             │
│  7. 给出结论：✅ APPROVE 或 ❌ REQUEST_CHANGES       │
└────────────┬────────────────────────────────────────┘
             │ 如果 reject
             ▼
┌─────────────────────────────────────────────────────┐
│              自动通知 Writer Agent                    │
│                                                       │
│  8. Writer 收到审查意见                               │
│  9. 修复 Reviewer 指出的问题                          │
│  10. 重新提交 → 再度触发 Reviewer                    │
│  11. 循环直到 Reviewer 给出 APPROVE                  │
└─────────────────────────────────────────────────────┘
```

### 操作层面

**1. 配置 Writer Agent**

Writer 用更快的模型（Sonnet），加载业务相关的 Skills：

```markdown
# CLAUDE.md (Writer 所在项目)
你是 Writer Agent。你的职责是：
- 实现功能需求
- 编写测试并确保全部通过
- 提交代码并创建 PR

规则：
- 永远不要审查自己的代码
- 提交后等待 Reviewer 的反馈
- 收到反馈后逐条修复
```

**2. 配置 Reviewer Agent**

Reviewer 用更聪明的模型（Opus），在**独立 worktree** 中运行：

```bash
# Reviewer 在独立目录中启动
git worktree add -b review/pr-42 ../review-sandbox main
cd ../review-sandbox
gh pr checkout 42

# 在新会话中审查（关键：全新上下文）
claude -p "
Review PR #42. Check for:
1. Security vulnerabilities (OWASP Top 10)
2. Logic errors and edge cases
3. Test coverage adequacy
4. Architecture consistency with the rest of the codebase

Output format:
- APPROVE: <reason> if safe to merge
- REQUEST_CHANGES: <file:line> <issue> <suggested fix> if problems found
"
```

**3. 自动化触发（Webhook）**

用 GitHub Webhook + CI 实现自动触发：

```yaml
# .github/workflows/reviewer-trigger.yml
name: Trigger AI Reviewer

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  trigger-reviewer:
    runs-on: ubuntu-latest
    steps:
      - name: Run Reviewer Agent
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          # 创建独立 worktree
          git worktree add /tmp/review-${{ github.event.pull_request.number }} main
          cd /tmp/review-${{ github.event.pull_request.number }}
          gh pr checkout ${{ github.event.pull_request.number }}

          # 用 Opus 模型审查（全新上下文）
          claude --model opus -p "$(cat <<'REVIEW_PROMPT'
          Review this PR for security issues, logic errors, and test coverage.
          Provide specific file:line references for any issues found.
          Conclude with APPROVE or REQUEST_CHANGES.
          REVIEW_PROMPT
          )"

          # 清理
          cd /tmp && git worktree remove review-${{ github.event.pull_request.number }} --force
```

### 追问：Reviewer 说测试不对怎么办

播客中任鑫追问了一个关键问题：

> **任鑫**："那如果那个测试的说这个测得不对，那它又会自动调用那个人，它去 debug 去改吗？"

> **徐文浩**："会。"

也就是说，这个闭环不仅是"审查 + 修 bug"，而是**审查 + 修 bug + 修测试** 的完整循环。Reviewer 不仅检查代码，还会发现测试本身的问题（覆盖不全、断言不准确、未覆盖边界情况）。

### 为什么这个闭环如此重要

徐文浩自己的反思：

> "就是要把它这个闭环给打通，免得它几个小时再滚雪球乱改。"

如果没有闭环机制：
- AI 写完代码，你审一遍，发现问题 → AI 改 → AI 可能在改的时候引入新问题 → 你又审一遍 → 几轮下来几小时过去了
- 更糟的是，如果你没发现就合并了，问题留到了线上

有了闭环：
- AI 写 → AI 审 → AI 改 → AI 再审，循环直到通过
- 人的角色变成"监控循环是否正常运转"，而不是"逐行审查代码"
- "它自己在那边玩，可能就搞得乱七八糟去了，你还会让它自己做 Code Review，以及让它写测试用例，然后再相当于自己做测试。"

### 效果：不是橡皮图章

> "还是会偶尔遇到一些情况它不 approve 的，它会觉得你写的有问题。"

Reviewer 确实会 reject——这证明审查机制确实在工作，不是走过场。关键在于两个 Agent 使用**不同的上下文**和**不同的模型**：

| | Writer | Reviewer |
|------|--------|----------|
| 模型 | Sonnet（快、便宜） | Opus（更聪明） |
| 上下文 | 有完整业务知识 | 全新、无偏见 |
| 职责 | 实现功能 | 找问题 |
| 态度 | "这个能工作" | "这个有 bug 吗" |

---

## 7.7 实战教训：补测试时发现 Bug 怎么办

这是徐文浩分享的一个具体案例，体现了"AI 写代码时人的工程判断"仍然不可替代。

### 发生了什么

他让 AI 去补自动化测试。测试补完后，AI 写的测试捕捉到了原来代码里的一个 bug。此时 AI 面临两个选择：

- **做法 A（保守）**：把 bug 测试先注释掉，标记"这里有 bug todo"，继续补剩下测试。补完、提交、合并完成之后，再单独开分支专门修这个 bug
- **做法 B（激进）**：在补测试的过程中顺手把这个 bug 也修了

### 徐文浩选了 A

原因是他自己的工程原则：

> "我每一个提交应该只解决一个问题。我就是补测试的，补测试的过程中我不应该去修 bug，因为鬼知道我修了 bug 之后还会不会有其他问题，我脑子也转不过来。"

### 操作细节

1. 先补测试，测试补完，代码没碰，所有原测试通过
2. 确认无误后提交
3. 同时单独记一个 todo："这里有个 bug 需要修"
4. 再另开一个独立分支，专门修这个 bug 以及补这个 bug 的测试
5. 人的注意力更多地放在这个 bug 分支的审核上——因为这个改动是修复逻辑，风险更高

### 为什么这个例子重要

因为 **AI 不一定按你的工程风格来**：

> "模型会选哪个，可能也有一个随机性。它不一定它的第一时间的想法跟你一致，它第一时间的想法可能跟你是有差别的。"

这就是为什么人需要跟 AI 互动、需要把自己的工程偏好明确告诉它。安全闭环的"规则"不是只有技术规则，还包括你作为工程师的原则和风格。

> "只要你给它的指令相对清楚和清晰，以及你对这件事情，你自己要掌握的知识，能够至少它给你的整个开发计划你看明白。我觉得它无论从设计还是开发上的能力是不错的，但是它不一定它的第一时间的想法跟你一致。"

---

## 7.8 设计哲学：安全边界内最大自由

这五层（加上互掐闭环是六层）不是为了让 AI 束手束脚，恰恰相反——是为了让它**更自由**。

### 纵深防御，而非单点防御

| 如果只有这一层 | 可能失败的原因 |
|-------------|-------------|
| 只有沙箱 | AI 写出了有 bug 的代码，沙箱防不了逻辑错误 |
| 只有分支隔离 | AI 可能绕过分支直接操作 main |
| 只有禁止合并 | AI 以管理员身份可以解禁 |
| 只有测试 | 测试覆盖率不够，漏了关键路径 |
| 只有 AI Review | 单一模型审查自己的代码 = 橡皮图章 |

**五层一起上**：任何一层被突破，后面还有一层。这就是徐文浩的纵深防御设计。

### 纵深防御的隐藏前提：各层必须独立失效

论文指出，Claude Code 的纵深防御架构建立在一个关键假设之上：**如果一层安全机制失效，其他层会捕捉违规行为。** 但此假设并非在所有情况下都成立——各安全层共享共同的性能和成本约束。

论文引用安全研究机构 Adversa.ai (2026) 发现的一个真实漏洞作为证据：

> **命令包含超过 50 个子命令时，per-subcommand 的 deny-rule 检查被跳过，回退到单一通用审批提示。**

原因是逐子命令解析导致了 UI 冻结。为了性能，安全检查被降级——所有层同时受到影响。论文的原话是：

> "This example demonstrates that defense-in-depth can degrade when its layers share failure modes, a structural tension between safety and performance."

翻译：**当纵深防御的各层共享失效模式时，它就会退化。这是安全与性能之间的结构性矛盾。**

这揭示了一个更根本的问题：任何使用 LLM 本身做安全评估的 Agent 系统都面临此矛盾。auto-mode 分类器是独立的 LLM 调用，有直接的 token 成本。bashSecurity.ts 执行顺序 AST 检查，有解析延迟。deny-first 规则评估操作在命令结构上。当性能压力推动削减这些成本时，各层可能同时退化。

**对你的意义**：纵深防御不是银弹。它的可靠性取决于各层是否共享失效模式。在实践中，这意味着：
- 不要把权限规则写得过于复杂（可能触发解析超时）
- 不要完全依赖 auto-mode 分类器（它是一个 ML 模型，有自己的错误率）
- 当任务涉及非常复杂的 shell 命令时（如大型批量操作），考虑临时提升权限到需要人工确认的模式

### 信任但要验证，验证了要放手

徐文浩的另一个关键心态转变在第8章会详述，但这里必须提一句——他把这套安全架构搭好后，就**敢于放手了**：

> "你干任何事都是安全的，因为它破坏不了线上环境。"

他甚至用一个"滑雪比赛"隐喻（详见第8章）来表达：如果 AI 写的代码线上从来没出过 bug，反而说明太保守了。在安全边界内，要敢于让 AI 去赌——追求产出速度，接受一定比例的可修复的 bug。

---

## 7.9 渐进落地路线

你不需要一次性把所有五层都搭好。按风险从高到低逐步建设：

| 阶段 | 做什么 | 耗时 | 收益 |
|------|--------|------|------|
| **立刻** | CLAUDE.md 中写死"不要直接 push main""不要 force push" | 2 分钟 | 防止最蠢的事故 |
| **本周** | GitHub 分支保护规则 + 必须 PR 审批 | 30 分钟 | 防止代码绕过审查上线 |
| **本周** | 搭 CI 自动测试（test + lint + typecheck） | 1-2 小时 | 防止坏代码合并 |
| **本月** | Dev Container 沙箱配置 | 半天 | 物理隔离，随便搞 |
| **本月** | AI Code Review CI pipeline | 半天 | 自动发现人的审查盲区 |
| **下月** | Writer/Reviewer 双 Agent 闭环 | 1-2 天 | 全自动质量保障 |

### 最小可行安全配置（今天就做）

如果你只有 30 分钟，做这三件事：

1. **CLAUDE.md 加三行**：
   ```markdown
   - 永远不要 push 到 main，永远不要 force push
   - 永远不要使用 --no-verify 跳过 hooks
   - 提交后提醒我创建 PR，不可自行合并
   ```

2. **GitHub 仓库设置**：Settings → Branches → 保护 main 分支 → 要求 PR + 审批

3. **`.github/workflows/test.yml`**：一个最简单的 test + lint pipeline

这三件事做完，AI 就基本不可能把坏代码弄上线了。剩下的层可以逐步加。
