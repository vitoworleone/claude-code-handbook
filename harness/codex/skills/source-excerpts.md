# Skills 关键源码摘录

基线：`codex` commit `633ab19`。

## 1. 系统 Skills 被编译进程序

来源：[`skills/src/lib.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/skills/src/lib.rs)

```rust
const SYSTEM_SKILLS_DIR: Dir =
    include_dir::include_dir!("$CARGO_MANIFEST_DIR/src/assets/samples");

const SYSTEM_SKILLS_DIR_NAME: &str = ".system";
const SKILLS_DIR_NAME: &str = "skills";
```

安装位置由下面的函数计算：

```rust
pub fn system_cache_root_dir(codex_home: &AbsolutePathBuf) -> AbsolutePathBuf {
    codex_home
        .join(SKILLS_DIR_NAME)
        .join(SYSTEM_SKILLS_DIR_NAME)
}
```

## 2. Provider 按来源分类

来源：[`ext/skills/src/sources.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/sources.rs)

```rust
#[derive(Clone, Default, Debug)]
pub struct SkillProviders {
    sources: Vec<SkillProviderSource>,
}

pub fn with_host_provider(mut self, provider: Arc<dyn SkillProvider>) -> Self {
    self.sources.push(SkillProviderSource::host("host", provider));
    self
}

pub fn with_executor_provider(mut self, provider: Arc<dyn SkillProvider>) -> Self {
    self.sources.push(SkillProviderSource::executor("executor", provider));
    self
}

pub fn with_orchestrator_provider(mut self, provider: Arc<dyn SkillProvider>) -> Self {
    self.sources.push(SkillProviderSource::orchestrator("orchestrator", provider));
    self
}
```

## 3. 三类 Catalog 可以并行发现

来源：[`ext/skills/src/world_state_catalogs.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/world_state_catalogs.rs)

```rust
let (executor, orchestrator, host) = futures::join!(
    self.discover_executor_catalog(query.clone()),
    self.discover_orchestrator_catalog(query),
    self.discover_host_catalog(),
);

CatalogContributions {
    executor,
    orchestrator,
    host,
}
```

## 4. Catalog 是 developer 片段

来源：[`ext/skills/src/fragments.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/fragments.rs)

```rust
impl ContextualUserFragment for AvailableSkillsInstructions {
    fn role(&self) -> &'static str {
        "developer"
    }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("skills.catalog".to_string())
    }

    fn body(&self) -> String {
        render_available_skills_body(
            self.prompt_kind,
            &self.skill_root_lines,
            &self.skill_lines,
        )
    }
}
```

## 5. 完整 Skill 片段单独保存 contents

```rust
pub(crate) struct SkillInstructions {
    pub(crate) name: String,
    pub(crate) path: String,
    pub(crate) contents: String,
    pub(crate) resource_access: Option<SkillResourceAccess>,
}

impl ContextualUserFragment for SkillInstructions {
    fn role(&self) -> &'static str {
        "user"
    }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("skills.selected_skill_instructions".to_string())
    }
}
```

这证明 catalog 和完整正文是两种不同的上下文对象。

## 6. 先根据用户输入选中 Skill

来源：[`ext/skills/src/extension.rs`](https://github.com/openai/codex/blob/633ab199cfd724aa78013c006b27a2b3d049fc3b/codex-rs/ext/skills/src/extension.rs)

```rust
let selected_entries =
    collect_explicit_skill_mentions(&input.user_input, &catalog);
```

随后 extension 才会为选中的 entries 读取内容并生成 `SkillInstructions`。
