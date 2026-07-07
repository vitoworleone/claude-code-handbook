# Python 源码解析：权限系统

## 模块概览

权限系统的代码集中在 `mewcode/permissions/` 目录下，拆成五个文件：

|     |     |     |
| --- | --- | --- |
| 文件  | 行数  | 职责  |
| `checker.py` | 93  | 核心。PermissionChecker 类，六层检查的主流程编排 |
| `dangerous.py` | 57  | 危险命令检测 + 安全命令白名单 |
| `modes.py` | 31  | 权限模式枚举和模式矩阵 |
| `rules.py` | 106 | 规则引擎，YAML 规则文件的加载和匹配 |
| `sandbox.py` | 46  | 路径沙箱，限制文件操作的目录范围 |

五个文件加起来约 330 行。每个文件只干一件事，职责切分很干净。 `checker.py` 是编排者，其他四个是具体的检查实现。

## 核心类型

### Decision：检查结果

```Python
@dataclass
class Decision:
    effect: DecisionEffect
    reason: str
```

每次权限检查返回一个 Decision，包含决策结果和原因。 `effect` 是三种可能之一： `"allow"` 、 `"deny"` 、 `"ask"` 。 `reason` 是人类可读的说明，用中文写的，比如「危险命令拦截: 递归强制删除根目录」或「权限模式 default 放行」。

`reason` 的存在不只是为了日志。当 `effect` 是 `"ask"` 时，UI 层会把 `reason` 展示给用户，帮助用户做出允许还是拒绝的判断。

### DecisionEffect 和 PermissionMode

```Python
DecisionEffect = Literal["allow", "deny", "ask"]

class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"
    CUSTOM = "custom"
    DONT_ASK = "dontAsk"
```

`DecisionEffect` 用 `Literal` 类型定义，不是 Enum。为什么？因为它只有三个固定字符串值，用 Literal 做类型注解就够了，不需要 Enum 那套实例化和比较机制。

`PermissionMode` 继承了 `str` 和 `Enum` 两个基类。继承 `str` 意味着 `PermissionMode.DEFAULT` 本身就是字符串 `"default"` ，可以直接用 `==` 和普通字符串比较，也可以直接拼进 JSON 序列化。这是 Python Enum 的常见技巧，避免到处写 `.value` 。

六种模式覆盖了从最严格（PLAN：只读）到最宽松（BYPASS：全部放行）的完整光谱。

### 模式矩阵

```Python
_MODE_MATRIX: dict[
    PermissionMode,
    dict[ToolCategory, DecisionEffect]
] = {
    PermissionMode.DEFAULT: {
        "read": "allow",
        "write": "ask",
        "command": "ask",
    },
    PermissionMode.ACCEPT_EDITS: {
        "read": "allow",
        "write": "allow",
        "command": "ask",
    },
    PermissionMode.PLAN: {
        "read": "allow",
        "write": "ask",
        "command": "ask",
    },
    PermissionMode.BYPASS: {
        "read": "allow",
        "write": "allow",
        "command": "allow",
    },
    PermissionMode.CUSTOM: {
        "read": "ask",
        "write": "ask",
        "command": "ask",
    },
    PermissionMode.DONT_ASK: {
        "read": "allow",
        "write": "allow",
        "command": "allow",
    },
}

def mode_decide(
    mode: PermissionMode, category: ToolCategory
) -> DecisionEffect:
    return _MODE_MATRIX[mode][category]
```

模式矩阵用嵌套字典实现，外层键是权限模式，内层键是工具分类，值是决策效果。 `mode_decide` 函数就是一个二维查表操作。

注意 `PLAN` 模式在矩阵里对 write 和 command 的值是 `ask` ，不是 `deny` 。Plan 模式的只读限制不是通过矩阵实现的，而是通过 `check()` 方法里 Layer 0 的例外逻辑：只放行特定工具和计划文件的写入，其他操作走到最后一层被 `ask` 拦住。

这个矩阵把「什么模式下什么类型的工具该怎么处理」这个复杂的逻辑压缩成了一张表。如果用 if-else 写，六种模式乘三种分类就是 18 个分支，代码会膨胀到无法维护。用字典查表，加一种新模式只需要加一行，读起来一目了然。

## 主流程：PermissionChecker.check()

### PermissionChecker 的构造

```Python
class PermissionChecker:
    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
```

PermissionChecker 不自己创建依赖，而是通过构造函数注入。这是典型的依赖注入模式。好处是测试时可以传 mock 对象，不需要真的去读文件系统或执行命令。

四个依赖分别对应四层防御中的前三层（危险命令检测、路径沙箱、规则引擎）加上权限模式（第四层）。 `mode` 有默认值 `DEFAULT` ，不传就是默认模式。

### check() 主方法：六层串行检查

