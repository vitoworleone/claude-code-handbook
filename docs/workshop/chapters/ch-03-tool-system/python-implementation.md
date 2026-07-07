# Python 源码解析：工具系统

## 模块概览

工具系统的代码分布在 `mewcode/tools/` 包下，加上一个独立的缓存模块：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `base.py` | 92  | 核心基础设施：Tool ABC、ToolResult dataclass、ToolCategory Literal、流事件 |
| `__init__.py` | 150 | ToolRegistry 注册中心，带评分的延迟搜索， `create_default_registry` 工厂 |
| `bash.py` | 49  | Bash 工具，asyncio 子进程 |
| `edit_file.py` | 56  | EditFile 工具，唯一性校验 + 缓存失效 |
| `read_file.py` | 51  | ReadFile 工具，offset/limit 分页 + 缓存 |
| `write_file.py` | 37  | WriteFile 工具，自动创建父目录 |
| `glob.py` | 38  | Glob 工具，模式匹配 |
| `grep.py` | 55  | Grep 工具，正则搜索 |
| `cache.py` | 29  | FileCache，线程安全的文件内容缓存 |

九个文件加起来不到 560 行。Python 版充分利用了 Pydantic 做参数校验和 asyncio 做异步执行，代码非常紧凑，但工具系统的完整能力一个不少。

## 核心类型

### ToolCategory：三值 Literal

```Python
ToolCategory = Literal["read", "write", "command"]
```

Python 版直接用 `Literal` 把三个合法值写死，是最简洁的做法。类型检查器能在编译期（准确说是 mypy 检查期）就捕获非法的分类值，运行时也不需要额外的校验代码。

这三个值的语义在所有语言版本里完全一致。 `"read"` 是只读操作不改文件系统， `"write"` 是写操作， `"command"` 是执行命令。权限系统根据这个分类做不同策略的检查。

### ToolResult：极简 dataclass

```Python
@dataclass
class ToolResult:
    output: str
    is_error: bool = False
```

只有两个字段， `is_error` 默认 False。这里用 dataclass 而不是 Pydantic 的 BaseModel 是刻意的选择：ToolResult 是内部数据结构，不需要 JSON 序列化和校验的开销，一个普通的 dataclass 就够了。

注意 `is_error` 不是程序级别的异常，而是告诉 LLM「这次工具调用没成功」。LLM 收到 `is_error=True` 的结果后会重新审视情况再试。比如 EditFile 找不到要替换的字符串，返回 `is_error=True` ，LLM 就知道要先用 ReadFile 确认文件内容再编辑。

### Tool ABC：抽象基类 + Pydantic 参数模型

```Python
class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...
```

Python 版的 Tool 用 ABC（抽象基类）而不是 Protocol，这意味着所有工具必须显式继承它。ABC 的好处是可以带默认实现和类属性。七个类属性直接写在基类上，子类只需要覆盖想改的。

最关键的设计在 `params_model` 字段。Python 版直接把一个 Pydantic BaseModel 类挂上去，Schema 自动生成，不需要手动构建 JSON Schema 字典：

```Python
def get_schema(self) -> dict[str, Any]:
    schema = self.params_model.model_json_schema()
    schema.pop("title", None)
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": schema,
    }
```

`model_json_schema()` 是 Pydantic v2 的内置方法，能把 BaseModel 的字段定义直接转成标准 JSON Schema。 `pop("title")` 去掉 Pydantic 自动生成的标题字段，因为 LLM API 不需要它。这个设计让每个工具只需要定义一个 Params 类，Schema 就自动有了，不需要手写字典。

还有一个 `is_read_only` 属性，是个便捷的语法糖：

```Python
@property
def is_read_only(self) -> bool:
    return self.category == "read"
```

权限系统检查工具是否只读时直接调这个属性，不需要自己比较字符串。

### FileCache：线程安全的文件缓存

```Python
class FileCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        with self._lock:
            return self._store.get(path)
```

FileCache 是 ReadFile、WriteFile、EditFile 三个工具共享的缓存层。核心就是一个字典加一把锁。三个操作 `get` 、 `put` 、 `invalidate` 都用 `with self._lock` 保护。为什么需要锁？虽然 Python 有 GIL，但 asyncio 的事件循环可能在不同的 await 点切换上下文，如果多个工具并发操作同一个文件的缓存条目，没有锁就会出问题。

`invalidate()` 的时机很重要：每次 WriteFile 或 EditFile 修改了文件之后，必须把缓存里的旧内容清掉，否则下次 ReadFile 会读到过时的数据。

## 主流程走读

工具系统的主线分三步：注册、描述、执行。

### 第一步：注册

`create_default_registry()` 是工具系统的启动入口：

```Python
def create_default_registry(
    file_cache: FileCache | None = None,
) -> ToolRegistry:
    from mewcode.tools.bash import Bash
    from mewcode.tools.edit_file import EditFile
    from mewcode.tools.read_file import ReadFile
    from mewcode.tools.write_file import WriteFile
    # ... 以及 Glob、Grep 的导入

    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache))
    registry.register(EditFile(file_cache=file_cache))
    registry.register(WriteFile(file_cache=file_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
```

