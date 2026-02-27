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

    # 下载完成后，询问是否进行 Whisper 转录
    print()
    try:
        choice = input("是否对下载的视频进行 Whisper 语音转录？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if choice in ("y", "yes", "是"):
        # 读取config获取下载路径
        download_path = "./Downloaded"
        # 从命令行参数读取
        for i, arg in enumerate(sys.argv):
            if arg in ("-p", "--path") and i + 1 < len(sys.argv):
                download_path = sys.argv[i + 1]
                break
        # 从config.yml读取 (命令行没指定时)
        if download_path == "./Downloaded":
            config_path = "config.yml"
            for i, arg in enumerate(sys.argv):
                if arg in ("-c", "--config") and i + 1 < len(sys.argv):
                    config_path = sys.argv[i + 1]
                    break
            try:
                import yaml
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if cfg.get("path"):
                    download_path = cfg["path"]
            except Exception:
                pass

        # transcript 输出目录 (避免原目录路径含特殊字符)
        transcript_dir = os.path.join(download_path, "transcripts")

        # 构建 whisper 参数
        whisper_args = [
            sys.executable,
            str(project_root / "cli" / "whisper_transcribe.py"),
            "-d", download_path,
            "-o", transcript_dir,
            "--sc",
            "--skip-existing",
        ]

        # 询问模型
        try:
            model = input("Whisper 模型 [base/small/medium] (回车=base): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            model = ""
        if model in ("tiny", "base", "small", "medium", "large"):
            whisper_args.extend(["-m", model])

        # 询问是否输出SRT
        try:
            srt = input("同时输出 SRT 字幕？(y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            srt = ""
        if srt in ("y", "yes", "是"):
            whisper_args.append("--srt")

        import subprocess
        subprocess.run(whisper_args)