"""Allow running cc as a package: python -m cc."""

# 包入口点：支持通过 `python -m cc` 方式运行本项目
from cc.main import main

# 直接调用主函数启动 CLI
main()
