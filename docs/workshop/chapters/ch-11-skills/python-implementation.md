# Python 源码解析：Skill 系统

## 模块概览

Python 版把 Skill 系统拆成了四个文件，各管一件事：

|     |     |
| --- | --- |
| 文件  | 职责  |
| `parser.py` | 定义 `SkillDef` 数据类，YAML frontmatter 解析，参数替换 |
| `loader.py` | `SkillLoader` ，三位置扫描（项目 → 用户 → 内置），热重载 |
| `executor.py` | `SkillExecutor` ，两种执行模式（inline / fork） |
| `directory.py` | `SkillDirectory` ，自定义工具注册， `tool.json` 解析 |

四个文件加起来约 520 行，用四个小模块分而治之，每个文件只关心一件事。核心链路是：读文件 → 解析元数据 → 注册到目录 → 执行时注入 prompt。

## 核心类型

### SkillDef：一个 Skill 的完整描述

```Python
@dataclass
class SkillDef:
    name: str
    description: str
    prompt_body: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    model: str | None = None
    context: Literal["full", "recent", "none"] = "full"
    source_path: Path | None = None
    is_directory: bool = False
```

Python 直接用一个 dataclass 搞定 Skill 的完整描述，元数据和 prompt 内容放在一起。 `mode` 字段决定执行方式： `"inline"` 表示注入主会话， `"fork"` 表示启动子 Agent。 `context` 控制 fork 时传递多少上下文给子 Agent，有三个选择： `"full"` 传完整摘要、 `"recent"` 传最近几条、 `"none"` 不传。

注意 `source_path` 字段。它记录了这个 Skill 是从哪个文件加载的，后面热重载的时候要用：每次 `get()` 都重新读一遍这个文件，这样 Skill 作者修改了 Markdown 之后不需要重启就能生效。

`allowed_tools` 是工具白名单。fork 模式下子 Agent 只能使用这里列出的工具，是隔离机制的核心。

### 名字校验：防止乱起名

```Python
VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}
```

Skill 名字只能用小写字母、数字和连字符，必须字母开头。这不是随意的限制：Skill 名字会出现在 slash 命令里（ `/my-skill` ），如果允许大写或特殊字符，用户在命令行输入会很痛苦。 `VALID_MODES` 和 `VALID_CONTEXTS` 是枚举白名单，写错了在加载时就报错，不会等到执行时才炸。

### SkillLoader：三位置扫描和热重载

```Python
class SkillLoader:
    def __init__(self, work_dir: str) -> None:
        self._work_dir = work_dir
        self._project_dir = Path(work_dir) / PROJECT_SKILLS_DIR
        self._user_dir = Path(USER_SKILLS_DIR).expanduser()
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}
```

两个字典看着像重复，其实各有用途。 `_skills` 是当前生效的版本， `_cache` 是上一次成功加载的版本。热重载失败的时候用 `_cache` 做 fallback，保证已经在用的 Skill 不会因为一次手误改坏了文件就突然消失。

`Path(USER_SKILLS_DIR).expanduser()` 是 Python 的 pathlib 特色， `~/.mewcode/skills` 会被自动展开成用户主目录下的绝对路径，一行搞定。

### SkillExecutor：执行器

```Python
class SkillExecutor:
    def __init__(
        self,
        agent: Agent,
        client: LLMClient,
        protocol: str,
    ) -> None:
        self.agent = agent
        self.client = client
        self.protocol = protocol
```

执行器持有 Agent 和 LLM Client 的引用。 `execute_inline` 和 `execute_fork` 两个方法分别处理两种执行模式。 `execute_inline` 和 `execute_fork` 两个方法分别处理两种执行模式。inline 直接调 Agent 的方法注入 prompt，fork 创建一个独立的 Agent 实例，逻辑很显式。

## 主流程走读

### 入口：load\_all

```Python
def load_all(self) -> dict[str, SkillDef]:
    seen: dict[str, SkillDef] = {}
    for skill in self._scan_directory(self._project_dir, "project"):
        if skill.name not in seen:   # 先到先得
            seen[skill.name] = skill
    for skill in self._scan_directory(self._user_dir, "user"):
        if skill.name not in seen:
            seen[skill.name] = skill
    for skill in self._load_builtins():
        if skill.name not in seen:
            seen[skill.name] = skill
    self._skills = seen
    self._cache = {k: v for k, v in seen.items()}
    return seen
```

