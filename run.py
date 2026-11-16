#!/usr/bin/env python3
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
# agent 模式的相对路径按调用方的 cwd 解析，不切到仓库根
if sys.argv[1:2] != ["agent"]:
    os.chdir(project_root)

if __name__ == "__main__":
    from cli.main import main

    main()
