"""条目跳过 / 失败的人话原因。

事件流逐条显示这些文案，任务结束后再按原文分组计数（「160 个条目跳过：
下载目录里已有该作品」）。所以文案必须固定、不插值：带编号或状态码的
文案会被拆成一堆各计 1 次的组。编号、状态码这类细节只写日志。

能力类问题（加密、没有下载地址）的文案不能带「暂」，见
docs/spec/gotchas.md「解析成功不等于可下载」。
"""

from __future__ import annotations

import errno

# ---------------------------------------------------------------------------
# 跳过
# ---------------------------------------------------------------------------

SKIP_LOCAL_FILE_EXISTS = "下载目录里已有该作品"
SKIP_HISTORY_EXISTS = "下载历史里已有该作品（已关闭文件缺失时重新下载）"
SKIP_COMMENTS_EXIST = "已下载过，评论也已保存"
SKIP_COMMENTS_FAILED = "已下载过，补抓评论失败"
SKIP_LIVE_NOT_STREAMING = "主播当前未在直播"
SKIP_MISSING_ID = "作品 ID 为空"
SKIP_UNSPECIFIED = "已跳过（未记录具体原因）"

# ---------------------------------------------------------------------------
# 失败：作品详情与数据
# ---------------------------------------------------------------------------

# get_video_detail 在「请求被拒 / 空响应」与「作品已删除 / 不可见」时都只回 None，
# 这里分不清是哪一种，所以两种可能都写上。
FAIL_DETAIL_UNAVAILABLE = (
    "抖音未返回作品详情（可能触发了风控验证，或作品已删除 / 不可见），"
    "请确认作品能在抖音打开后重试，或重新登录"
)
FAIL_MISSING_ID = "作品数据缺少 ID"
FAIL_UNSUPPORTED_MEDIA = "不支持的作品类型"

# ---------------------------------------------------------------------------
# 失败：视频 / 图集资源
# ---------------------------------------------------------------------------

FAIL_NO_VIDEO_URL = "作品未返回可下载的视频地址"
FAIL_PAID_NO_VIDEO_URL = "付费作品未返回可下载的视频地址"
FAIL_VIDEO_ALL_SOURCES = "视频所有下载线路均失败，请稍后重试"
FAIL_VIDEO_DEADLINE = "视频下载超过单条时限仍未完成，已放弃"
FAIL_ENCRYPTED = "加密视频（付费 / 会员内容），本地无法播放，已删除"
FAIL_GALLERY_NO_ASSETS = "图集未返回可下载的图片地址"
FAIL_GALLERY_IMAGE = "图集中有图片在所有线路上均下载失败"
FAIL_LIVE_PHOTO = "图集里的实况视频下载失败"
FAIL_NOTHING_SELECTED = "未勾选视频或任何附件，没有可下载的内容"
FAIL_OPTIONAL_ASSETS = "所选附件（封面 / 音乐等）全部下载失败"
FAIL_SELECTED_ASSETS_MISSING = "作品没有所选附件（封面 / 音乐等）的下载地址"

# ---------------------------------------------------------------------------
# 失败：音乐
# ---------------------------------------------------------------------------

FAIL_MUSIC_AUDIO = "音乐音频下载失败，已重试仍未成功"
FAIL_MUSIC_NO_SOURCE = "音乐没有可用音源（接口未返回或受限）"

# ---------------------------------------------------------------------------
# 失败：直播 / 直播回放
# ---------------------------------------------------------------------------

FAIL_LIVE_ROOM_INFO = "直播间信息获取失败，请确认链接或重新登录"
FAIL_LIVE_NO_STREAM = "直播间未返回可用的直播流"
FAIL_LIVE_STREAM_REJECTED = "直播流请求被拒绝"
FAIL_LIVE_NO_DATA = "直播流没有数据（可能已下播）"
FAIL_LIVE_SAVE = "直播录制文件保存失败"
FAIL_REPLAY_MISSING_ID = "链接缺少回放 ID"
FAIL_REPLAY_NOT_FOUND = "回放不存在或已删除"
FAIL_REPLAY_NO_ROOM = "回放缺少直播间信息"
FAIL_REPLAY_NO_PLAYBACK = "未取到回放播放信息（可能未开放回放）"
FAIL_REPLAY_NO_VIDEO_TRACK = "回放没有可下载的视频轨"
FAIL_REPLAY_VIDEO_TRACK = "回放视频下载失败，已重试仍未成功"

# ---------------------------------------------------------------------------
# 失败：兜底
# ---------------------------------------------------------------------------

FAIL_WRITE_ERROR = "文件写入失败（磁盘空间不足或没有写入权限）"
FAIL_UNEXPECTED = "处理出错（详见后台日志）"
FAIL_UNSPECIFIED = "下载失败（未记录具体原因，详见后台日志）"


# 跳过类原因。结算时据此丢弃与状态不同类的记录：判定「已有」后继续补评论、
# 却在取详情时失败，失败行不能写「下载目录里已有该作品」。
SKIP_REASONS = frozenset(
    value for name, value in dict(globals()).items() if name.startswith("SKIP_")
)

_DISK_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, name, None) for name in ("ENOSPC", "EDQUOT", "EACCES", "EPERM", "EROFS")
    )
    if code is not None
)


def reason_for_exception(exc: BaseException) -> str:
    """条目处理中途抛出的异常 → 原因。只区分用户能自己处理的磁盘类错误。

    不能按 ``isinstance(exc, OSError)`` 判：``TimeoutError`` / ``ConnectionError``
    都是它的子类，网络故障会被说成「磁盘满了」。
    """
    if isinstance(exc, OSError) and exc.errno in _DISK_ERRNOS:
        return FAIL_WRITE_ERROR
    return FAIL_UNEXPECTED