三个位置依次扫描：项目目录 `.mewcode/skills/` 、用户目录 `~/.mewcode/skills/` 、内置 Skill 包。注意 `if skill.name not in seen` 这个判断：同名 Skill 只保留先遇到的，后面的被忽略。项目级先扫，所以项目级 Skill 优先于用户级，用户级优先于内置。

项目目录先扫，所以项目级 Skill 优先于用户级，用户级优先于内置。先到先得（ `not in seen` 跳过后来者）的策略简单直接，优先级由扫描顺序天然决定。

最后一行 `self._cache = {k: v for k, v in seen.items()}` 做了一次浅拷贝。不能直接 `self._cache = seen` ，否则两个字典指向同一个对象，后续修改 `_skills` 会连 `_cache` 一起改。

### 目录扫描：\_scan\_directory

```Python
def _scan_directory(self, path: Path, source: str) -> list[SkillDef]:
    if not path.is_dir():
        return []
    results = []
    for entry in sorted(path.iterdir()):
        try:
            if entry.is_file() and entry.suffix == ".md":
                results.append(parse_skill_file(entry))
            elif entry.is_dir() and (entry / "SKILL.md").is_file():
                skill = parse_skill_file(entry / "SKILL.md")
                skill.is_directory = True
                results.append(skill)
        except SkillParseError as e:
            log.warning("Skipping %s: %s", entry.name, e)
    return results
```

支持两种目录结构：直接在 skills 目录下放一个 `.md` 文件，或者放一个子目录，子目录里有 `SKILL.md` 。后者多设了 `skill.is_directory = True` ，表示这个 Skill 有自己的独立目录，可能还带着自定义工具（ `tool.json` ）。

`sorted(path.iterdir())` 保证了加载顺序的确定性。不排序的话，文件系统返回的顺序在不同平台上可能不同，导致同名 Skill 的优先级不可预测。

容错策略用 `try/except` 包住每个 Skill 的解析，单个文件写坏了只打 warning，不影响其他 Skill 加载。

### 内置 Skill 加载

```Python
def _load_builtins(self) -> list[SkillDef]:
    results: list[SkillDef] = []
    builtins_pkg = importlib.resources.files("mewcode.skills.builtins")
    for resource in builtins_pkg.iterdir():
        skill_md = resource / "SKILL.md" if resource.is_dir() else None
        if skill_md is None or not skill_md.is_file():
            continue
        try:
            raw = skill_md.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(raw)
            _validate_meta(meta, f"builtin:{resource.name}")
            # ... 构建 SkillDef，注册到 results ...
```

这里用了 `importlib.resources` 而不是直接拼路径。原因是内置 Skill 打包在 Python package 里面，安装后的位置不可预测（可能在 site-packages，可能在 zip 里）。 `importlib.resources.files()` 不管包装形式是什么都能正确读取。

`importlib.resources` 不管包装形式是什么都能正确读取，解决的核心问题是不依赖运行时的文件系统路径。

## Frontmatter 解析

### parse\_frontmatter：分离元数据和正文

```Python
def parse_frontmatter(raw: str) -> tuple[dict, str]:
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise SkillParseError("Missing YAML frontmatter")
    end = stripped.find("---", 3)
    if end == -1:
        raise SkillParseError("Unclosed YAML frontmatter")
    yaml_block = stripped[3:end]
    body = stripped[end + 3:].lstrip("\n")
```

核心是字符串切割：找到两个 `---` 标记，中间的是 YAML，后面的是 Markdown 正文。先用 `lstrip()` 去掉文件开头可能的空白，然后从位置 3 开始搜索第二个 `---` 。切出 YAML 块和正文后，交给 YAML 解析器：

```Python
    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillParseError(f"Invalid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise SkillParseError("Frontmatter must be a YAML mapping")
    return meta, body
```

