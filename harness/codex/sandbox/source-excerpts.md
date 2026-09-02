# Sandbox 关键源码摘录

基线：OpenAI `codex` 公开仓库提交 `633ab19`。

以下片段用于建立阅读索引，不参与编译。真实行为以链接的固定提交源码为准。

## 1. 用户配置层：`SandboxMode`

来源：[`protocol/src/config_types.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/config_types.rs)

```rust
pub enum SandboxMode {
    #[serde(rename = "read-only")]
    #[default]
    ReadOnly,

    #[serde(rename = "workspace-write")]
    WorkspaceWrite,

    #[serde(rename = "danger-full-access")]
    DangerFullAccess,
}
```

这是面向配置和 UI 的传统预设，不是 macOS/Linux/Windows 的具体沙箱实现。

## 2. 从模式转换成有效权限

来源：[`config/src/config_toml.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/config/src/config_toml.rs)

```rust
let permission_profile = match effective_sandbox_mode {
    SandboxMode::ReadOnly => PermissionProfile::read_only(),
    SandboxMode::WorkspaceWrite => match self.sandbox_workspace_write.as_ref() {
        Some(SandboxWorkspaceWrite {
            writable_roots,
            network_access,
            exclude_tmpdir_env_var,
            exclude_slash_tmp,
        }) => {
            let network_policy = if *network_access {
                NetworkSandboxPolicy::Enabled
            } else {
                NetworkSandboxPolicy::Restricted
            };
            PermissionProfile::workspace_write_with(
                writable_roots,
                network_policy,
                *exclude_tmpdir_env_var,
                *exclude_slash_tmp,
            )
        }
        None => PermissionProfile::workspace_write(),
    },
    SandboxMode::DangerFullAccess => PermissionProfile::Disabled,
};
```

这里能直接看到：Full Access 的内部表达是 `PermissionProfile::Disabled`，即不由 Codex 建立外层沙箱。

## 3. Runtime 规范权限：`PermissionProfile`

来源：[`protocol/src/models.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/models.rs)

```rust
pub enum PermissionProfile {
    Managed {
        file_system: ManagedFileSystemPermissions,
        network: NetworkSandboxPolicy,
    },
    Disabled,
    External { network: NetworkSandboxPolicy },
}
```

```rust
pub fn read_only() -> Self {
    let file_system = FileSystemSandboxPolicy::read_only();
    Self::Managed {
        file_system: ManagedFileSystemPermissions::from_sandbox_policy(&file_system),
        network: NetworkSandboxPolicy::Restricted,
    }
}
```

`Managed`、`Disabled`、`External` 描述谁负责强制实施权限；文件和网络字段描述实际允许的能力。

## 4. 单次命令的权限请求

来源：[`protocol/src/models.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/models.rs)

```rust
pub enum SandboxPermissions {
    UseDefault,
    RequireEscalated,
    WithAdditionalPermissions,
}
```

- `UseDefault`：沿用本轮权限；
- `RequireEscalated`：请求无沙箱执行；
- `WithAdditionalPermissions`：仍留在沙箱内，只为本次命令扩大特定权限。

这说明“授权”不必总是直接关闭整个沙箱。新路径可以只增加本次操作需要的最小权限。

## 5. 文件与网络权限的基本类型

来源：[`protocol/src/permissions.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/permissions.rs)

```rust
pub enum NetworkSandboxPolicy {
    Restricted,
    Enabled,
}
```

```rust
pub enum FileSystemAccessMode {
    Read,
    Write,
    Deny,
}
```

```rust
pub enum FileSystemSandboxKind {
    Restricted,
    Unrestricted,
    ExternalSandbox,
}
```

路径权限与整个文件系统策略是两层对象：一个受限策略可以包含多条 `Read`、`Write`、`Deny` 路径规则。

## 6. 内置 Read Only

来源：[`protocol/src/permissions.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/permissions.rs)

```rust
fn read_only_file_system_entries() -> Vec<FileSystemSandboxEntry> {
    vec![FileSystemSandboxEntry::new(
        FileSystemPath::Special {
            value: FileSystemSpecialPath::Root,
        },
        FileSystemAccessMode::Read,
    )]
}

pub fn read_only() -> Self {
    Self::restricted(read_only_file_system_entries())
}
```

