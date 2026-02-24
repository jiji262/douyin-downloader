#!/usr/bin/env python
# -*- coding: utf-8 -*-

import apiproxy
from apiproxy.common import utils

douyin_headers = {
    'User-Agent': apiproxy.ua,
    'referer': 'https://www.douyin.com/',
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'accept-encoding': 'gzip, deflate, br',
    'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin'
    # Cookie字段将在运行时动态设置
}
