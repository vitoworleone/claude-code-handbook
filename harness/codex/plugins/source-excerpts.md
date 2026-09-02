# Plugins 关键源码摘录

基线：`codex` commit `633ab19`。

## 1. Manifest 明确分开四类能力

来源：[`plugin/src/manifest.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/plugin/src/manifest.rs)

```rust
pub struct PluginManifest<Resource> {
    pub name: String,
    pub version: Option<String>,
    pub description: Option<String>,
    pub keywords: Vec<String>,
    pub paths: PluginManifestPaths<Resource>,
    pub interface: Option<PluginManifestInterface<Resource>>,
}

pub struct PluginManifestPaths<Resource> {
    pub skills: Vec<Resource>,
    pub mcp_servers: Option<PluginManifestMcpServers<Resource>>,
    pub apps: Option<Resource>,
    pub hooks: Option<PluginManifestHooks<Resource>>,
}
```

这正是 Plugin、Skill、MCP、App 不应混成一个概念的源码证据。

## 2. MCP 配置可以内联或引用文件

```rust
pub enum PluginManifestMcpServers<Resource> {
    Path(Resource),
    Object(String),
}
```

## 3. Plugin framework 的公开能力

来源：[`core-plugins/src/lib.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core-plugins/src/lib.rs)

```rust
pub use manager::PluginInstallRequest;
pub use manager::PluginInstallOutcome;
pub use manager::PluginUninstallError;
pub use manager::PluginsManager;
pub use manager::RecommendedPluginCandidatesInput;
pub use provider::ExecutorPluginProvider;
pub use provider::ResolvedExecutorPlugin;
```

这些类型说明 framework 同时负责管理安装状态、推荐候选和 executor 来源。

## 4. Loader 读取 Manifest 后分流能力

来源：[`core-plugins/src/loader.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core-plugins/src/loader.rs)

关键调用顺序可以概括为：

```rust
let Some(loaded_manifest) = load_plugin_manifest_with_format(plugin_root.as_path()) else {
    loaded_plugin.error = Some("missing or invalid plugin.json".to_string());
    return loaded_plugin;
};

// 根据 manifest：
// 1. load_plugin_skill_inventory(...)
// 2. load_plugin_mcp_servers_from_manifest_with_format(...)
// 3. load_plugin_apps_from_manifest(...)
// 4. load_plugin_hooks(...)
```

这里保留为调用链摘要；完整错误处理、policy 和兼容格式逻辑请直接阅读原文件。

## 5. 默认资源文件名

```rust
const DEFAULT_SKILLS_DIR_NAME: &str = "skills";
const DEFAULT_HOOKS_CONFIG_FILE: &str = "hooks/hooks.json";
const DEFAULT_MCP_CONFIG_FILE: &str = ".mcp.json";
const DEFAULT_APP_CONFIG_FILE: &str = ".app.json";
const CONFIG_TOML_FILE: &str = "config.toml";
```

这些是 Loader 的默认与兼容入口；Codex 原生 manifest 仍以 `.codex-plugin/plugin.json` 为核心。