所以内置 Read Only 的文件语义是根路径可读，但没有普通写入授权。Approval 是否弹出由另一套策略决定。

## 7. 内置 Workspace Write 与受保护子路径

来源：[`protocol/src/permissions.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/permissions.rs)

```rust
pub fn workspace_write(
    writable_roots: &[AbsolutePathBuf],
    exclude_tmpdir_env_var: bool,
    exclude_slash_tmp: bool,
) -> Self {
    let mut entries = vec![FileSystemSandboxEntry::new(
        FileSystemPath::Special {
            value: FileSystemSpecialPath::Root,
        },
        FileSystemAccessMode::Read,
    )];

    entries.push(FileSystemSandboxEntry::new(
        FileSystemPath::Special {
            value: FileSystemSpecialPath::project_roots(None),
        },
        FileSystemAccessMode::Write,
    ));

    // 省略临时目录和自定义 writable_roots 的追加。

    append_default_read_only_project_root_subpath_if_no_explicit_rule(
        &mut entries,
        ".git",
    );
    append_default_read_only_project_root_subpath_if_no_explicit_rule(
        &mut entries,
        ".agents",
    );
    append_default_read_only_project_root_subpath_if_no_explicit_rule(
        &mut entries,
        ".codex",
    );

    FileSystemSandboxPolicy::restricted(entries)
}
```

这里体现“父目录 Write + 更具体子目录 Read”的覆盖关系。

## 8. Approval Policy

来源：[`protocol/src/protocol.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/protocol/src/protocol.rs)

```rust
pub enum AskForApproval {
    UnlessTrusted,
    OnRequest,
    Granular(GranularApprovalConfig),
    Never,
}
```

```rust
pub struct GranularApprovalConfig {
    pub sandbox_approval: bool,
    pub rules: bool,
    pub skill_approval: bool,
    pub request_permissions: bool,
    pub mcp_elicitations: bool,
}
```

Approval Policy 与 Sandbox Policy 是独立维度。`Never` 表示从不向用户申请权限，不表示自动拥有完整权限；在 Read Only + Never 中，越界会直接失败。

## 9. Tool Orchestrator：先审批，再选择沙箱

来源：[`core/src/tools/orchestrator.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/orchestrator.rs)

```rust
let permission_profile = environment.permission_profile();
let permissions = if executor_managed_process_sandbox {
    permission_profile.clone()
} else {
    environment.permission_profile_with_workspace_roots()
};
let file_system_sandbox_policy = permissions.file_system_sandbox_policy();
let requirement = tool.exec_approval_requirement(req).unwrap_or_else(|| {
    default_exec_approval_requirement(approval_policy, &file_system_sandbox_policy)
});
```

之后根据工具请求的单次权限、网络要求和当前策略选择首次尝试：

```rust
let sandbox_requested = match sandbox_override {
    SandboxOverride::BypassSandboxFirstAttempt => false,
    SandboxOverride::NoOverride => self.sandbox.should_sandbox(
        &permissions,
        sandbox_preference,
        managed_network_active,
    ),
};

let initial_sandbox = if sandbox_requested && !executor_managed_process_sandbox {
    self.sandbox.select_initial(
        &permissions,
        sandbox_preference,
        sandbox_config.windows_sandbox_level,
        managed_network_active,
    )
} else {
    SandboxType::None
};
```

## 10. 被拒绝后的升级路径

来源：[`core/src/tools/orchestrator.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/orchestrator.rs)

```rust
let CodexErrorDetails::Sandbox(SandboxErr::Denied {
    output,
    network_policy_decision,
}) = err.details()
else {
    // 不是明确的 Sandbox Denied，按普通 Tool Error 返回。
};
```

只有明确识别为沙箱拒绝后，Orchestrator 才继续判断：

- 工具是否允许失败后升级；
- Approval Policy 是否允许发起这类申请；
- 是文件系统拒绝还是受管理网络拒绝；
- 能否在不丢失 deny-read 规则的前提下绕过沙箱；
- 之前是否已经批准过；
- 是否需要再次交给用户或 Guardian/Auto-review。

因此不存在一个简单的“失败后一律无沙箱重试”。

## 11. 平台实现选择

