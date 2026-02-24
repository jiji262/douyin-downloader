#!/usr/bin/env python
# -*- coding: utf-8 -*-


import time
import copy


class Result(object):
    def __init__(self):
        # 作者信息
        self.authorDict = {
            "avatar_thumb": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "avatar": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "cover_url": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            # 喜欢的作品数
            "favoriting_count": "",
            # 粉丝数
            "follower_count": "",
            # 关注数
            "following_count": "",
            # 昵称
            "nickname": "",
            # 是否允许下载
            "prevent_download": "",
            # 用户 url id
            "sec_uid": "",
            # 是否私密账号
            "secret": "",
            # 短id
            "short_id": "",
            # 签名
            "signature": "",
            # 总获赞数
            "total_favorited": "",
            # 用户id
            "uid": "",
            # 用户自定义唯一id 抖音号
            "unique_id": "",
            # 年龄
            "user_age": "",

        }
        # 图片信息
        self.picDict = {
            "height": "",
            "mask_url_list": "",
            "uri": "",
            "url_list": [],
            "width": ""
        }
        # 音乐信息
        self.musicDict = {
            "cover_hd": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "cover_large": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "cover_medium": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "cover_thumb": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            # 音乐作者抖音号
            "owner_handle": "",
            # 音乐作者id
            "owner_id": "",
            # 音乐作者昵称
            "owner_nickname": "",
            "play_url": {
                "height": "",
                "uri": "",
                "url_key": "",
                "url_list": [],
                "width": ""
            },
            # 音乐名字
            "title": "",
        }
        # 视频信息
        self.videoDict = {
            "play_addr": {
                "uri": "",
                "url_list": [],
            },
            "cover_original_scale": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "dynamic_cover": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "origin_cover": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            },
            "cover": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": ""
            }
        }
        # mix信息
        self.mixInfo = {
            "cover_url": {
                "height": "",
                "uri": "",
                "url_list": [],
                "width": 720
            },
            "ids": "",
            "is_serial_mix": "",
            "mix_id": "",
            "mix_name": "",
            "mix_pic_type": "",
            "mix_type": "",
            "statis": {
                "current_episode": "",
                "updated_to_episode": ""
            }
        }
        # 作品信息
        self.awemeDict = {
            # 作品创建时间
            "create_time": "",
            # awemeType=0 视频, awemeType=1 图集, awemeType=2 直播
            "awemeType": "",
            # 作品 id
            "aweme_id": "",
            # 作者信息
            "author": self.authorDict,
            # 作品描述
            "desc": "",
            # 图片
            "images": [],
            # 音乐
            "music": self.musicDict,
            # 合集
            "mix_info": self.mixInfo,
            # 视频
            "video": self.videoDict,
            # 作品信息统计
            "statistics": {
                "admire_count": "",
                "collect_count": "",
                "comment_count": "",
                "digg_count": "",
                "play_count": "",
                "share_count": ""
            }
        }
        # 用户作品信息
        self.awemeList = []
        # 直播信息
        self.liveDict = {
            # awemeType=0 视频, awemeType=1 图集, awemeType=2 直播
            "awemeType": "",
            # 是否在播
            "status": "",
            # 直播标题
            "title": "",
            # 直播cover
            "cover": "",
            # 头像
            "avatar": "",
            # 观看人数
            "user_count": "",
            # 昵称
            "nickname": "",
            # sec_uid
            "sec_uid": "",
            # 直播间观看状态
            "display_long": "",
            # 推流
            "flv_pull_url": "",
            # 分区
            "partition": "",
            "sub_partition": "",
            # 最清晰的地址
            "flv_pull_url0": "",
        }



    # 将得到的json数据（dataRaw）精简成自己定义的数据（dataNew）
    # 转换得到的数据
    def dataConvert(self, awemeType, dataNew, dataRaw):
        for item in dataNew:
            try:
                # 作品创建时间
                if item == "create_time":
                    dataNew['create_time'] = time.strftime(
                        "%Y-%m-%d %H.%M.%S", time.localtime(dataRaw['create_time']))
                    continue
                # 设置 awemeType
                if item == "awemeType":
                    dataNew["awemeType"] = awemeType
                    continue
                # 当 解析的链接 是图片时
                if item == "images":
                    if awemeType == 1:
                        for image in dataRaw[item]:
                            for i in image:
                                self.picDict[i] = image[i]
                            # 字典要深拷贝
                            self.awemeDict["images"].append(copy.deepcopy(self.picDict))
                    continue
                # 当 解析的链接 是视频时
                if item == "video":
                    if awemeType == 0:
                        self.dataConvert(awemeType, dataNew[item], dataRaw[item])
                    continue
                # 将小头像放大
                if item == "avatar":
                    for i in dataNew[item]:
                        if i == "url_list":
                            for j in self.awemeDict["author"]["avatar_thumb"]["url_list"]:
                                dataNew[item][i].append(j.replace("100x100", "1080x1080"))
                        elif i == "uri":
                            dataNew[item][i] = self.awemeDict["author"]["avatar_thumb"][i].replace("100x100",
                                                                                                   "1080x1080")
                        else:
                            dataNew[item][i] = self.awemeDict["author"]["avatar_thumb"][i]
                    continue

                # 原来的json是[{}] 而我们的是 {}
                if item == "cover_url":
                    self.dataConvert(awemeType, dataNew[item], dataRaw[item][0])
                    continue

                # 根据 uri 获取 1080p 视频
                if item == "play_addr":
                    dataNew[item]["uri"] = dataRaw["bit_rate"][0]["play_addr"]["uri"]
                    # 使用 这个api 可以获得1080p
                    # dataNew[item]["url_list"] = "https://aweme.snssdk.com/aweme/v1/play/?video_id=%s&ratio=1080p&line=0" \
                    #                             % dataNew[item]["uri"]
                    dataNew[item]["url_list"] = copy.deepcopy(dataRaw["bit_rate"][0]["play_addr"]["url_list"])
                    continue

                # 常规 递归遍历 字典
                if isinstance(dataNew[item], dict):
                    self.dataConvert(awemeType, dataNew[item], dataRaw[item])
                else:
                    # 赋值
                    dataNew[item] = dataRaw[item]
            except Exception as e:
                # 删除这个警告, 总是让人误会出错了
                # print("[  警告  ]:转换数据时在接口中未找到 %s\r" % (item))
                pass

    def clearDict(self, data):
        for item in data:
            # 常规 递归遍历 字典
            if isinstance(data[item], dict):
                self.clearDict(data[item])
            elif isinstance(data[item], list):
                data[item] = []
            else:
                data[item] = ""


if __name__ == '__main__':
    pass