`yaml.safe_load` 而不是 `yaml.load` ，这是 Python YAML 库的安全约定。 `yaml.load` 允许在 YAML 里嵌入任意 Python 对象（包括执行代码），用在用户提供的文件上会有安全风险。 `safe_load` 只解析基础数据类型：字符串、数字、列表、字典。Skill 文件来自用户编写，必须用 safe 版本。

### 元数据校验

```Python
def _validate_meta(meta: dict, source: str = "") -> None:
    ctx = f" in {source}" if source else ""
    if "name" not in meta:
        raise SkillParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise SkillParseError(f"Missing required field 'description'{ctx}")
    name = meta["name"]
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(
            f"Invalid skill name '{name}'{ctx}: must be lowercase, digits, hyphens")
```

校验发生在解析阶段，不是执行阶段。这意味着一个写错名字的 Skill 在加载时就被拒绝，不会进入 `_skills` 字典等到用户触发时才报错。错误信息带上了 `source` （来源路径），方便排查是哪个文件出了问题。

### 从文件到 SkillDef

```Python
def parse_skill_file(path: Path) -> SkillDef:
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    _validate_meta(meta, str(path))
    return SkillDef(
        name=meta["name"],
        description=meta["description"],
        prompt_body=body,
        allowed_tools=meta.get("allowedTools", []),
        mode=meta.get("mode", "inline"),
        model=meta.get("model"),
        context=meta.get("context", "full"),
        source_path=path,
        is_directory=False,
    )
```

三步流水线：读文件 → 分离 frontmatter → 校验后构造 `SkillDef` 。可选字段用 `meta.get()` 取值并给默认值， `allowedTools` 默认空列表、 `mode` 默认 `"inline"` 、 `context` 默认 `"full"` 。创建一个 Skill 只需要写 `name` 和 `description` ，其他都有合理的默认值。

## 热重载

```Python
def get(self, name: str) -> SkillDef | None:
    skill = self._skills.get(name)
    if skill is None:
        return None
    if skill.source_path is not None:
        try:
            fresh = parse_skill_file(skill.source_path)
            fresh.is_directory = skill.is_directory
            self._skills[name] = fresh
            self._cache[name] = fresh
            return fresh
        except SkillParseError as e:
            log.warning("Hot-reload failed for '%s': %s", name, e)
            return self._cache.get(name, skill)
    return skill
```

每次调用 `get()` 获取 Skill 时，如果有 `source_path` ，就重新读一遍文件。这实现了「热重载」：用户修改了 Skill 的 Markdown 文件后，下次触发这个 Skill 就能自动用上新版本，不需要重启。

关键的容错逻辑是 `except SkillParseError` ：如果重新解析失败（比如用户改坏了 YAML frontmatter），不返回 `None` ，而是返回 `_cache` 里上一次成功的版本。这就是前面说的两个字典的用途： `_skills` 是随时更新的， `_cache` 是最后一次完整加载时的快照，充当安全网。

热重载的代价是每次 `get()` 都有一次文件 IO，但 Skill 文件通常很小（几 KB），IO 开销可以忽略不计。换来的好处是开发体验极佳：改完 Markdown 保存，下次触发就是新版本。

## 两种执行模式

### Inline 模式：注入主会话

```Python
def execute_inline(
    self, skill: SkillDef, args: str
) -> None:
    prompt = substitute_arguments(
        skill.prompt_body, args
    )
    self.agent.activate_skill(skill.name, prompt)
```

两行代码，先替换参数，再调 Agent 的 `activate_skill` 方法把 prompt 注入主会话。Skill 的指令变成了主 Agent 上下文的一部分，Agent 接下来的行为就受这段 prompt 的引导。简单直接，不创建新的 Agent 实例，不开新的对话。

### Fork 模式：启动子 Agent

```Python
async def execute_fork(self, skill: SkillDef, args: str) -> str:
    prompt = substitute_arguments(skill.prompt_body, args)
    fork_conv = ConversationManager()
    context_messages = self._build_fork_context(skill.context)
    for msg in context_messages:
        if msg.role == "user":
            fork_conv.add_user_message(msg.content)
        else:
            fork_conv.add_assistant_message(msg.content)
    fork_conv.add_user_message(prompt)
```

