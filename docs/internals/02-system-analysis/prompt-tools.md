# 09 Prompt 详解：工具

## 如果面试官问：如何提升工具调用的准确度？

> system_prompt 里有一段专门讲"怎么用工具"。但"工具相关的 Prompt"远不止这一段。事实上，影响模型 如何选择和使用工具的 Prompt 分散在三个不同的层次 ，每一层的注入方式、生效时机和设计意图都不同。

```Plaintext
工具相关 Prompt 的三层架构:

第一层: system_prompt 中的全局指令
  → get_using_tools_section() — "用 Read 不要用 cat"
  → 控制模型的工具选择倾向

第二层: 每个工具的 Schema description
  → ToolSchema.description — 每个工具的"使用说明书"
  → 作为 API tools 参数发给模型
  → 模型根据 description 判断"用哪个工具做这件事"

第三层: Skill 的 Prompt 注入
  → SkillTool 返回 skill.prompt 作为 ToolResult
  → 本质是按需加载的 Prompt 模板
```

本篇逐层展开这三层架构，原文完整呈现，逐句讲解设计意图。

## 1. `get_using_tools_section()` — 工具选择的全局指令

位置： `cc/prompts/sections.py:109-123`

这个函数返回一个完整的 prompt 段落，注入到 system_prompt 的第五段（在 `builder.py:112` 中调用）。它是模型在整个对话过程中始终可见的"工具使用总纲"。

**英文原文**

```Plaintext
# Using your tools
 - Do NOT use the Bash to run commands when a relevant dedicated tool is provided.
   Using dedicated tools allows the user to better understand and review your work.
   This is CRITICAL to assisting the user:
  - To read files use Read instead of cat, head, tail, or sed
  - To edit files use Edit instead of sed or awk
  - To create files use Write instead of cat with heredoc or echo redirection
  - To search for files use Glob instead of find or ls
  - To search the content of files, use Grep instead of grep or rg
  - Reserve using the Bash exclusively for system commands and terminal operations
    that require shell execution. If you are unsure and there is a relevant dedicated
    tool, default to using the dedicated tool and only fallback on using the Bash tool
    for these if it is absolutely necessary.
 - You can call multiple tools in a single response. If you intend to call multiple
   tools and there are no dependencies between them, make all independent tool calls
   in parallel. Maximize use of parallel tool calls where possible to increase
   efficiency. However, if some tool calls depend on previous calls to inform dependent
   values, do NOT call these tools in parallel and instead call them sequentially.
   For instance, if one operation must complete before another starts, run these
   operations sequentially instead.
```

**中文翻译**

```Plaintext
# 使用工具
 - 当存在相关的专用工具时，不要使用 Bash 来执行命令。使用专用工具可以让用户更好地
   理解和审查你的工作。这对于协助用户至关重要：
  - 读取文件请用 Read，而不是 cat、head、tail 或 sed
  - 编辑文件请用 Edit，而不是 sed 或 awk
  - 创建文件请用 Write，而不是带 heredoc 的 cat 或 echo 重定向
  - 搜索文件请用 Glob，而不是 find 或 ls
  - 搜索文件内容请用 Grep，而不是 grep 或 rg
  - 将 Bash 专门保留给需要 shell 执行的系统命令和终端操作。如果你不确定，
    且存在相关的专用工具，请默认使用专用工具，只有在绝对必要时才回退到使用
    Bash 工具。
 - 你可以在单个响应中调用多个工具。如果你打算调用多个工具，且它们之间没有依赖
   关系，请并行发起所有独立的工具调用。尽可能最大化并行工具调用以提高效率。
   但是，如果某些工具调用依赖于前一个调用的结果，则不要并行调用这些工具，而应
   按顺序调用。例如，如果某个操作必须在另一个操作开始之前完成，则按顺序执行这
   些操作。
```

### 1.1 逐句分析

**"Do NOT use the Bash to run commands when a relevant dedicated tool is provided."**

这是整段话的核心断言。模型天生倾向于用 Bash 解决一切问题—— `cat file.py` 、 `grep -r "pattern" .` 、 `echo "content" > file.py` 都是合法的 Bash 命令，模型完全可以用 Bash 工具完成文件读取、搜索和写入。但系统设计者不希望这样做。

