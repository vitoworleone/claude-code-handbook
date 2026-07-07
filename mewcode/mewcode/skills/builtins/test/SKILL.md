---
name: test
description: 运行测试并分析结果
allowedTools:
  - Bash
  - ReadFile
  - Grep
  - Glob
mode: inline
---

# 任务

你需要运行项目的测试套件并分析结果。

## 步骤

1. 检测项目类型，按优先级查找：
   - `pyproject.toml` 或 `setup.py` → Python 项目，使用 `pytest`
   - `go.mod` → Go 项目，使用 `go test ./...`
   - `package.json` → Node.js 项目，使用 `npm test`
   - `Cargo.toml` → Rust 项目，使用 `cargo test`
2. 运行对应的测试命令，捕获完整输出
3. 分析测试结果：
   - 如果全部通过：报告通过数量和覆盖率（如可用）
   - 如果有失败：区分两种失败原因：
     a. **代码 bug**：断言期望值正确但实际值错误，说明源码有问题
     b. **测试 bug**：断言期望值本身就不对，或测试设置有误，说明测试需要修复
4. 对于每个失败的测试：
   - 指出失败位置（文件名、测试名）
   - 判断是代码 bug 还是测试 bug
   - 给出具体修复建议
5. 如果全部通过，检查是否有明显缺失的测试场景：
   - 边界值测试
   - 错误路径测试
   - 空输入/极端值测试

$ARGUMENTS
