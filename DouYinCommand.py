#!/usr/bin/env python
# -*- coding: utf-8 -*-


import argparse
import os
import sys
import json
import yaml
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path
import logging

# 配置logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
# 改名为douyin_logger以避免冲突
douyin_logger = logging.getLogger("DouYin")

# 现在可以安全使用douyin_logger
try:
    import asyncio
    import aiohttp
    ASYNC_SUPPORT = True
except ImportError:
    ASYNC_SUPPORT = False
    douyin_logger.warning("aiohttp 未安装，异步下载功能不可用")

from apiproxy.douyin.douyin import Douyin
from apiproxy.douyin.download import Download
from apiproxy.douyin import douyin_headers
from apiproxy.common import utils

@dataclass
class DownloadConfig:
    """下载配置类"""
    link: List[str]
    path: Path
    music: bool = True
    cover: bool = True
    avatar: bool = True
    json: bool = True
    start_time: str = ""
    end_time: str = ""
    folderstyle: bool = True
    mode: List[str] = field(default_factory=lambda: ["post"])
    thread: int = 5
    cookie: Optional[str] = None
    database: bool = True
    number: Dict[str, int] = field(default_factory=lambda: {
        "post": 0, "like": 0, "allmix": 0, "mix": 0, "music": 0
    })
    increase: Dict[str, bool] = field(default_factory=lambda: {
        "post": False, "like": False, "allmix": False, "mix": False, "music": False
    })
    
    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "DownloadConfig":
        """从YAML文件加载配置"""
        # 实现YAML配置加载逻辑
        
    @classmethod 
    def from_args(cls, args) -> "DownloadConfig":
        """从命令行参数加载配置"""
        # 实现参数加载逻辑
        
    def validate(self) -> bool:
        """验证配置有效性"""
        # 实现验证逻辑

configModel = {
    "link": [],
    "path": os.getcwd(),
    "music": True,
    "cover": True,
    "avatar": True,
    "json": True,
    "start_time": "",
    "end_time": "",
    "folderstyle": True,
    "mode": ["post"],
    "number": {
        "post": 0,
        "like": 0,
        "allmix": 0,
        "mix": 0,
        "music": 0,
    },
    'database': True,
    "increase": {
        "post": False,
        "like": False,
        "allmix": False,
        "mix": False,
        "music": False,
    },
    "thread": 5,
    "cookie": os.environ.get("DOUYIN_COOKIE", "")
}

def argument():
    parser = argparse.ArgumentParser(description='抖音批量下载工具 使用帮助')
    parser.add_argument("--cmd", "-C", help="使用命令行(True)或者配置文件(False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--link", "-l",
                        help="作品(视频或图集)、直播、合集、音乐集合、个人主页的分享链接或者电脑浏览器网址, 可以设置多个链接(删除文案, 保证只有URL, https://v.douyin.com/kcvMpuN/ 或者 https://www.douyin.com/开头的)",
                        type=str, required=False, default=[], action="append")
    parser.add_argument("--path", "-p", help="下载保存位置, 默认当前文件位置",
                        type=str, required=False, default=os.getcwd())
    parser.add_argument("--music", "-m", help="是否下载视频中的音乐(True/False), 默认为True",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--cover", "-c", help="是否下载视频的封面(True/False), 默认为True, 当下载视频时有效",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--avatar", "-a", help="是否下载作者的头像(True/False), 默认为True",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--json", "-j", help="是否保存获取到的数据(True/False), 默认为True",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--folderstyle", "-fs", help="文件保存风格, 默认为True",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--mode", "-M", help="link是个人主页时, 设置下载发布的作品(post)或喜欢的作品(like)或者用户所有合集(mix), 默认为post, 可以设置多种模式",
                        type=str, required=False, default=[], action="append")
    parser.add_argument("--postnumber", help="主页下作品下载个数设置, 默认为0 全部下载",
                        type=int, required=False, default=0)
    parser.add_argument("--likenumber", help="主页下喜欢下载个数设置, 默认为0 全部下载",
                        type=int, required=False, default=0)
    parser.add_argument("--allmixnumber", help="主页下合集下载个数设置, 默认为0 全部下载",
                        type=int, required=False, default=0)
    parser.add_argument("--mixnumber", help="单个合集下作品下载个数设置, 默认为0 全部下载",
                        type=int, required=False, default=0)
    parser.add_argument("--musicnumber", help="音乐(原声)下作品下载个数设置, 默认为0 全部下载",
                        type=int, required=False, default=0)
    parser.add_argument("--database", "-d", help="是否使用数据库, 默认为True 使用数据库; 如果不使用数据库, 增量更新不可用",
                        type=utils.str2bool, required=False, default=True)
    parser.add_argument("--postincrease", help="是否开启主页作品增量下载(True/False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--likeincrease", help="是否开启主页喜欢增量下载(True/False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--allmixincrease", help="是否开启主页合集增量下载(True/False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--mixincrease", help="是否开启单个合集下作品增量下载(True/False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--musicincrease", help="是否开启音乐(原声)下作品增量下载(True/False), 默认为False",
                        type=utils.str2bool, required=False, default=False)
    parser.add_argument("--thread", "-t",
                        help="设置线程数, 默认5个线程",
                        type=int, required=False, default=5)
    parser.add_argument("--cookie", help="设置cookie, 格式: \"name1=value1; name2=value2;\" 注意要加冒号",
                        type=str, required=False, default='')
    parser.add_argument("--config", "-F", 
                       type=argparse.FileType('r', encoding='utf-8'),
                       help="配置文件路径")
    args = parser.parse_args()
    if args.thread <= 0:
        args.thread = 5

    return args


