# Sandbox 技术实现细节与运行机制

[返回总目录](../README.md)

Claude Code 面临着在本地（宿主机）执行生成代码、自动化命令的强需求。为了防止大模型生成的代码（甚至是不受信任的 MCP 服务器的远程代码）对宿主机带来灾难性的破坏（如删除文件、外发敏感数据），系统引入了严密的**沙盒（Sandbox）隔离机制**。

核心实现位于 [`src/utils/sandbox/sandbox-adapter.ts`](../src/utils/sandbox/sandbox-adapter.ts)，是对 `@anthropic-ai/sandbox-runtime` 库的平台化适配层。以下是详细的技术实现说明：

## 1. 跨平台适配与底层依赖

- **底层引擎选择：**
  沙盒环境并非由单一库生造，而是借助了各操作系统的系统级隔离工具。
  - **Linux / WSL2+：** 底层高度依赖 `bubblewrap` (bwrap) 以及 `socat` 等内核级 Namespace 隔离工具。
  - **macOS：** 内部会基于原生架构实现调用。
- **环境检测：**
  系统在启动时会通过 [`isSupportedPlatform`](../src/utils/sandbox/sandbox-adapter.ts) 函数校验当前系统平台。由于拦截级别的限制，诸如普通 WSL1 以及未安装特定依赖项的 Linux 将无法运行沙盒。为了安全兜底，如若 `sandbox.failIfUnavailable` 为 true，遇到不兼容情况时系统会直接阻断启动。

## 2. 权限策略模型与配置映射

沙盒并非一刀切的隔离，而是**白名单驱动的细粒度模型**。用户可以通过 `permissions.allow` 和 `permissions.deny` 对 `FileEditTool`、`FileReadTool` 和 `WebFetchTool` 的行为能力进行设定，这部分将被 [`convertToSandboxRuntimeConfig`](../src/utils/sandbox/sandbox-adapter.ts) 函数自动转换并映射入底层运行时隔离配置：

- **文件系统管控（Filesystem Mounting）：**
   在转换阶段，所有通过规则指定的路径将转换为具体的挂载策略（`allowWrite`, `denyWrite`, `denyRead`, `allowRead`）。
   - **内置保护：** 沙盒底层会自动保护 `~/.claude/settings.json`、当前目录及 Original CWD 的设置文件、甚至是 `.claude/skills` 等，不受到命令篡改（这防御了所谓的"沙盒逃逸"）。
   - **特殊处理点：** 对于 `//path` (绝对路径) 和 `/path` (Settings 相对路径) 以及 Git Worktree 的 `index.lock` 特例进行动态推导和打通，使得隔离不会破坏正常的版本控制。

- **网络管控（Network Rules）：**
   利用 `WebFetchTool` 的 `domain:` 匹配记录抽取出 `allowedDomains` 和 `deniedDomains`。运行时的外发请求如果不在此范围，或者触碰到了被管理员锁死只能使用"受管域名（`allowManagedDomainsOnly`）"，系统直接拦截底层的 Socket 通信。

## 3. 防御型漏洞拦截（如 Git 逃逸）

由于 Claude 自带一系列脱离沙盒操作的 Host 层面动作（比如 `bash` 在沙盒内，但原生的 `git log` 可能在沙盒外），这引发了复杂的逃逸攻击途径。

**经典的 Git 篡改逃逸防护：**
若恶意命令在沙盒内创建了一套完整的 `HEAD`/`objects/`/`refs/` 将当前目录标记为 Bare Repo（并在内部注入带有 `core.fsmonitor` 的钩子）。那么当 Claude 下一步在宿主机无沙盒地执行原生 Git 时就会踩入恶意命令陷阱。

[`scrubBareGitRepoFiles`](../src/utils/sandbox/sandbox-adapter.ts) 函数专门设计为在命令执行完毕后强制扫描并清空在沙盒内被植入的 Git 裸库文件（`HEAD`、`objects/`、`refs/` 等），从源头封堵通过多阶段欺骗手段实现宿主持久化控制的可能性。

## 4. 隔离域内的命令委派 (Invocation)

一旦参数拼装完毕，[`wrapWithSandbox`](../src/utils/sandbox/sandbox-adapter.ts) 函数会将宿主机的执行命令完全放入新生成的隔离环境执行：

```typescript
BaseSandboxManager.wrapWithSandbox(
    command,
    binShell,
    customConfig,
    abortSignal,
)
```

无论其使用 bash 还是其他 shell，这一执行体已经完全无法穿越 namespace / 文件映射屏障读写被标记的系统核心领域。

而且由于系统还通过 [`settingsChangeDetector`](../src/utils/sandbox/sandbox-adapter.ts) 监听了配置文件热更动作，如果在宿主机改动了配置文件，沙盒的内存配置也会实时调用 `refreshConfig()` 同步更新，确保随时与用户的收缩与放宽配置同频。

## 总结

Claude Code 不是盲目通过 Docker 去做重型隔离，而是在宿主层面上借助轻量级的 `Sandbox Runtime` 进行了极高手术刀精度的 Namespace 文件挂载与网络阻断。它与 Claude Tool Permission（应用层逻辑阻断，核心实现于 [`src/utils/permissions/permissionSetup.ts`](../src/utils/permissions/permissionSetup.ts)）构成了一套双重锁，确保哪怕大模型在执行极其恶意的脚本，也不会损耗用户的关键生产环境。
