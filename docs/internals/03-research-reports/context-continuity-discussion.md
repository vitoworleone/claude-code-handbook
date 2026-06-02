# 上下文连续性、Compact 与 PDF 处理讨论记录

> 生成时间：2026-05-16  
> 范围：本轮关于 ClaudeCode-Runtime 上下文注入、上一轮状态保持、长上下文注意力稀释、session/compact 区别、PDF 解析方案的讨论整理。  

---

## 1. 核心结论

这个 runtime 不是靠“模型自己记住上一轮”，而是每次 API 调用都重新喂给模型：

```text
system prompt
+ 当前 messages transcript
+ tool schemas
```

其中：

- `system prompt` 负责告诉模型“你是谁、有哪些长期规则、有哪些项目指令、有哪些 memory 索引”。
- `messages transcript` 负责告诉模型“这一轮之前发生了什么，包括用户输入、模型输出、工具调用和工具结果”。
- `session` 负责把当前 transcript 保存到磁盘，便于 resume。
- `compact` 负责把过长 transcript 压短，让后续对话还能继续进入 context window。
- `memory` 负责跨会话保存偏好、项目事实、反馈等长期信息。

所以真正的层次是：

```text
Working Context      = 当前喂给模型的 messages + system prompt
Session Snapshot     = 当前 Working Context 的本地快照
Compact Summary      = 对旧 Working Context 的摘要替换
Memory / CLAUDE.md   = 跨会话的长期指令或知识
Raw Archive/Retrieval = 当前项目尚不完整具备的能力
```

---

## 2. 上下文和 Markdown 文档如何注入

### 2.1 CLAUDE.md 注入

`CLAUDE.md` / `.claude/CLAUDE.md` / `.claude/rules/*.md` / `CLAUDE.local.md` 由：

```text
cc/prompts/claudemd.py
```

负责从当前工作目录向上搜索、合并，并支持 `@path` import。之后由：

```text
cc/prompts/builder.py
```

拼进 system prompt。

在当前复现项目中，`build_system_prompt()` 会把 CLAUDE.md 内容作为最后一段加入：

```text
# CLAUDE.md
Codebase and user instructions are shown below...
```

它靠后出现，所以对模型的行为影响更强。但它仍然是 context，不是硬约束。

### 2.2 Memory 注入

Memory 的路径来自：

```text
~/.claude/projects/{SHA256(cwd)[:12]}/memory/
```

关键文件：

```text
MEMORY.md
```

`MEMORY.md` 是索引，不是完整知识库。启动或构建 system prompt 时，runtime 会读取 `MEMORY.md` 的索引内容，把“有哪些 memory 条目”注入 system prompt。

如果模型认为某条 memory 相关，应该再通过文件读取工具读取具体 `.md` memory 文件。

### 2.3 Skill Markdown 注入

Skill 和 CLAUDE.md 不同。

Skill 被触发后，通常不是进入 system prompt，而是作为一条 `UserMessage` 追加到 transcript：

```text
messages.append(UserMessage(content=found_skill.prompt))
```

这意味着 skill 更像“用户在当前对话里贴了一段任务说明/操作手册”。

### 2.4 普通用户输入注入

REPL 中每次普通输入都会变成：

```text
UserMessage(content=user_input)
```

并追加到同一个 `messages` list。这个 list 是当前会话的 transcript，也是模型下一次调用时看到的历史。

---

## 3. 模型如何知道上一轮做了什么

上一轮发生的事情会被写回 `messages`：

```text
用户输入          -> UserMessage
模型文本回复      -> AssistantMessage(TextBlock)
模型工具调用      -> AssistantMessage(ToolUseBlock)
工具执行结果      -> UserMessage(ToolResultBlock)
```

下一轮调用模型前，runtime 会把 `messages` 规范化为 API 格式：

```text
normalize_messages_for_api(messages)
```

所以模型看到的是：

```text
上一轮用户说了什么
上一轮模型自己说了什么
上一轮模型调用了什么工具
上一轮工具返回了什么结果
```

这就是“连续性”的本质。不是模型内部状态在记忆，而是 runtime 每轮重新把 transcript 发给模型。

---

## 4. 长上下文会不会注意力稀释

会。

长上下文有两个问题：

```text
1. 硬限制：token 太多，API 直接 prompt_too_long
2. 软退化：还没超限，但信息太多，模型容易漏看、误读、抓错重点
```

所以 context management 不只是为了“塞得下”，也是为了“看得准”。