两个值得注意的设计。第一，所有 import 都写在函数体内部，是延迟导入。这避免了模块级别的循环依赖：工具模块可能反过来引用 registry，延迟导入切断了这个环。第二， `file_cache` 参数通过依赖注入传给 ReadFile、WriteFile、EditFile 这三个需要缓存的工具，不需要缓存的 Bash、Glob、Grep 就不传。

注册本身很简单，就是往字典里存：

```Python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
```

Python 版还维护了一个 `_disabled` 集合。这让 Registry 支持在运行时禁用/启用某些工具，比如团队模式下可能需要禁用某些危险工具：

```Python
def disable(self, name: str) -> None:
    if name in self._tools:
        self._disabled.add(name)

def enable(self, name: str) -> None:
    self._disabled.discard(name)
```

### 第二步：生成 Schema

Agent Loop 每轮迭代调用 `get_all_schemas()` 获取所有工具描述：

```Python
def get_all_schemas(
    self, protocol: str = "anthropic"
) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for name, tool in self._tools.items():
        if name in self._disabled:
            continue
        if getattr(tool, "should_defer", False) \
                and name not in self._discovered:
            continue
        base = tool.get_schema()
        # ... protocol adaptation (openai vs anthropic) ...
        schemas.append(base)
    return schemas
```

这里做了三层过滤。第一层跳过被禁用的工具。第二层跳过未被发现的延迟工具。第三层根据 protocol 参数适配不同的 API 格式。Anthropic 原生格式用 `input_schema` ，OpenAI 格式用 `parameters` ，这一层把差异抹平了。

每个工具的 Schema 由 Pydantic 自动生成。以 EditFile 为例，它的 Params 类定义：

```Python
class Params(BaseModel):
    file_path: str = Field(
        description="Path to the file to edit"
    )
    old_string: str = Field(
        description="The exact string to find and replace"
    )
    new_string: str = Field(
        description="The replacement string"
    )
```

Pydantic 的 `model_json_schema()` 会把这个类自动转成标准 JSON Schema，包含 `type` 、 `properties` 、 `required` 等字段。工具开发者只需要写一个 Params 类，Schema 就有了，不需要手写字典。

### 第三步：执行

执行流程很直接。Agent Loop 通过 `registry.get(name)` 查找工具，然后调用 `execute()` 。 `execute()` 是 async 方法，所有工具的执行都走 asyncio 事件循环，天然支持 IO 密集型操作的并发。

## 内置工具速览

|     |     |     |     |
| --- | --- | --- | --- |
| 工具  | Category | 核心逻辑 | 关键设计 |
| ReadFile | read | 按行号范围读文件，输出带行号 | FileCache 缓存 + offset/limit 分页 |
| WriteFile | write | 创建父目录 + 写入整个文件 | 写后缓存失效 |
| EditFile | write | 唯一性校验 + 精确替换 | old\_string 必须恰好出现一次 |
| Bash | command | asyncio 子进程执行命令 | 超时杀进程 + 编码容错 |
| Glob | read | 递归遍历 + 模式匹配 | 自动跳过 SKIP\_DIRS |
| Grep | read | 正则搜索文件内容 | 支持 include 过滤 |

### 深入 EditFile：唯一性校验 + 缓存失效

EditFile 是最能体现工具设计哲学的一个。完整的 execute 方法：

先是文件校验和内容读取：

```Python
async def execute(self, params: Params) -> ToolResult:
    path = Path(params.file_path)
    if not path.exists():
        return ToolResult(
            output=f"Error: file not found: ...",
            is_error=True,
        )
    content = path.read_text(encoding="utf-8")
```

然后是唯一性校验，这是 EditFile 最核心的逻辑：

```Python
    count = content.count(params.old_string)
    if count == 0:
        return ToolResult(
            output="Error: old_string not found in file",
            is_error=True,
        )
    if count > 1:
        return ToolResult(
            output=f"Error: old_string found {count} times",
            is_error=True,
        )
```

校验通过后执行替换并失效缓存：

```Python
    new_content = content.replace(
        params.old_string, params.new_string, 1
    )
    path.write_text(new_content, encoding="utf-8")
    if self._cache:
        self._cache.invalidate(str(path.resolve()))
    return ToolResult(
        output=f"Successfully edited {params.file_path}"
    )
```

`content.count(params.old_string)` 是唯一性校验的核心。Python 的 `str.count()` 做全文计数，然后检查结果：0 次报找不到，大于 1 次报不唯一。这个约束看起来严格，但解决了一个关键安全问题：如果允许多次替换，LLM 可能把文件里所有类似的代码段都改掉，造成意想不到的破坏。

`replace()` 的第三个参数 `1` 确保只替换一次。虽然前面已经校验了只出现一次，但这里还是多加了一层保护。

写完文件后立即 `self._cache.invalidate()` ，清掉缓存里的旧版本。如果不做这一步，下次 ReadFile 读这个文件时会从缓存拿到修改前的旧内容，LLM 看到的文件状态和磁盘不一致。

### 深入 Bash：asyncio 子进程

Bash 工具是唯一用到 asyncio 底层能力的工具：