def yamlConfig():
    curPath = os.path.dirname(os.path.realpath(sys.argv[0]))
    yamlPath = os.path.join(curPath, "config.yml")
    
    try:
        with open(yamlPath, 'r', encoding='utf-8') as f:
            configDict = yaml.safe_load(f)
            
        # 使用字典推导式简化配置更新
        for key in configModel:
            if key in configDict:
                if isinstance(configModel[key], dict):
                    configModel[key].update(configDict[key] or {})
                else:
                    configModel[key] = configDict[key]
                    
        # 特殊处理cookie
        if configDict.get("cookies"):
            cookieStr = "; ".join(f"{k}={v}" for k,v in configDict["cookies"].items())
            configModel["cookie"] = cookieStr
            
        # 特殊处理end_time
        if configDict.get("end_time") == "now":
                configModel["end_time"] = time.strftime("%Y-%m-%d", time.localtime())
            
    except FileNotFoundError:
        douyin_logger.warning("未找到配置文件config.yml")
    except Exception as e:
        douyin_logger.warning(f"配置文件解析出错: {str(e)}")


def validate_config(config: dict) -> bool:
    """验证配置有效性"""
    required_keys = {
        'link': list,
        'path': str,
        'thread': int
    }
    
    for key, typ in required_keys.items():
        if key not in config or not isinstance(config[key], typ):
            douyin_logger.error(f"无效配置项: {key}")
            return False
            
    if not all(isinstance(url, str) for url in config['link']):
        douyin_logger.error("链接配置格式错误")
        return False
        
    return True


def main():
    start = time.time()

    # 配置初始化
    args = argument()
    if args.cmd:
        update_config_from_args(args)
    else:
        yamlConfig()

    if not validate_config(configModel):
        return

    if not configModel["link"]:
        douyin_logger.error("未设置下载链接")
        return

    # Cookie处理
    if configModel["cookie"]:
        douyin_headers["Cookie"] = configModel["cookie"]

    # 路径处理
    configModel["path"] = os.path.abspath(configModel["path"])
    os.makedirs(configModel["path"], exist_ok=True)
    douyin_logger.info(f"数据保存路径 {configModel['path']}")

    # 初始化下载器
    dy = Douyin(database=configModel["database"])
    dl = Download(
        thread=configModel["thread"],
        music=configModel["music"],
        cover=configModel["cover"],
        avatar=configModel["avatar"],
        resjson=configModel["json"],
        folderstyle=configModel["folderstyle"]
    )

    # 处理每个链接
    for link in configModel["link"]:
        process_link(dy, dl, link)

    # 计算耗时
    duration = time.time() - start
    douyin_logger.info(f'\n[下载完成]:总耗时: {int(duration/60)}分钟{int(duration%60)}秒\n')