Fork 模式创建全新的 `ConversationManager` ，然后根据 `context` 字段决定传多少历史进去。注意这是一个 `async` 方法，因为子 Agent 的执行需要异步等待 LLM 响应。

接下来是工具白名单的过滤和子 Agent 的创建：

```Python
    filtered_registry = filter_tool_registry(
        self.agent.registry, skill.allowed_tools
    )

    fork_agent = AgentClass(
        client=self.client,
        registry=filtered_registry,
        protocol=self.protocol,
        work_dir=self.agent.work_dir,
        max_iterations=self.agent.max_iterations,
        permission_checker=None,
        context_window=self.agent.context_window,
    )
```

`permission_checker=None` 是个有意思的设计：子 Agent 不做权限检查。因为主 Agent 已经通过了权限系统的审核才触发了这个 Skill，子 Agent 的行为范围又被 `allowed_tools` 限制住了，再加一层权限检查是多余的。

子 Agent 和主 Agent 共享同一个 `work_dir` 和 `context_window` ，但有独立的对话历史和工具列表。

### 工具白名单过滤

```Python
def filter_tool_registry(registry: ToolRegistry, allowed: list[str]) -> ToolRegistry:
    if not allowed:
        return registry
    filtered = ToolRegistry()
    for name in allowed:
        tool = registry.get(name)
        if tool is None:
            raise SkillDependencyError(f"Skill requires tool '{name}' but it is not registered")
        filtered.register(tool)
```

如果 `allowed_tools` 为空，直接返回完整的 Registry，子 Agent 能用所有工具。如果指定了白名单，创建一个新的 Registry 只注册白名单里的工具。白名单里有工具名在 Registry 里找不到的，直接抛 `SkillDependencyError` ，不默默忽略。

白名单过滤完之后，还有一个例外处理：

```Python
    for tool in registry.list_tools():
        if getattr(tool, "is_system_tool", False) \
           and filtered.get(tool.name) is None:
            filtered.register(tool)
    return filtered
```

`is_system_tool` 标记为 `True` 的工具总是会被加进去。系统工具（比如 `LoadSkill` 本身）是基础设施，Skill 作者不应该需要手动声明它们。

## 上下文传递策略

Fork 模式的上下文传递有三种策略，由 `context` 字段控制：

```Python
def _build_fork_context(
    self, mode: str
) -> list[Message]:
    if mode == "none":
        return []

    history = (self.agent._conversation.history
               if hasattr(self.agent, '_conversation')
               else [])
```

**"none"** ：空列表，子 Agent 完全从零开始，只看到 Skill 的 prompt。适合独立任务，比如代码格式化。

**"recent"** ：取最近几条对话消息，具体数量由 `FORK_RECENT_COUNT = 5` 控制。

```Python
    if mode == "recent":
        content_messages = [
            m for m in main_history
            if m.content and not m.tool_results
        ]
        return content_messages[-FORK_RECENT_COUNT:]
```

过滤掉了工具结果消息（ `not m.tool_results` ），只保留用户和 Agent 之间的对话文本。工具输出往往又长又碎，传给子 Agent 是噪音。取最后 5 条，够让子 Agent 了解最近在讨论什么。

**"full"** ：传递完整历史的摘要。

```Python
    if mode == "full":
        summary_parts = []
        for m in content_messages:
            prefix = ("User" if m.role == "user"
                      else "Assistant")
            text = m.content[:200]
            if len(m.content) > 200:
                text += "..."
            summary_parts.append(f"{prefix}: {text}")
        summary = ("## Previous conversation summary"
                   "\n\n"
                   + "\n\n".join(summary_parts))
        return [Message(role="user", content=summary)]
```

不是直接把完整历史搬过去，而是每条消息只保留前 200 个字符，加上角色前缀，拼成一段摘要。这样既保留了上下文信息，又不会把子 Agent 的上下文窗口撑爆。最后包装成一条 `user` 消息注入子 Agent 的对话。

## $ARGUMENTS 替换

```Python
def substitute_arguments(
    prompt_body: str, args: str
) -> str:
    return prompt_body.replace("$ARGUMENTS", args)
```