为什么？紧接着的一句话给出了理由："Using dedicated tools allows the user to better understand and review your work." 这不是技术原因，而是 UX 原因。专用工具（Read、Edit、Grep）的调用参数和返回结果是结构化的，用户可以在 UI 上看到"读取了哪个文件""搜索了什么模式""替换了哪段文字"；而 Bash 调用只有一个 `command` 字符串，用户需要自己解析 shell 命令才能理解模型在做什么。

**"This is CRITICAL to assisting the user"**

注意措辞。不是"recommended"，不是"preferred"，而是"CRITICAL"。这个全大写的强调词不是随便写的。模型对 prompt 中的语气强度是敏感的——"you should"和"you MUST"、"important"和"CRITICAL"在模型行为上有可观测的差异。这里用 CRITICAL 是为了确保模型在面临"用 Bash 还是用专用工具"的选择时，几乎总是选择后者。

**五组替代对**

```Plaintext
Read    > cat, head, tail, sed
Edit    > sed, awk
Write   > cat with heredoc, echo redirection
Glob    > find, ls
Grep    > grep, rg
```

这是整段 prompt 中最值得反复品味的设计。设计者没有写"请使用合适的工具"，而是列出了五组具体的替代映射。

为什么具体优于抽象？因为"请使用合适的工具"是一个模糊指令——模型可能认为 `cat` 就是"合适的"（毕竟 cat 确实能读文件），从而继续使用 Bash。而"To read files use Read instead of cat, head, tail, or sed"直接告诉模型：你脑子里想到 cat 的那个瞬间，换成 Read。这是用枚举消除歧义。

注意 `sed` 出现了两次——既在 Read 组（ `sed -n '10,20p'` 用于查看文件片段）又在 Edit 组（ `sed -i 's/old/new/' file` 用于替换文件内容）。这说明设计者对模型可能使用 Bash 的具体场景做了细致的分析，而非泛泛地说"不要用 Bash"。

**"Reserve using the Bash exclusively for system commands and terminal operations that require shell execution."**

这句话划定了 Bash 的正当使用范围。 `git commit` 、 `npm install` 、 `python -m pytest` 、 `docker build` 这些命令确实只能通过 shell 执行，没有对应的专用工具。Bash 工具不是被禁止了，而是被限制在了"没有替代方案"的场景中。

**"If you are unsure and there is a relevant dedicated tool, default to using the dedicated tool"**

面对不确定性时的默认行为：优先专用工具。这是一种"安全默认值"设计——在两个选项都能完成任务时，选择对用户更友好的那个。

**"Maximize use of parallel tool calls where possible to increase efficiency."**

并行化指令。模型在单个响应中可以发起多个 tool_use block。如果模型需要读取三个文件来理解一个问题，它可以在一个响应中同时发起三个 Read 调用，而不是串行地发三轮对话。这对性能的提升是显著的：三个文件的 IO 延迟从串行的 T1+T2+T3 变成并行的 max(T1,T2,T3)。

**"if some tool calls depend on previous calls to inform dependent values, do NOT call these tools in parallel"**

依赖约束。如果模型需要先用 Grep 搜索一个函数的定义位置，再用 Read 读取那个文件——第二个调用的 `file_path` 参数取决于第一个调用的结果——这种情况下并行是错误的，因为模型无法在发起 Read 时填入正确的文件路径。

"Maximize parallel"和"do NOT parallel when dependent"这两句话构成了一对互补约束。如果只说前者，模型可能在存在依赖关系时也尝试并行（用猜测的参数值），导致工具调用失败。如果只说后者，模型可能过于保守地把所有调用都变成串行。两条规则同时存在，才能让模型在效率和正确性之间找到平衡点。

## 2. Schema description 即 Prompt — 举例分析

第一层是 system_prompt 中的全局规则，告诉模型"优先用专用工具"。但模型在决定用哪个专用工具时，依赖的信息来源是另一层：每个工具的 `ToolSchema.description` 。

### 2.1 description 的传递路径

每个工具类的 `get_schema()` 方法返回一个 `ToolSchema` 对象（ `cc/tools/base.py:22-32` ），其中包含 `name` 、 `description` 、 `input_schema` 三个字段。在 `query_loop` 的每一轮中， `ToolRegistry.get_api_schemas()` 会遍历所有已注册工具，将它们的 schema 转为 dict 列表，作为 API 请求的 `tools` 参数传入。