```Python
_PLAN_MODE_ALLOWED_TOOLS = frozenset({
    "Agent", "ToolSearch", "AskUserQuestion"
})

def check(
    self, tool: Tool, arguments: dict[str, Any]
) -> Decision:
    content = extract_content(
        tool.name, arguments
    )

    # Layer 0: Plan mode exceptions
    if self.mode == PermissionMode.PLAN:
        if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
            return Decision(
                effect="allow",
                reason="Plan mode: allowed tool",
            )
        if tool.name in ("WriteFile", "EditFile") \
                and content:
            if self._is_plan_file(content):
                return Decision(
                    effect="allow",
                    reason="Plan mode: plan file write",
                )

    # Layer 1: safe read-only commands
    if tool.category == "command" \
            and is_safe_command(content or ""):
        return Decision(
            effect="allow",
            reason="Safe read-only command",
        )

    # Layer 2: dangerous command blacklist
    if tool.category == "command":
        hit, reason = self.detector.detect(content)
        if hit:
            return Decision(
                effect="deny",
                reason=f"危险命令拦截: {reason}",
            )

    # Layer 3: path sandbox
    if tool.category in ("read", "write") \
            and content:
        ok, reason = self.sandbox.check(content)
        if not ok:
            return Decision(
                effect="deny",
                reason=f"路径沙箱拦截: {reason}",
            )

    # Layer 4: rule engine
    rule_result = self.rule_engine.evaluate(
        tool.name, content
    )
    if rule_result == "allow":
        return Decision(
            effect="allow", reason="权限规则放行"
        )
    if rule_result == "deny":
        return Decision(
            effect="deny", reason="权限规则拒绝"
        )

    # Layer 5: permission mode
    effect = mode_decide(self.mode, tool.category)
    if effect == "allow":
        return Decision(
            effect="allow",
            reason=f"权限模式 {self.mode.value} 放行",
        )
    if effect == "deny":
        return Decision(
            effect="deny",
            reason=f"权限模式 {self.mode.value} 拒绝",
        )

    # Layer 6: ASK → triggers HITL
    return Decision(
        effect="ask", reason="需要用户确认"
    )
```

这是整个权限系统的核心。六层检查按顺序执行，一旦某层产生了明确的 allow 或 deny，立即返回，不再往下走。

**第一步是内容提取** 。 `extract_content(tool.name, arguments)` 从工具参数里提取「核心内容」，比如 Bash 工具提取 `command` 字段，ReadFile 工具提取 `file_path` 字段。这个提取出来的内容是后续所有检查的输入。

**Layer 0 只在 Plan 模式下生效** 。 `Agent` 、 `ToolSearch` 、 `AskUserQuestion` 这三个工具是规划流程必需的，直接放行。写入计划文件（ `.mewcode/plans/` 目录下）也必须放行，否则 Plan Mode 连计划都写不出来。

**Layer 1 对 command 类工具自动放行安全命令** 。 `is_safe_command()` 检查命令是否在白名单内且不含管道/重定向等危险字符。匹配到的命令直接放行，减少用户被频繁打断确认的体验问题。

**Layer 2 只对 command 类工具生效** 。文件操作不走危险命令检测，因为危险命令检测的正则全是针对 shell 命令的。 `tool.category == "command"` 这个判断把文件工具排除在外。

**Layer 3 只对 read 和 write 类工具生效** 。用 `in ("read", "write")` 判断，Bash 命令不走路径沙箱。因为 Bash 命令可能涉及任意路径，路径沙箱的逻辑不适用。

**Layer 4 对所有工具生效** 。规则引擎返回三种结果： `"allow"` 、 `"deny"` 或 `None` 。前两种直接决策， `None` 表示没有匹配的规则，继续往下走。

**Layer 5 是兜底** 。如果前面所有层都没有产生决策，就用模式矩阵查表。矩阵返回的结果如果是 `"ask"` ，最终走到方法末尾返回 `Decision(effect="ask")` ，触发 UI 弹窗让用户确认。

## 各层详解

### Layer 2：危险命令检测

```Python
_DANGEROUS_PATTERNS: list[
    tuple[re.Pattern[str], str]
] = [
    (re.compile(
        r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$"),
     "递归强制删除根目录"),
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"),
     "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/"),
     "递归修改根目录权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"),
     "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"),
     "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"),
     "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
]
```

八条正则模式，每条配一个中文描述。 `re.compile()` 在模块加载时就编译好正则，避免每次检查都重新编译。

看几条有代表性的： `rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$` 匹配 `rm -rf /` ，但不是死板的字面匹配。 `[a-z]*r[a-z]*f[a-z]*` 能匹配 `rf` 、 `rif` 、 `rfv` 等各种组合，因为 rm 的参数可以混合在一起写。 `\s+/\s*$` 要求目标是根目录且在命令末尾。