一行代码，纯字符串替换。统一做替换，如果 prompt 里没有 `$ARGUMENTS` 这个字符串， `replace` 返回原字符串，不会出错也不会改任何东西。这意味着 Skill 作者需要自己记得在 prompt 里写 `$ARGUMENTS` 占位符，否则用户传的参数会被静默忽略。简洁性和便利性之间的取舍。

## 自定义工具：SkillDirectory

Skill 不只是 prompt，还可以自带工具。 `directory.py` 负责解析 Skill 目录下的 `tool.json` 并注册自定义工具。

### 解析 tool.json

```Python
def parse_tool_json(path: Path) -> list[dict]:
    raw = json.loads(
        path.read_text(encoding="utf-8")
    )
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        log.warning(
            "tool.json at %s must be a JSON "
            "array or object", path
        )
        return []
    return raw
```

`tool.json` 可以是一个对象（定义单个工具）或一个数组（定义多个工具）。如果是单个对象，包装成列表统一处理。这种「单个和多个都接受」的模式在 API 设计里很常见，降低简单场景的使用成本。

### 动态加载工具实现

```Python
def load_tool_implementation(references_dir: Path, tool_name: str):
    script = references_dir / f"{tool_name}.py"
    if not script.is_file():
        return None
    module_name = f"mewcode_skill_tool_{tool_name}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "execute", None)
```

这是 Python 的动态模块加载：给定一个 `.py` 文件路径，用 `importlib.util` 动态创建一个模块并执行它，然后从模块里取出 `execute` 函数。约定很简单：工具的 Python 实现文件必须定义一个 `execute` 函数。

`module_name` 加了 `mewcode_skill_tool_` 前缀，避免和已有的模块名冲突。如果两个 Skill 都定义了一个叫 `formatter` 的工具，它们的模块名分别是 `mewcode_skill_tool_formatter` ，不会互相覆盖。

### SkillCustomTool：动态工具包装器

```Python
class SkillCustomTool(Tool):
    def __init__(self, tool_name, description, schema, impl):
        self.name = tool_name
        self.description = description
        self._schema = schema
        self._impl = impl
```

`SkillCustomTool` 继承自 `Tool` 基类，把 `tool.json` 里定义的 schema 和动态加载的 `execute` 函数包装成标准工具。构造函数只是存引用，真正的逻辑在 `execute` 方法里：

```Python
    async def execute(self, params: BaseModel) -> ToolResult:
        if self._impl is None:
            return ToolResult(output=f"Error: no impl for '{self.name}'", is_error=True)
        kwargs = params.model_dump()
        if asyncio.iscoroutinefunction(self._impl):
            result = await self._impl(**kwargs)
        else:
            result = self._impl(**kwargs)
        return ToolResult(output=str(result))
```

注意 `asyncio.iscoroutinefunction` 的检测：工具实现可以是同步函数也可以是异步函数，执行器自动判断该用 `await` 还是直接调用。

`_impl` 为 `None` 时不报错退出，而是返回一个 `is_error=True` 的结果。这样 Agent 能看到错误信息并做出反应（比如换个方式完成任务），而不是让整个流程崩掉。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 三位置加载 | `load_all` 按项目 → 用户 → 内置顺序扫描，先到先得（ `not in seen` ） |
| Frontmatter 解析 | `yaml.safe_load` 安全解析，拒绝任意 Python 对象注入 |
| 热重载 | `get()` 每次重新读文件，失败时用 `_cache` 兜底 |
| Inline 执行 | `activate_skill()` 直接注入主会话 prompt |
| Fork 执行 | 创建独立 `Agent` 实例 + 独立 `ConversationManager` |
| 上下文传递 | 三种模式：none（不传）、recent（最近5条）、full（每条截断200字符的摘要） |
| 工具白名单 | `filter_tool_registry` 创建新 Registry，系统工具自动放行 |
| 自定义工具 | `tool.json` + `references/xxx.py` 动态加载 |
| 参数替换 | 纯 `str.replace` ，无 `$ARGUMENTS` 时参数被静默忽略 |
| 容错策略 | 加载失败打 warning 跳过，热重载失败用缓存兜底 |