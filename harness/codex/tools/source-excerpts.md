# Tools 关键源码摘录

基线：`codex` commit `633ab19`。

这些片段用于学习调用链，不参与编译。真实实现以链接的原始文件为准。

## 1. 工具总装配线

来源：[`core/src/tools/spec_plan.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/spec_plan.rs)

```rust
let mut registry = ToolRegistry::default();
add_core_tool_sources(&context, &mut registry);

let registered_mcp_tools = session.services.mcp_handler_cache.append_mcp_tools(
    mcp,
    &turn_context.config,
    apps_enabled,
    &mcp.config().mcp_server_catalog,
    search_tool_enabled(turn_context, model_info),
    &mut registry,
);

let standalone_web_search_tool = append_extension_tool_executors(
    turn_context,
    model_info,
    extension_tool_executors(session, step_store),
    &mut registry,
);

append_dynamic_tool_runtimes(&turn_context.dynamic_tools, &mut registry);

let hosted_specs = hosted_model_tool_specs(
    turn_context,
    model_info,
    standalone_web_search_tool.as_slice(),
);

finalize_tool_router(
    turn_context,
    model_info,
    registry,
    hosted_specs,
    &session.services.tool_search_handler_cache,
)
```

这段代码表明 Tool 并非来自一个列表，而是由 Core、MCP、Extension、Dynamic、Hosted 五类来源汇合。

## 2. Core Tool 的四个来源

来源：[`core/src/tools/spec_plan.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/tools/spec_plan.rs)

```rust
fn add_core_tool_sources(context: &CoreToolPlanContext<'_>, registry: &mut ToolRegistry) {
    // Guardian 分支略
    add_shell_tools(context, registry);
    add_mcp_resource_tools(context, registry);
    add_core_utility_tools(context, registry);
    add_collaboration_tools(context, registry);
}
```

## 3. 只有连接了 MCP Server 才出现 Resource 工具

```rust
fn add_mcp_resource_tools(context: &CoreToolPlanContext<'_>, registry: &mut ToolRegistry) {
    if context.mcp.has_servers() {
        registry.add(ListMcpResourcesHandler);
        registry.add(ListMcpResourceTemplatesHandler);
        registry.add(ReadMcpResourceHandler);
    }
}
```

## 4. Extension Tool 也要经过能力过滤

```rust
for executor in executors {
    let tool_name = executor.tool_name();
    let is_standalone_web_search = tool_name == ToolName::namespaced("web", "run");

    if is_standalone_web_search && (!standalone_web_search_enabled || !web_search_mode_on) {
        continue;
    }
    if tool_name == ToolName::namespaced(IMAGE_GEN_NAMESPACE, IMAGEGEN_TOOL_NAME)
        && !image_generation_available(turn_context, model_info)
    {
        continue;
    }

    let runtime = Arc::new(ExtensionToolAdapter::new(executor));
    registry.register_external(runtime);
}
```

## 5. Code Mode 注册的是两个入口工具

```rust
let execute_handler = CodeModeExecuteHandler::new(
    create_code_mode_tool(/* 可嵌套调用的工具定义 */),
    code_mode_nested_tool_specs,
);

registry.prepend_trusted(Arc::new(CodeModeWaitHandler));
registry.prepend_trusted(Arc::new(execute_handler));
```

对模型而言，它主要看到 `exec` 和 `wait`；部分底层能力被变成 `exec` 内部可调用的方法。