Claude Code 官方文档也提醒：CLAUDE.md 和上下文都会消耗 context window；文件太长会降低遵循度，建议保持具体、简洁、结构化。官方还建议用 `/context` 看窗口占用，用 `/compact` 压缩长会话。

来源：

- Claude Code memory: https://code.claude.com/docs/en/memory
- Claude Code commands/context/compact: https://code.claude.com/docs/en/commands
- Claude Code how it works: https://code.claude.com/docs/en/how-claude-code-works

---

## 5. Compact 和 Session 的区别

### 5.1 Session 是恢复机制

`session` 保存的是当前 `messages` 的快照。它的作用是：

```text
退出后下次可以 resume
崩溃后可以恢复 transcript
保留工具调用和工具结果的结构
```

在这个 Python runtime 里，session 保存到：

```text
~/.claude/sessions/{session_id}.jsonl
```

相关源码：

```text
cc/session/storage.py
```

### 5.2 Compact 是工作上下文压缩机制

`compact` 的作用是把旧消息压成摘要，然后保留最近几轮原文：

```text
[Previous conversation summary]
+ 最近 4 轮 user-assistant 对话
```

它解决的是：

```text
当前对话太长，后续模型调用放不下或注意力变差
```

### 5.3 为什么 session 不能替代 compact

因为 session 只是保存当前 messages。

如果当前 messages 已经过 compact，那么 session 保存的就是 compact 后的 messages，而不是原始完整历史。

也就是说：

```text
session != append-only raw archive
session != 文档检索库
session != 无限上下文
```

它更像：

```text
当前工作状态快照
```

### 5.4 当前架构缺少什么

如果想做到“上下文短，但完整历史可回查”，需要拆出一个更深的 Module：

```text
Session Archive / Context Retrieval Module
```

它应该区分：

```text
Working Context：喂给模型的短上下文，允许 compact
Raw Archive：append-only 保存完整事件流，用于检索、审计和回放
Retrieval：按当前问题检索历史片段，再注入上下文
```

当前项目更像：

```text
当前工作上下文 = messages
恢复机制 = session snapshot
长程知识 = memory index
超长处理 = compact
```

还不是：

```text
完整历史库 + 智能检索 + 按需注入
```

---

## 6. PDF 为什么会成为问题

当前 Python runtime 的 `Read` 工具主要支持两类文件：

```text
文本文件：按 UTF-8 读取，加行号返回
图片文件：转 base64 image block
```

PDF 没有专门 Adapter。

相关源码：

```text
cc/tools/file_read/file_read_tool.py
```

PDF 不在 `IMAGE_EXTENSIONS` 中，所以 `.pdf` 会走普通文本读取路径：

```python
text = path.read_text(encoding="utf-8", errors="replace")
```

结果通常不是“直接报错”，而是更糟：

```text
PDF 二进制被当成文本读
乱码、控制字符、结构丢失
模型看到大量噪声
```

当 PDF 很多时，还会叠加：

```text
1. 解析失败：扫描版 PDF 没有文字层，需要 OCR
2. 上下文爆炸：几十/几百份 PDF 抽出的文本远超 context window
3. 注意力稀释：即使塞得进去，模型也很难稳定抓重点
4. 表格/图表丢失：纯文本抽取不一定保留版面结构
```

所以 PDF 问题不能靠 session 或 compact 解决。它需要专门的：

```text
PDF Ingestion + Document Retrieval Module
```

---

## 7. Codex / OpenAI 当前 PDF 处理方式

### 7.1 OpenAI API file input

OpenAI API 支持 PDF file input。PDF 会被处理为：

```text
抽取文本 + 每页图像
```

一起进入模型上下文。这适合少量 PDF，尤其是需要图表、版式、页面视觉理解的场景。

但官方也提醒：

```text
PDF parsing includes both extracted text and page images in context,
which can increase token usage.
```

并且单请求文件大小有约束。官方建议大文件/大语料用 File Search，而不是把完整文件直接作为 input_file。

来源：

- OpenAI file inputs: https://developers.openai.com/api/docs/guides/file-inputs
- OpenAI file search: https://developers.openai.com/api/docs/guides/tools-file-search

### 7.2 OpenAI File Search

File Search 是更适合大量 PDF 的方式：

```text
上传文件 -> vector store -> semantic + keyword search -> 返回相关片段
```

它解决的是：

```text
不要把所有 PDF 一次性塞进上下文
每轮只取和当前问题相关的 top-k chunks
```

适合：

```text
论文库
合同库
手册库
产品文档库
```

### 7.3 Codex 产品层的 Skills