从模型的视角看，它在每次被调用时都能看到所有可用工具的名称、描述和参数定义。description 就是模型判断"这个工具能做什么"的唯一依据。换言之，description 不是给人看的文档——它是给模型看的"内联 prompt"。

### 2.2 典型 description 对比分析

**BashTool** ( `cc/tools/bash/bash_tool.py:53-55` )

**英文原文**

```Python
description="Executes a given bash command and returns its output."
```

**中文翻译**

```Python
description="执行给定的 bash 命令并返回其输出。"
```

极简，一句话。因为 Bash 本身是万能的——任何能在终端执行的命令都可以通过 Bash 工具执行。不需要告诉模型 Bash 能做什么， 模型对 shell 的理解比任何 description 都要充分 。description 只需要说明交互契约：你给一个命令，我执行它，把输出返回给你。

**FileReadTool** ( `cc/tools/file_read/file_read_tool.py:43-45` )

**英文原文**

```Python
description="Reads a file from the local filesystem."
```

**中文翻译**

```Python
description="从本地文件系统读取一个文件。"
```

同样简洁，但有一个关键限定词："from the local filesystem"。这告诉模型此工具只能读取本地文件，不能读取远程 URL——如果要获取网页内容，需要用 WebFetchTool 而非 Read。

**FileEditTool** ( `cc/tools/file_edit/file_edit_tool.py:41-42` )

**英文原文**

```Python
description="Performs exact string replacements in files."
```

**中文翻译**

```Python
description="在文件中执行精确的字符串替换。"
```

重点在"exact"。这个词不是修辞，而是功能约束。FileEditTool 的实现逻辑是在文件内容中查找 `old_string` 的精确匹配并替换为 `new_string` （ `cc/tools/file_edit/file_edit_tool.py:67-70` ）。如果模型给出的 `old_string` 与文件中的实际内容有一个空格的差异，替换就会失败。"exact"这个词预先告知模型：你必须提供与文件内容完全一致的字符串，不能靠"大概记得是什么"来编辑。

**FileWriteTool** ( `cc/tools/file_write/file_write_tool.py:39-41` )

**英文原文**

```Python
description="Writes a file to the local filesystem."
```

**中文翻译**

```Python
description="向本地文件系统写入一个文件。"
```

与 Read 对称。注意它没有说"creates or overwrites"——这个信息隐含在"writes"中。模型对"write"的理解默认包含覆盖语义，不需要额外说明。

**GlobTool** ( `cc/tools/glob_tool/glob_tool.py:32-33` )

**英文原文**

```Python
description="Fast file pattern matching tool that works with any codebase size."
```

**中文翻译**

```Python
description="快速文件模式匹配工具，适用于任意规模的代码库。"
```

这里有两个值得注意的信号词。"Fast"暗示模型应该优先使用此工具而非 Bash 中的 `find` 命令。"works with any codebase size"解决模型的一个常见顾虑：在大型代码库中， `find` 可能很慢甚至超时，而 Glob 工具被设计为在任意规模的代码库中都能快速工作。

**GrepTool** ( `cc/tools/grep_tool/grep_tool.py:33-34` )

**英文原文**

```Python
description="Search file contents using regex patterns."
```

**中文翻译**

```Python
description="使用正则表达式搜索文件内容。"
```

简洁地说明了核心功能：基于正则表达式搜索文件内容。模型读到这句话就知道：要在代码库中搜索某个函数名、某段注释或某个字符串模式时，用这个工具。

**AgentTool** ( `cc/tools/agent/agent_tool.py:68-70` )

**英文原文**

```Python
description="Launch a sub-agent to handle complex, multi-step tasks autonomously."
```

**中文翻译**

```Python
description="启动一个子 agent 来自主处理复杂的多步骤任务。"
```

这是所有工具 description 中最具约束性的一个。三个关键词界定了使用场景：

- **"complex"——简单任务不需要子 agent，直接用其他工具完成即可**
- **"multi-step"——只涉及单步操作的任务不需要子 agent**
- **"autonomously"——子 agent 一旦启动就自主运行，父 agent 不干预中间过程**