def process_link(dy, dl, link):
    """处理单个链接的下载逻辑"""
    douyin_logger.info("-" * 80)
    douyin_logger.info(f"[  提示  ]:正在请求的链接: {link}")
    
    try:
        url = dy.getShareLink(link)
        key_type, key = dy.getKey(url)
        
        handlers = {
            "user": handle_user_download,
            "mix": handle_mix_download,
            "music": handle_music_download,
            "aweme": handle_aweme_download,
            "live": handle_live_download
        }
        
        handler = handlers.get(key_type)
        if handler:
            handler(dy, dl, key)
        else:
            douyin_logger.warning(f"[  警告  ]:未知的链接类型: {key_type}")
    except Exception as e:
        douyin_logger.error(f"处理链接时出错: {str(e)}")


def handle_user_download(dy, dl, key):
    """处理用户主页下载"""
    douyin_logger.info("[  提示  ]:正在请求用户主页下作品")
    data = dy.getUserDetailInfo(sec_uid=key)
    nickname = ""
    if data and data.get('user'):
        nickname = utils.replaceStr(data['user']['nickname'])

    userPath = os.path.join(configModel["path"], f"user_{nickname}_{key}")
    os.makedirs(userPath, exist_ok=True)

    for mode in configModel["mode"]:
        douyin_logger.info("-" * 80)
        douyin_logger.info(f"[  提示  ]:正在请求用户主页模式: {mode}")
        
        if mode in ('post', 'like'):
            _handle_post_like_mode(dy, dl, key, mode, userPath)
        elif mode == 'mix':
            _handle_mix_mode(dy, dl, key, userPath)

def _handle_post_like_mode(dy, dl, key, mode, userPath):
    """处理发布/喜欢模式的下载"""
    datalist = dy.getUserInfo(
        key, 
        mode, 
        35, 
        configModel["number"][mode], 
        configModel["increase"][mode],
        start_time=configModel.get("start_time", ""),
        end_time=configModel.get("end_time", "")
    )
    
    if not datalist:
        return
        
    modePath = os.path.join(userPath, mode)
    os.makedirs(modePath, exist_ok=True)
    
    dl.userDownload(awemeList=datalist, savePath=modePath)

def _handle_mix_mode(dy, dl, key, userPath):
    """处理合集模式的下载"""
    mixIdNameDict = dy.getUserAllMixInfo(key, 35, configModel["number"]["allmix"])
    if not mixIdNameDict:
        return

    modePath = os.path.join(userPath, "mix")
    os.makedirs(modePath, exist_ok=True)

    for mix_id, mix_name in mixIdNameDict.items():
        douyin_logger.info(f'[  提示  ]:正在下载合集 [{mix_name}] 中的作品')
        mix_file_name = utils.replaceStr(mix_name)
        datalist = dy.getMixInfo(
            mix_id, 
            35, 
            0, 
            configModel["increase"]["allmix"], 
            key,
            start_time=configModel.get("start_time", ""),
            end_time=configModel.get("end_time", "")
        )
        
        if datalist:
            dl.userDownload(awemeList=datalist, savePath=os.path.join(modePath, mix_file_name))
            douyin_logger.info(f'[  提示  ]:合集 [{mix_name}] 中的作品下载完成')

def handle_mix_download(dy, dl, key):
    """处理单个合集下载"""
    douyin_logger.info("[  提示  ]:正在请求单个合集下作品")
    try:
        datalist = dy.getMixInfo(
            key, 
            35, 
            configModel["number"]["mix"], 
            configModel["increase"]["mix"], 
            "",
            start_time=configModel.get("start_time", ""),
            end_time=configModel.get("end_time", "")
        )
        
        if not datalist:
            douyin_logger.error("获取合集信息失败")
            return
            
        mixname = utils.replaceStr(datalist[0]["mix_info"]["mix_name"])
        mixPath = os.path.join(configModel["path"], f"mix_{mixname}_{key}")
        os.makedirs(mixPath, exist_ok=True)
        dl.userDownload(awemeList=datalist, savePath=mixPath)
    except Exception as e:
        douyin_logger.error(f"处理合集时出错: {str(e)}")

def handle_music_download(dy, dl, key):
    """处理音乐作品下载"""
    douyin_logger.info("[  提示  ]:正在请求音乐(原声)下作品")
    datalist = dy.getMusicInfo(key, 35, configModel["number"]["music"], configModel["increase"]["music"])

    if datalist:
        musicname = utils.replaceStr(datalist[0]["music"]["title"])
        musicPath = os.path.join(configModel["path"], f"music_{musicname}_{key}")
        os.makedirs(musicPath, exist_ok=True)
        dl.userDownload(awemeList=datalist, savePath=musicPath)

