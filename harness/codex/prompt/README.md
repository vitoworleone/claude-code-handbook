# Prompt：上下文组装文档

这个目录记录 Codex 最终发送给模型的指令、消息和 Tool schemas 是怎样形成的。

## 阅读顺序

1. [system-prompt.md](system-prompt.md)：先建立完整 Prompt 的总体认识。
2. [base-instructions.zh-CN.md](base-instructions.zh-CN.md)：阅读基础指令中文整理版。
3. [skill-context-loading.md](skill-context-loading.md)：理解 Skill catalog 与完整正文的渐进加载。
4. [tool-context-loading.md](tool-context-loading.md)：理解 Tool schema 如何进入模型请求。
5. [完整 Context 装配](../context/)：把 Prompt、WorldState、Extensions、Memory、历史与 Tools 放回同一条链路。
6. [端到端请求与 Agent Loop 深读](../context/request-lifecycle.md)：用源码跟完用户输入、Skill 注入、Tool Call、图片/音频结果和再次采样。

## 分主题源码文档

- [Tools](../tools/)
- [MCP](../mcps/)
- [Skills](../skills/)
- [Plugins](../plugins/)
- [Context](../context/)
