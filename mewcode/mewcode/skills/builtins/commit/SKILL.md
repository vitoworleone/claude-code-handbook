---
name: commit
description: 分析 git diff 并生成规范的 commit
allowedTools:
  - Bash
  - ReadFile
  - Grep
mode: inline
---

# 任务

你需要帮用户创建一个 git commit。

## 步骤

1. 运行 `git status` 查看当前变更状态
2. 运行 `git diff` 和 `git diff --staged` 查看具体变更内容
3. 分析变更，确定 commit 类型和范围：
   - feat: 新功能
   - fix: 修复 bug
   - docs: 文档变更
   - refactor: 重构
   - test: 测试
   - chore: 构建/工具变更
4. 生成 commit message，格式：`type(scope): description`
5. 用 `git add` 逐个添加相关文件（不要添加 .env、credentials 等敏感文件）
6. 执行 `git commit -m "生成的 message"`
7. 如果用户提供了额外说明，纳入 commit message
8. 如果变更覆盖超过 10 个文件，建议用户拆分成多个 commit

## 注意事项

- 不要用 `git add -A` 或 `git add .`，逐个文件添加
- commit message 用英文
- description 不超过 72 个字符

$ARGUMENTS