def handle_aweme_download(dy, dl, key):
    """处理单个作品下载"""
    douyin_logger.info("[  提示  ]:正在请求单个作品")
    
    # 最大重试次数
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            douyin_logger.info(f"[  提示  ]:第 {retry_count+1} 次尝试获取作品信息")
            result = dy.getAwemeInfo(key)
            
            if not result:
                douyin_logger.error("[  错误  ]:获取作品信息失败")
                retry_count += 1
                if retry_count < max_retries:
                    douyin_logger.info("[  提示  ]:等待 5 秒后重试...")
                    time.sleep(5)
                continue
            
            # 直接使用返回的字典，不需要解包
            datanew = result
            
            if datanew:
                awemePath = os.path.join(configModel["path"], "aweme")
                os.makedirs(awemePath, exist_ok=True)
                
                # 下载前检查视频URL
                video_url = datanew.get("video", {}).get("play_addr", {}).get("url_list", [])
                if not video_url or len(video_url) == 0:
                    douyin_logger.error("[  错误  ]:无法获取视频URL")
                    retry_count += 1
                    if retry_count < max_retries:
                        douyin_logger.info("[  提示  ]:等待 5 秒后重试...")
                        time.sleep(5)
                    continue
                    
                douyin_logger.info(f"[  提示  ]:获取到视频URL，准备下载")
                dl.userDownload(awemeList=[datanew], savePath=awemePath)
                douyin_logger.info(f"[  成功  ]:视频下载完成")
                return True
            else:
                douyin_logger.error("[  错误  ]:作品数据为空")
                
            retry_count += 1
            if retry_count < max_retries:
                douyin_logger.info("[  提示  ]:等待 5 秒后重试...")
                time.sleep(5)
                
        except Exception as e:
            douyin_logger.error(f"[  错误  ]:处理作品时出错: {str(e)}")
            retry_count += 1
            if retry_count < max_retries:
                douyin_logger.info("[  提示  ]:等待 5 秒后重试...")
                time.sleep(5)
    
    douyin_logger.error("[  失败  ]:已达到最大重试次数，无法下载视频")

def handle_live_download(dy, dl, key):
    """处理直播下载"""
    douyin_logger.info("[  提示  ]:正在进行直播解析")
    live_json = dy.getLiveInfo(key)
    
    if configModel["json"] and live_json:
        livePath = os.path.join(configModel["path"], "live")
        os.makedirs(livePath, exist_ok=True)
        
        live_file_name = utils.replaceStr(f"{key}{live_json['nickname']}")
        json_path = os.path.join(livePath, f"{live_file_name}.json")
        
        douyin_logger.info("[  提示  ]:正在保存获取到的信息到result.json")
        with open(json_path, "w", encoding='utf-8') as f:
            json.dump(live_json, f, ensure_ascii=False, indent=2)

# 条件定义异步函数
if ASYNC_SUPPORT:
    async def download_file(url, path):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    with open(path, 'wb') as f:
                        f.write(await response.read())
                    return True
        return False

def update_config_from_args(args):
    """从命令行参数更新配置"""
    configModel["link"] = args.link
    configModel["path"] = args.path
    configModel["music"] = args.music
    configModel["cover"] = args.cover
    configModel["avatar"] = args.avatar
    configModel["json"] = args.json
    configModel["folderstyle"] = args.folderstyle
    configModel["mode"] = args.mode if args.mode else ["post"]
    configModel["thread"] = args.thread
    configModel["cookie"] = args.cookie
    configModel["database"] = args.database
    
    # 更新number字典
    configModel["number"]["post"] = args.postnumber
    configModel["number"]["like"] = args.likenumber
    configModel["number"]["allmix"] = args.allmixnumber
    configModel["number"]["mix"] = args.mixnumber
    configModel["number"]["music"] = args.musicnumber
    
    # 更新increase字典
    configModel["increase"]["post"] = args.postincrease
    configModel["increase"]["like"] = args.likeincrease
    configModel["increase"]["allmix"] = args.allmixincrease
    configModel["increase"]["mix"] = args.mixincrease
    configModel["increase"]["music"] = args.musicincrease

if __name__ == "__main__":
    main()
