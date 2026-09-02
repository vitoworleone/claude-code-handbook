# MCP 关键源码摘录

基线：`codex` commit `633ab19`。

## 1. 用户配置是一个动态 Map

来源：[`config/src/config_toml.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/config/src/config_toml.rs)

```rust
/// Definition for MCP servers that Codex can reach out to for tool calls.
#[serde(default)]
#[schemars(schema_with = "crate::schema::mcp_servers_schema")]
pub mcp_servers: HashMap<String, McpServerConfig>,
```

`#[serde(default)]` 配合 `HashMap` 表示没有配置时可以是空集合。

## 2. MCP Tools 在工具装配阶段加入 Registry

来源：[`core/src/tools/spec_plan.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/spec_plan.rs)

```rust
let registered_mcp_tools = session.services.mcp_handler_cache.append_mcp_tools(
    mcp,
    &turn_context.config,
    apps_enabled,
    &mcp.config().mcp_server_catalog,
    search_tool_enabled(turn_context, model_info),
    &mut registry,
);

apply_mcp_tool_exposure_policy(
    turn_context,
    model_info,
    mcp,
    &registered_mcp_tools,
    &mut registry,
);
```

先注册，再应用可见性策略，说明“已经连接并发现”不等于“本轮一定直接发给模型”。

## 3. Apps 开启时贡献 `codex_apps`

来源：[`ext/mcp/src/lib.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/mcp/src/lib.rs)

```rust
let name = CODEX_APPS_MCP_SERVER_NAME.to_string();
if !config.features.enabled(codex_features::Feature::Apps) {
    return vec![McpServerContribution::Remove { name }];
}

vec![McpServerContribution::HostedApps {
    config: Box::new(hosted_plugin_runtime_mcp_server_config(
        &config.chatgpt_base_url,
        config.apps_mcp_product_sku.as_deref(),
        context.originator(),
    )),
}]
```

## 4. Executor Plugin 也能贡献 MCP Servers

```rust
pub fn install_executor_plugins(
    builder: &mut ExtensionRegistryBuilder<Config>,
    environment_manager: Arc<EnvironmentManager>,
) {
    builder.mcp_server_contributor(Arc::new(
        executor_plugin::SelectedExecutorPluginMcpContributor::new(environment_manager),
    ));
}
```

## 5. 入站 MCP Server 对外列出两个 Tools

来源：[`mcp-server/src/message_processor.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/mcp-server/src/message_processor.rs)

```rust
let result = rmcp::model::ListToolsResult::with_all_items(vec![
    create_tool_for_codex_tool_call_param(),
    create_tool_for_codex_tool_call_reply_param(),
]);
```

调用时按名称分发：

```rust
match name.as_ref() {
    "codex" => self.handle_tool_call_codex(id, arguments).await,
    "codex-reply" => {
        self.handle_tool_call_codex_session_reply(id, arguments).await
    }
    _ => { /* unknown tool */ }
}
```
