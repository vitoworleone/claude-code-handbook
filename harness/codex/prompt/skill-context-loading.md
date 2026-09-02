# Codex Skill 如何渐进式载入模型上下文

本文只说明 `codex` 源码中 Skill 的**上下文装配**。重点是区分两件事：

1. 模型一开始看到的 Skill **目录**；
2. 某个 Skill 被选中后，才读取并注入的 `SKILL.md` **正文**。

> 结论：Codex 不会在一开始把所有 `SKILL.md` 正文放入模型上下文。它先注入经过预算控制的 `skills.catalog`；只有选中的 Skill 才会读取正文，并作为 `skills.selected_skill_instructions` 注入。

## 1. `skills.catalog` 是什么

`skills.catalog` 不是一个 Skill、不是工具，也不是 MCP 协议中的对象。它是 Codex 为“一段列出可用 Skill 的目录上下文”定义的**内部内容类别名**。

这个名称可拆为：

```text
skills  = 与 Skill 有关
catalog = 目录、清单
```

模型实际读到的是一段文本，结构类似：

```text
## Skills
### Skill roots
### Available skills
- Skill 名称、描述和路径/资源定位符
```

源码用 `ContentItemKind("skills.catalog")` 为这段文本加分类标签：

```rust
impl ContextualUserFragment for AvailableSkillsInstructions {
    fn role(&self) -> &'static str { "developer" }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("skills.catalog".to_string())
    }
}
```

这个标签让 Runtime 能区分“这是 Skill 目录上下文”；它不代表模型获得了一个名为 `skills.catalog` 的可调用能力。与之对应，已选中 Skill 的正文使用另一个内容类别：`skills.selected_skill_instructions`。

## 2. 两阶段流程

```text
发现本地 / 插件 / 执行环境中的 Skill
  → 建立 SkillCatalog
  → 渲染目录（skills.catalog）
  → 发送给模型

用户输入中选中某个 Skill
  → 读取该 Skill 的 SKILL.md
  → 渲染正文（skills.selected_skill_instructions）
  → 注入本轮后续模型上下文
```

## 3. 第一阶段：注入目录，而不是正文

目录上下文的类型在 [`fragments.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/fragments.rs) 中定义：

```rust
pub(crate) struct AvailableSkillsInstructions {
    prompt_kind: SkillPromptKind,
    skill_root_lines: Vec<String>,
    skill_lines: Vec<String>,
}

impl ContextualUserFragment for AvailableSkillsInstructions {
    fn role(&self) -> &'static str { "developer" }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("skills.catalog".to_string())
    }
}
```

它使用 `developer` 角色进入上下文，内容类别为 `skills.catalog`。渲染函数在 [`catalog_prompt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/catalog_prompt.rs)：

```rust
pub(crate) fn render_available_skills_body(...) -> String {
    lines.push("## Skills".to_string());
    lines.push(prompt_kind.intro().to_string());
    lines.push("### Skill roots".to_string());
    lines.push("### Available skills".to_string());
    lines.extend(skill_lines.iter().cloned());
}
```

因此首轮目录包含的是：Skill 根路径（或资源定位方式）、名称、描述、路径/资源定位符，以及读取和使用规则；**不包含每个 Skill 的 `SKILL.md` 正文**。

### 目录的大小也受控制

Skills 扩展在 [`extension.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/extension.rs) 渲染目录时，会先依据上下文窗口和配置计算 `metadata_budget`，再调用 `render_catalog`：

```rust
let metadata_budget = skill_metadata_budget(
    context_window,
    config.max_context_tokens,
);
let rendered = render_catalog(
    ...,
    &turn_catalog,
    include_usage,
    SkillCatalogRenderPolicy::ExtensionCompatible,
    metadata_budget,
);
```

目录是“元数据层”，因此可以在有限 token 预算下截断描述或省略部分条目，而不需要装入全部正文。

## 4. 第二阶段：确定哪些 Skill 被选中

当前实现的主机侧选择函数是 [`collect_explicit_skill_mentions`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/selection.rs)：

```rust
pub(crate) fn collect_explicit_skill_mentions(
    inputs: &[UserInput],
    catalog: &SkillCatalog,
) -> Vec<SkillCatalogEntry>
```

它只从当前 `UserInput` 中收集显式提及：

```rust
UserInput::Skill { name, path }
UserInput::Mention { name, path } if path_is_skill(path)
UserInput::Text { text, .. }  // 提取文本中的 Skill 名称或 skill:// 路径
```

找到条目后还会检查 `entry.enabled`，并按名称/路径去重。这个结果叫 `selected_skills` 或 `selected_entries`。

`core` 在 [`turn.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs) 中调用这一逻辑：