这三个词组合起来形成了一个隐含的"使用门槛"：只有当任务既复杂又需要多步骤且可以自主完成时，才应该派生子 agent。如果 description 只写"Launch a sub-agent"，模型可能在任何稍微麻烦的任务上都启动子 agent，造成不必要的资源消耗和延迟。

### 2.3 设计规律

观察以上六个 description，可以归纳出一个规律： **description 越短越通用，越长越约束** 。

BashTool 的 description 最短（10 个单词），因为它的使用场景最广。AgentTool 的 description 最长（带约束性形容词），因为设计者需要精确控制模型在何时使用它。中间的 Read/Edit/Write/Glob/Grep 保持适度简洁，给模型足够信息做判断，但不过度限制。

description 本质上是"内联 prompt"——它不在 system_prompt 中，但它与 system_prompt 一起被发送给模型，对模型的工具选择行为有直接影响。一个写得好的 description 能让模型在正确的时机选择正确的工具；一个写得模糊的 description 会导致模型频繁选错工具，然后靠错误反馈来纠正，浪费 token 和时间。

## Skill 的 Prompt 注入机制

前两层——system_prompt 全局指令和 Schema description——在每次 API 调用中都会发送。第三层不同：Skill 的 prompt 是按需加载的，只有在模型或用户触发时才进入对话上下文。

### 3.1 Skill 不是工具，是 Prompt 注入

从文件结构看，Skill 的定义在 `cc/skills/loader.py` ，而触 发 Skill 的 SkillTool 在 `cc/tools/skill/skill_tool.py` 。但如果你去看 SkillTool 的 `execute()` 方法（ `skill_tool.py:64-85` ），会发现它做的全部事情就是：

1. 根据名称查找 Skill 对象
2. 取出 `skill.prompt` （一段 Markdown 文本）
3. 如果有参数，追加到 prompt 末尾
4. 把整个 prompt 文本作为 `ToolResult.content` 返回

```Python
async def execute(self, tool_input: dict[str, Any]) -> ToolResult:
    skill_name = tool_input.get("skill", "")
    ...
    found = get_skill_by_name(self._skills, skill_name)
    ...
    prompt = found.prompt
    if args:
        prompt = f"{prompt}\n\nArguments: {args}"
    return ToolResult(content=prompt)
```

这里没有任何"执行"动作——不运行命令、不读写文件、不调用 API。SkillTool 的 execute 方法是一个纯粹的 prompt 查找和返回操作。它返回的 ToolResult 进入 transcript 后，模型在下一轮就能看到这段 prompt 文本，然后按照其中的指令行事。

所以，Skill 在本质上不是一个"工具"，而是一个 Prompt 注入机制。SkillTool 只是触发注入的载体。

### 3.2 两条触发路径

Skill 有两条独立的触发路径，最终效果相同（ `main.py:141-143` 的注释明确说明了这一点）。

**路径一：用户在 REPL 中用 slash command 触发**

用户输入 `/commit` ，REPL 层识别这是一个 skill 命令（ `main.py:714-724` ）：

```Python
elif isinstance(result, str) and result.startswith("__SKILL__"):
    skill_name = result[len("__SKILL__"):]
    found_skill = get_skill_by_name(skills, skill_name)
    if found_skill:
        messages.append(UserMessage(content=found_skill.prompt))
```

REPL 层直接将 skill.prompt 包装成 UserMessage 追加到 transcript。这条路径绕过了工具系统——不经过 ToolSchema 注册、不经过 SkillTool.execute()、不经过 StreamingToolExecutor。skill 的 prompt 文本作为一条"用户消息"直接注入到对话历史中。

**路径二：模型在对话中自主调用 SkillTool**

模型在 system prompt 的 `system-reminder` 中看到可用 skill 列表，判断当前任务需要某个 skill 的能力，于是发起一个 tool_use 调用：

```JSON
{
  "type": "tool_use",
  "name": "Skill",
  "input": {"skill": "commit"}
}
```

SkillTool.execute() 查找 "commit" skill，返回 ToolResult(content=skill.prompt)。这个 ToolResult 按照标准流程被拼为 tool_result 消息块追加到 transcript。模型在下一轮看到这段 prompt 文本。