`:\(\)\{\s*:\|:&\s*\};:` 匹配 fork bomb `:(){ :|:& };:` ，这个经典的 shell 攻击会无限递归派生进程，瞬间耗尽系统资源。

```Python
class DangerousCommandDetector:
    def __init__(
        self,
        extra_patterns: list[tuple[str, str]] | None
            = None,
    ) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append(
                    (re.compile(regex_str), reason)
                )

    def detect(
        self, command: str
    ) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
```

`DangerousCommandDetector` 支持通过 `extra_patterns` 扩展。构造时把模块级的默认模式复制一份（ `list(_DANGEROUS_PATTERNS)` 是浅拷贝），然后追加自定义模式。这样不同的 Checker 实例可以有不同的危险模式集合。

`detect()` 方法遍历所有模式，用 `pattern.search()` 做子串匹配（不是全字符串匹配）。一旦命中就返回 `(True, reason)` ，全部不命中返回 `(False, "")` 。

### Layer 1：安全命令白名单

`dangerous.py` 里除了危险命令检测，还定义了安全命令白名单和 `is_safe_command()` 函数：

```Python
_SAFE_COMMANDS = frozenset({
    "ls", "dir", "pwd", "echo", "cat",
    "head", "tail", "wc",
    "find", "which", "whereis", "whoami",
    "hostname", "uname",
    # ... 共约 50 个只读命令
    "git status", "git log", "git diff",
    "git show", "git branch",
    "go version", "go env",
    "python --version", "pip list",
})

def is_safe_command(command: str) -> bool:
    trimmed = command.strip()
    if not trimmed:
        return False
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    for safe in _SAFE_COMMANDS:
        if trimmed == safe \
                or trimmed.startswith(safe + " "):
            return True
    return False
```

安全命令检测先排除所有包含管道、分号、重定向、命令替换的命令。这是因为 `ls | rm -rf /` 里虽然有 `ls` ，但管道后面跟了危险命令，不能放行。 `trimmed.startswith(safe + " ")` 确保匹配的是完整的命令前缀， `ls` 能匹配 `ls -la` ，但不会匹配 `lsof` 。

### Layer 3：路径沙箱

```Python
class PathSandbox:
    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [
            root,
            Path(tempfile.gettempdir()).resolve(),
        ]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(
                    Path(p).resolve()
                )
```

路径沙箱的核心思路很简单：维护一个「允许操作的目录列表」，文件操作的目标路径必须在某个允许目录之下。

默认允许两个目录：项目根目录和系统临时目录。 `Path.resolve()` 把路径转成绝对路径并解析符号链接，防止通过 `../../../etc/passwd` 这样的相对路径逃逸。

`tempfile.gettempdir()` 在 Linux 上通常返回 `/tmp` ，这个目录是 AI 工具正常工作需要的。比如用临时文件做 diff、保存中间结果等。

```Python
def check(
    self, path: str
) -> tuple[bool, str]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = self.project_root / p
    abs_path = p.absolute()

    try:
        real_path = abs_path.resolve(strict=True)
    except OSError:
        parent = abs_path.parent
        try:
            parent_real = parent.resolve(strict=True)
        except OSError:
            return False, f"无法解析路径: {path}"
        real_path = parent_real / abs_path.name

    for root in self._allowed_roots:
        try:
            real_path.relative_to(root)
            return True, ""
        except ValueError:
            continue

    return False, f"路径 {path} 超出沙箱范围"
```

`check()` 方法的逻辑分三步：路径规范化、符号链接解析、目录检查。

`expanduser()` 把 `~` 展开为用户主目录。如果路径不是绝对路径，就拼接到项目根目录下。 `resolve(strict=True)` 解析符号链接并且要求路径真实存在。如果文件还不存在（比如要创建的新文件），就退而求其次，解析父目录的符号链接。

最后用 `real_path.relative_to(root)` 检查路径是否在允许目录下。 `relative_to()` 在路径不是子路径时会抛 `ValueError` ，这里用 try-except 捕获并继续检查下一个允许目录。

### Layer 4：规则引擎

#### 规则定义

```Python
_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")

_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}
```

`_RULE_RE` 匹配 `Bash(git *)` 这样的规则语法，第一个捕获组是工具名，第二个是通配符模式。 `_CONTENT_FIELDS` 定义了每个工具应该从参数中提取哪个字段作为匹配内容。

```Python
@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect

    def matches(
        self, tool_name: str, content: str
    ) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)
```

Rule 用 `frozen=True` 的 dataclass，创建后不可修改。 `matches()` 先比较工具名，然后用 `fnmatch` 做通配符匹配。 `fnmatch` 是 Python 标准库，支持 `*` （匹配任意字符）和 `?` （匹配单个字符）语法，和 shell 的 glob 一致。

