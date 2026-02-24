#!/usr/bin/env python3
import sys
import os
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

os.chdir(project_root)

if __name__ == '__main__':
    from cli.main import main
    main()