Codex 不是只靠模型原生读 PDF。OpenAI 的 Codex app 介绍中明确提到 Codex 可以通过 skills 扩展到 PDF、spreadsheet、docx 等文档工作。

也就是说，Codex 中更现实的 PDF 处理路径是：

```text
PDF skill / script / MCP
  -> 提取文本、表格、图片、页面信息
  -> 形成 Markdown / JSON / chunks
  -> 再交给模型理解
```

来源：

- OpenAI Codex app: https://openai.com/index/introducing-the-codex-app/
- OpenAI skills catalog: https://github.com/openai/skills

---

## 8. Claude / Claude Code 当前 PDF 处理方式

### 8.1 Claude API PDF support

Claude API 支持 PDF，可以通过三种方式提供：

```text
1. PDF URL
2. base64 document block
3. Files API file_id
```

Claude 的 PDF 处理方式也是：

```text
每页转图像 + 每页抽取文本
```

然后 Claude 同时分析文本和视觉内容。

来源：

- Claude PDF support: https://platform.claude.com/docs/en/build-with-claude/pdf-support

### 8.2 Claude Code 文件引用与 MCP

Claude Code 支持 `@file` 引用文件、`@directory` 引用目录、`@server:resource` 引用 MCP resource。

但这不等于“很多 PDF 可以直接读”。对于大量 PDF，更好的路线仍然是：

```text
PDF parser / OCR / MCP resource
  -> 按需返回相关页或片段
  -> 注入当前会话
```

来源：

- Claude Code reference files: https://code.claude.com/docs/en/tutorials

### 8.3 Claude Skills / PDF skill

Anthropic 公开的 `anthropics/skills` 仓库包含 document skills，其中包括 PDF。README 中给的 Claude Code 示例是：

```text
Use the PDF skill to extract the form fields from path/to/some-file.pdf
```

这说明 Claude Code 做复杂 PDF 工作时，也倾向通过 skill 的脚本和流程来完成，而不是单纯依赖模型把 PDF 当上下文硬读。

来源：

- Anthropic skills: https://github.com/anthropics/skills

---

## 9. 推荐的 PDF 架构方案

对于少量 PDF：

```text
直接 file input / document input
适合：单份报告、几十页以内、需要看图表
```

对于很多 PDF：

```text
PDF Ingestion Module
  -> Adapter: text PDF / scanned PDF / table-heavy PDF / form PDF
  -> 输出 Markdown / JSON chunks
  -> 保存 file/page/section/hash metadata

Document Index Module
  -> full-text index
  -> vector index
  -> metadata filter

Context Retrieval Module
  -> 根据当前问题检索 top-k chunks
  -> 注入少量相关证据
  -> 保留文件名和页码引用
```

推荐流程：

```text
PDF -> 解析/OCR -> chunk -> metadata(page/file/section) -> index
问题 -> retrieval top-k -> 注入相关片段 -> 回答并引用来源
```

不推荐：

```text
Read all PDFs -> 塞进 messages -> 依赖 compact
```

因为这会把解析问题、上下文爆炸、注意力稀释混在一起，最后调试会很痛苦。

---

## 10. 对 ClaudeCode-Runtime 的架构启发

如果要把这个 Python Runtime 补成更完整的 harness，建议新增几个候选 Module：

### 10.1 PDF Ingestion Module

职责：

```text
识别 PDF 类型
选择 Adapter
抽取文本、表格、图片或 OCR 结果
输出统一 DocumentChunk
```

可能 Adapter：

```text
PyMuPDF / pdfplumber / pypdf / pdftotext / OCR / table extractor
```

### 10.2 Document Index Module

职责：

```text
保存 chunks
建立 full-text / vector / hybrid index
维护 metadata
```

### 10.3 Context Retrieval Module

职责：

```text
根据当前 user query 检索相关 chunks
控制注入 token budget
保留 citation
避免整库塞入 context
```

### 10.4 Raw Session Archive Module

职责：

```text
append-only 保存完整原始 conversation event stream
与 Working Context 分离
支持历史检索、审计、回放
```

这会补上当前 session snapshot 的不足。

---

## 11. 一句话总结

`session` 让 runtime 能恢复；`compact` 让当前对话能继续；`memory/CLAUDE.md` 让规则跨会话存在；但大量 PDF 需要的是独立的文档摄取和检索系统。

因此对于“很多 PDF”的场景，正确方向不是增强 compact，而是新增：

```text
PDF Ingestion + Document Retrieval + Context Injection
```

这才是能扩展、能引用、能控制 token、也更抗注意力稀释的架构。