两条路径的区别在于 prompt 文本在 transcript 中的位置和角色：路径一是 UserMessage（用户消息），路径二是 tool_result（工具返回结果）。但从模型的行为角度看，效果几乎相同——模型都会看到 skill 的 prompt 文本，并按照其中的指令行事。

### 3.3 SkillTool 的 description

**英文原文**

```Python
description="Load a skill by name and return its prompt text for the model to follow."
```

**中文翻译**

```Python
description="按名称加载一个 skill，并返回其 prompt 文本供模型遵循。"
```

这个 description 对模型传达了两层信息。第一，这个工具的作用是"加载 skill"——模型需要先知道 skill 的名称才能调用。第二，"for the model to follow"直接告诉模型：工具返回的内容不是供你参考的信息，而是你需要遵循的指令。这句话让模型在收到 skill prompt 后会将其当作行动指南而非普通文本。

### 3.4 与 system_prompt 注入的区别

Skill prompt 和 system_prompt 段落都是影响模型行为的指令文本，但它们在两个维度上有本质区别。

**Token 成本不同** 。system_prompt 中的每个段落（get_intro_section、get_system_section、get_using_tools_section 等）在每次 API 调用中都会发送。即使模型从不需要某段规则，它的 token 成本也已经产生了。Skill prompt 只有在被触发时才进入 transcript，不使用就不占 token。对于像 `/commit` 这样的 skill——包含大量 Git 操作规则但只在提交代码时才需要——按需加载比始终注入节省了大量基础 token。

**生效时机不同** 。system_prompt 在整个对话的每一轮都可见，模型从第一轮到最后一轮都受其约束。Skill prompt 从注入那一轮开始可见，在 compact（上下文压缩）时可能被压缩掉。这意味着 skill 的指令是"临时性"的——它影响注入后的几轮对话，但不像 system_prompt 那样具有永久约束力。

这个区别解释了为什么工具选择偏好（"用 Read 不要用 cat"）放在 system_prompt 而非 skill 中：工具选择是每一轮都需要遵守的全局规则，必须始终可见；而 Git 提交的最佳实践只在提交时需要，按需注入即可。

### 3.5 Skill 的加载与定义

Skill 的加载逻辑在 `cc/skills/loader.py` 。 `load_skills()` 函数（第 38-66 行）从两个目录搜索 `.md` 文件：

1. `~/.claude/skills/` — 用户级技能，跨项目共享
2. `.claude/skills/` — 项目级技能，随项目代码分发

每个 `.md` 文件被解析为一个 `Skill` 对象（第 23-35 行），包含四个字段：

```Python
@dataclass
class Skill:
    name: str          # 技能名称，默认取自文件名
    description: str   # 简短描述，在技能列表中展示
    prompt: str        # 核心：注入到对话中的 prompt 文本
    trigger: str = ""  # 可选的自动触发模式
```

支持可选的 YAML frontmatter 来覆盖默认值（ `loader.py:91-105` ）。没有 frontmatter 的文件，整个文件内容直接作为 prompt。这意味着用户只需要在 `~/.claude/skills/` 下放一个 `my-skill.md` 文件，写入想要模型遵循的规则，就能通过 `/my-skill` 或模型自主调用来激活它。

## 4. 三层架构的协作

三层 Prompt 不是孤立的，它们在运行时形成一个协作链条。

模型收到一个用户请求后，首先根据 system_prompt 中的 `get_using_tools_section()` 确定"优先使用专用工具"的总原则。然后扫描所有工具的 Schema description，判断哪个工具最适合当前任务。如果任务需要特定领域知识（比如 Git 提交规范），模型可以调用 SkillTool 加载对应的 skill prompt，获得额外的行动指南。

这个设计体现了一种"渐进式 Prompt 加载"策略：

- 全局规则始终加载（第一层），保证基础行为正确
- 工具描述始终可见（第二层），保证工具选择正确
- 领域知识按需加载（第三层），避免无关 prompt 浪费 token

三层的 token 成本递减：system_prompt 段落加上所有工具的 Schema description 构成了每次请求的固定成本；Skill prompt 只有在被触发时才产生成本。这种分层设计在 token 预算有限的场景下（尤其是长对话接近上下文窗口上限时）尤其重要——它确保了最关键的规则始终在场，而非必要的规则只在需要时才出现。