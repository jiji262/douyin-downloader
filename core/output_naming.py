"""抖音视频、音乐和直播共用的输出目录与文件名规划。"""

from pathlib import Path

from utils.naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE
from utils.naming_limits import PathLimits, fit_names


def _directory_budgets(parent, suffixes):
    limits = PathLimits.for_directory(parent)
    roots = dict.fromkeys((parent.absolute(), parent.resolve()))
    return [(root, limits, suffixes) for root in roots]


def _output_budgets(file_manager, config, parent, *, suffixes, with_transcript):
    budgets = _directory_budgets(parent, suffixes)
    transcript_config = config.get("transcript") or {}
    transcript_root = str(transcript_config.get("output_dir", "")).strip()
    if with_transcript and transcript_config.get("enabled") and transcript_root:
        try:
            relative = parent.resolve().relative_to(file_manager.base_path.resolve())
        except (OSError, ValueError):
            # 与 TranscriptManager.resolve_output_dir 一致:镜像失败回到视频目录。
            return budgets
        transcript_parent = (Path(transcript_root) / relative).absolute()
        budgets.extend(_directory_budgets(transcript_parent, (".transcript.json",)))
    return budgets


def plan_output_paths(
    *,
    file_manager,
    config,
    context,
    mode,
    author_sec_uid=None,
    collection_dir=None,
    with_transcript=False,
    suffixes=(".mp4.tmp", ".transcript.json", "_comments.json"),
):
    parent = file_manager.get_save_path(
        author_name=context.get("author", ""),
        mode=mode,
        folderstyle=False,
        author_sec_uid=author_sec_uid,
        author_dir_style=config.get("author_dir") or "nickname",
        group_by_mode=config.get("group_by_mode", True),
        collection_dir=collection_dir,
    )
    templates = {"file": config.get("filename_template") or DEFAULT_FILE_TEMPLATE}
    if config.get("folderstyle", True):
        templates["folder"] = config.get("folder_template") or DEFAULT_FOLDER_TEMPLATE
    budgets = _output_budgets(
        file_manager,
        config,
        parent,
        suffixes=suffixes,
        with_transcript=with_transcript,
    )
    names = fit_names(
        templates,
        context,
        lambda names: all(
            limits.fits(root, names, suffixes=endings) for root, limits, endings in budgets
        ),
        fallback="{0}_{1}".format(context.get("date", ""), context.get("id", "")),
    )
    directory = parent / names["folder"] if "folder" in names else parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory, names["file"]