来源：[`sandboxing/src/manager.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/sandboxing/src/manager.rs)

```rust
pub enum SandboxType {
    None,
    MacosSeatbelt,
    LinuxSeccomp,
    WindowsRestrictedToken,
}
```

```rust
pub fn get_platform_sandbox(windows_sandbox_enabled: bool) -> Option<SandboxType> {
    if cfg!(target_os = "macos") {
        Some(SandboxType::MacosSeatbelt)
    } else if cfg!(target_os = "linux") {
        Some(SandboxType::LinuxSeccomp)
    } else if cfg!(target_os = "windows") {
        if windows_sandbox_enabled {
            Some(SandboxType::WindowsRestrictedToken)
        } else {
            None
        }
    } else {
        None
    }
}
```

## 12. 什么时候确实需要平台沙箱

来源：[`sandboxing/src/policy_transforms.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/sandboxing/src/policy_transforms.rs)

```rust
pub fn should_require_platform_sandbox(
    file_system_policy: &FileSystemSandboxPolicy,
    network_policy: NetworkSandboxPolicy,
    has_managed_network_requirements: bool,
) -> bool {
    if has_managed_network_requirements {
        return true;
    }

    if !network_policy.is_enabled() {
        return !matches!(
            file_system_policy.kind,
            FileSystemSandboxKind::ExternalSandbox
        );
    }

    match file_system_policy.kind {
        FileSystemSandboxKind::Restricted => {
            !file_system_policy.has_full_disk_write_access()
        }
        FileSystemSandboxKind::Unrestricted
        | FileSystemSandboxKind::ExternalSandbox => false,
    }
}
```

即使文件系统完全可写，只要网络仍需隔离，也可能必须启动平台沙箱。

## 13. SandboxManager 如何转换命令

来源：[`sandboxing/src/manager.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/sandboxing/src/manager.rs)

```rust
let base_effective_permission_profile =
    effective_permission_profile(permissions, additional_permissions.as_ref());

let (argv, arg0_override, pending_sandboxed_request) = match sandbox {
    SandboxType::None => (os_argv_to_strings(argv), None, None),
    SandboxType::MacosSeatbelt => {
        // 根据有效文件和网络权限生成 Seatbelt policy，
        // 再用 /usr/bin/sandbox-exec 包装原命令。
    }
    SandboxType::LinuxSeccomp => {
        // 调用 codex-linux-sandbox helper，传入序列化权限配置。
    }
    SandboxType::WindowsRestrictedToken => {
        // 保留权限快照，交给 Windows 执行边界实施。
    }
};
```

关键点是 `additional_permissions` 会先与基础权限合并，得到这一次命令真正使用的 effective profile。

## 14. macOS Seatbelt

来源：[`sandboxing/src/seatbelt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/sandboxing/src/seatbelt.rs)

```rust
pub const MACOS_PATH_TO_SEATBELT_EXECUTABLE: &str = "/usr/bin/sandbox-exec";
```

```rust
let full_policy = policy_sections.join("\n");

let mut seatbelt_args: Vec<String> = vec!["-p".to_string(), full_policy];
seatbelt_args.extend(definition_args);
seatbelt_args.push("--".to_string());
seatbelt_args.extend(command);
```

Codex 动态生成 Seatbelt policy，并将原命令追加在 `--` 后执行。

## 15. Linux bubblewrap + seccomp

来源：[`linux-sandbox/src/bwrap.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/linux-sandbox/src/bwrap.rs)

```rust
//! The overall Linux sandbox is composed of:
//! - seccomp + `PR_SET_NO_NEW_PRIVS` applied in-process, and
//! - bubblewrap used to construct the filesystem view before exec.
```

```rust
/// Wrap a command with bubblewrap so the filesystem is read-only by default,
/// with explicit writable roots and read-only subpaths layered afterward.
```

Linux 默认先建立只读文件系统视图，再叠加 writable roots 和受保护只读子路径。网络受限时，即使文件系统全部可写，也仍需要 namespace/seccomp 相关隔离。

## 16. `apply_patch` 也经过 Orchestrator

来源：[`core/src/tools/handlers/apply_patch.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/handlers/apply_patch.rs)

```rust
let mut orchestrator = ToolOrchestrator::new();
let mut runtime = ApplyPatchRuntime::new();
let result = orchestrator
    .run(&mut runtime, &request, &tool_ctx)
    .await
    .map(|result| result.output);
```

这说明本地文件修改并不是完全绕过权限系统的特殊路径。