```Python
async def execute(self, params: Params) -> ToolResult:
    timeout = min(params.timeout, MAX_TIMEOUT)
    proc = await asyncio.create_subprocess_shell(
        params.command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(), timeout=timeout
    )
```

超时处理单独拎出来看：

```Python
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(
            output=f"Error: timed out after {timeout}s",
            is_error=True,
        )
```

Python 版用 `asyncio.create_subprocess_shell` + `asyncio.wait_for` 做超时控制。

`create_subprocess_shell` 启动一个真实的子进程，stdout 和 stderr 都通过管道捕获。 `wait_for` 包裹了 `proc.communicate()` ，如果超时就抛出 `TimeoutError` 。捕获到超时后先 `proc.kill()` 杀掉子进程，再 `await proc.wait()` 等它真正退出，避免僵尸进程。

输出组装的部分也值得看：

```Python
    parts: list[str] = []
    if stdout:
        parts.append(
            f"STDOUT:\n{stdout.decode(errors='replace')}"
        )
    if stderr:
        parts.append(
            f"STDERR:\n{stderr.decode(errors='replace')}"
        )
    if not parts:
        parts.append("(no output)")
    output = "\n".join(parts)
    return ToolResult(
        output=output, is_error=proc.returncode != 0
    )
```

`stdout.decode(errors='replace')` 中的 `errors='replace'` 很关键。命令输出可能包含非 UTF-8 字节（比如二进制工具的输出），直接 decode 会抛异常。 `'replace'` 策略把无法解码的字节替换成 Unicode 替换字符，保证不会因为编码问题炸掉整个工具调用。

### ReadFile：缓存 + 分页

ReadFile 展示了缓存层如何融入工具执行：

```Python
async def execute(self, params: Params) -> ToolResult:
    path = Path(params.file_path)
    if not path.exists():
        return ToolResult(output=f"Error: ...", is_error=True)

    resolved = str(path.resolve())
    text = self._cache.get(resolved) if self._cache else None
    if text is None:
        text = path.read_text(encoding="utf-8")
        if self._cache:
            self._cache.put(resolved, text)
```

读到内容后做分页和行号标注：

```Python
    lines = text.splitlines()
    selected = lines[params.offset : params.offset + params.limit]
    numbered = [
        f"{i + params.offset + 1}\t{line}"
        for i, line in enumerate(selected)
    ]
    return ToolResult(output="\n".join(numbered))
```

先用 `path.resolve()` 把路径转成绝对路径作为缓存 key，这样无论 LLM 传的是相对路径还是绝对路径，都能命中同一个缓存条目。缓存里有就直接用，没有就读磁盘再存进去。

分页用 Python 的 slice 语法： `lines[offset : offset + limit]` 。默认 offset=0、limit=2000，也就是最多读前 2000 行。LLM 如果需要读大文件的后半部分，可以传 offset 参数跳过前面的内容。

输出格式是 `行号\t内容` ，行号从 1 开始。这让 LLM 在后续用 EditFile 编辑时能精确定位位置。

### Glob 和 Grep：SKIP\_DIRS 全局常量

```Python
SKIP_DIRS = {
    ".git", ".venv", "node_modules",
    "__pycache__", ".tox", ".mypy_cache"
}
```

这个常量定义在 `base.py` 里，Glob 和 Grep 都引用它。遍历目录时，如果路径中包含这些目录名就跳过。这不仅是性能优化（不扫描巨大的 node\_modules），也是安全考虑： `.git` 目录里有大量二进制文件，扫描它们既慢又没意义。

## ToolSearch 与延迟加载

ToolRegistry 里还有一套延迟加载机制。工具类可以声明 `should_defer = True` ，Registry 就知道这个工具默认不暴露给 LLM。配套的 `search_deferred()` 实现了一套评分搜索， `find_deferred_by_names()` 支持按名称精确拉取。

目前六个内置工具都没有设置 `should_defer` ，这套机制在本章阶段不会被触发。它真正发挥作用是在第七章引入 MCP 之后：MCP 工具数量不可控，全量塞进上下文的 token 开销和对模型选择的干扰都不可接受。到那时我们会详细走读 Python 版独有的评分搜索算法和整个延迟加载流程。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 工具抽象 | ABC 抽象基类，类属性 + 一个 async 抽象方法 |
| 参数校验 | Pydantic BaseModel， `model_json_schema()` 自动生成 Schema |
| 工具分类 | `Literal["read", "write", "command"]` ，静态类型检查 |
| 结果传递 | `ToolResult` dataclass， `is_error` 让 LLM 自行处理失败 |
| 注册机制 | `ToolRegistry` 用 dict 存储，支持 disable/enable 动态管理 |
| 文件缓存 | `FileCache` 线程安全字典，ReadFile 读时填充，EditFile/WriteFile 写后失效 |
| 异步执行 | 所有 `execute()` 都是 async，Bash 用 `asyncio.create_subprocess_shell` |
| 延迟加载 | `should_defer` 类属性 + 评分搜索（详见第七章） |
| 协议适配 | `get_all_schemas(protocol)` 参数区分 Anthropic/OpenAI 格式 |