```rust
let mentioned_skills =
    collect_explicit_skill_mentions(user_input, skills_outcome, &connector_slug_counts);
```

这里的“显式”很重要：仅凭目录存在，不会自动把所有 Skill 正文加载进来。

## 5. 第三阶段：读取被选 Skill 的正文

`core` 将已选中的 Skill 传给 `HostSkillsSnapshot::load_skill_prompts`：

```rust
let HostSkillPrompts { fragments, .. } =
    skills_snapshot.load_skill_prompts(&mentioned_skills).await;
```

在 [`host_prompt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/host_prompt.rs) 中，读取发生在循环内部：

```rust
for skill in selected_skills {
    match self.read_skill_text(skill).await {
        Ok(contents) => {
            prompts.fragments.push(Box::new(SkillInstructions {
                name: skill.name.clone(),
                path: skill.path_to_skills_md.to_string_lossy().into_owned(),
                contents,
                resource_access: None,
            }));
        }
    }
}
```

因此，`read_skill_text` 只对 `selected_skills` 执行；未选中的 Skill 不读取正文。

对于由 Skills 扩展提供的 Skill，等价路径在 [`extension.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/extension.rs) 中调用 `read_main_prompt(entry, ...)`，同样只遍历 `selected_entries`。

## 6. 正文怎样成为模型上下文

被选 Skill 的正文使用另一种片段类型 `SkillInstructions`：

```rust
impl ContextualUserFragment for SkillInstructions {
    fn role(&self) -> &'static str { "user" }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("skills.selected_skill_instructions".to_string())
    }
}
```

其渲染结构为：

```text
<skill>
<name>Skill 名称</name>
<path>SKILL.md 的路径或资源定位符</path>
<resource_access>可选的资源访问信息</resource_access>
SKILL.md 正文
</skill>
```

Core 将这些片段转换为会话项：

```rust
let skill_items = fragments
    .into_iter()
    .map(ContextualUserFragment::into_boxed_response_item)
    .collect::<Vec<_>>();
```

这些 `ResponseItem` 随后进入模型请求的 `Prompt.input`；它们不是 `Prompt.tools`。

## 7. 与最终 Prompt 的关系

最终模型请求的统一结构在 [`client_common.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/client_common.rs) 中：

```rust
pub struct Prompt {
    pub input: Vec<ResponseItem>,
    pub(crate) tools: Arc<[ToolSpec]>,
    pub base_instructions: BaseInstructions,
}
```

Skill 的两层内容都属于 `input`：

```text
Prompt.input
├── skills.catalog                       所有可见 Skill 的目录元数据
├── skills.selected_skill_instructions   仅被选中 Skill 的正文
├── 用户消息、历史、工具结果
└── 其他动态上下文
```

这正是渐进式披露：先用较小的目录让模型知道“有什么”，再按需把一个或少数 Skill 的完整操作说明送进上下文。

## 8. 阅读源码的顺序

1. [`fragments.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/fragments.rs)：目录片段与正文片段的角色、类别和渲染格式。
2. [`catalog_prompt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/catalog_prompt.rs)：目录实际如何生成。
3. [`selection.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/selection.rs)：如何从用户输入选中 Skill。
4. [`host_prompt.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/host_prompt.rs)：主机侧如何只读取已选 Skill 的正文。
5. [`core/src/session/turn.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/core/src/session/turn.rs)：如何把片段变为 `ResponseItem` 并纳入本轮上下文。