用 `fnmatch` 而不是正则，是因为权限规则面向的是普通用户， `Bash(git *)` 比 `Bash(git\s+.*)` 直观得多。

#### 规则加载

```Python
def _load_rules_file(path: Path) -> list[Rule]:
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(
            path.read_text(encoding="utf-8")
        )
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_str = entry.get("rule", "")
        effect = entry.get("effect", "")
        if effect not in ("allow", "deny"):
            continue
        try:
            rules.append(parse_rule(rule_str, effect))
        except ValueError:
            continue
    return rules
```

规则文件是 YAML 格式，每条规则有 `rule` 和 `effect` 两个键。加载过程做了非常多的容错：文件不存在返回空列表、YAML 解析失败返回空列表、格式不对的条目跳过。这种防御性编码保证了一条坏规则不会搞崩整个权限系统。

`yaml.safe_load` 而不是 `yaml.load` ，安全加载不会执行 YAML 中嵌入的 Python 对象，避免配置文件成为攻击入口。

#### 规则求值

```Python
class RuleEngine:
    def __init__(
        self,
        user_rules_path: Path | None = None,
        project_rules_path: Path | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path

    def _load_tiers(self) -> list[list[Rule]]:
        tiers: list[list[Rule]] = []
        for p in (self._user_path,
                  self._project_path,
                  self._local_path):
            tiers.append(
                _load_rules_file(p) if p else []
            )
        return tiers

    def evaluate(
        self, tool_name: str, content: str
    ) -> Effect | None:
        for rules in self._load_tiers():
            for rule in reversed(rules):
                if rule.matches(tool_name, content):
                    return rule.effect
        return None
```

规则引擎支持三层规则文件：用户级（ `~/.mewcode/permissions.yaml` ）、项目级（ `{project}/.mewcode/permissions.yaml` ）、本地级（ `{project}/.mewcode/permissions.local.yaml` ）。

`evaluate()` 的匹配逻辑有两个重要细节。第一，先扫描所有层的 deny 规则（deny 跨层合并不可翻转），任何一层说了 deny 就直接返回。第二，再从高优先级到低优先级找 allow：本地级 → 项目级 → 用户级，先匹配到的层直接返回。每一层内部用 `reversed()` 反向遍历，最后一条匹配的规则胜出。这意味着本地级规则优先于项目级，项目级优先于用户级。

`_load_tiers()` 每次 `evaluate()` 都重新加载规则文件。这看起来效率低，但好处是用户修改了规则文件后不需要重启程序，立即生效。在权限系统这种低频调用场景下，IO 开销可以接受。

#### 动态追加规则

```Python
def append_local_rule(self, rule: Rule) -> None:
    if self._local_path is None:
        return
    self._local_path.parent.mkdir(
        parents=True, exist_ok=True
    )
    existing = _load_rules_file(self._local_path)
    existing.append(rule)
    entries = [
        {
            "rule": f"{r.tool_name}({r.pattern})",
            "effect": r.effect,
        }
        for r in existing
    ]
    self._local_path.write_text(
        yaml.dump(entries, allow_unicode=True),
        encoding="utf-8",
    )
```

当用户在 UI 上点击「始终允许」时，规则会被追加到 local 文件。 `mkdir(parents=True, exist_ok=True)` 确保目录存在，然后读取现有规则、追加新规则、序列化回 YAML。 `allow_unicode=True` 让 yaml.dump 正确处理中文字符。

### 内容提取

```Python
def extract_content(
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    return str(arguments.get(field, ""))
```

这个小函数是所有层的公共基础设施。它根据工具名查表，知道该从参数的哪个字段提取内容。比如 Bash 工具提取 `command` ，ReadFile 提取 `file_path` 。不在表里的工具返回空字符串，后续检查层会根据空字符串跳过不适用的检查。

## 小结

|     |     |
| --- | --- |
| 设计决策 | Python 的实现方式 |
| 决策结果 | `@dataclass Decision` ，effect + reason |
| 权限模式 | `str` + `Enum` 混合继承，可直接做字符串比较 |
| 模式矩阵 | 嵌套字典查表， `mode_decide()` 一行搞定 |
| 危险命令 | `re.compile()` 预编译， `pattern.search()` 子串匹配 |
| 路径沙箱 | `pathlib.Path` + `resolve()` + `relative_to()` |
| 规则语法 | `fnmatch` 通配符，对用户友好 |
| 规则文件 | `yaml.safe_load` ，三层优先级，每次调用重新加载 |
| Plan Mode 例外 | Layer 0 独立处理，白名单工具 + 计划文件写入放行 |
| 安全命令 | `_SAFE_COMMANDS` frozenset 白名单 + 管道/重定向排除 |
| 六层串行 | 早返回模式，一层命中立即返回，不继续往下 |
| 依赖注入 | 构造函数接收四个组件，方便测试 |