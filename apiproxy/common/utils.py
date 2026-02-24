#!/usr/bin/env python
# -*- coding: utf-8 -*-


import random
import requests
import re
import os
import sys
import hashlib
import base64
import time

import apiproxy


class Utils(object):
    def __init__(self):
        pass

    def replaceStr(self, filenamestr: str):
        """
        替换非法字符，缩短字符长度，使其能成为文件名
        """
        # 匹配 汉字 字母 数字 空格
        match = "([0-9A-Za-z\u4e00-\u9fa5]+)"

        result = re.findall(match, filenamestr)

        result = "".join(result).strip()
        if len(result) > 20:
            result = result[:20]
        # 去除前后空格
        return result

    def resource_path(self, relative_path):
        if getattr(sys, 'frozen', False):  # 是否Bundle Resource
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def str2bool(self, v):
        if isinstance(v, bool):
            return v
        if v.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        elif v.lower() in ('no', 'false', 'f', 'n', '0'):
            return False
        else:
            return True

    def generate_random_str(self, randomlength=16):
        """
        根据传入长度产生随机字符串
        """
        random_str = ''
        base_str = 'ABCDEFGHIGKLMNOPQRSTUVWXYZabcdefghigklmnopqrstuvwxyz0123456789='
        length = len(base_str) - 1
        for _ in range(randomlength):
            random_str += base_str[random.randint(0, length)]
        return random_str

    # https://www.52pojie.cn/thread-1589242-1-1.html
    def getttwid(self):
        url = 'https://ttwid.bytedance.com/ttwid/union/register/'
        data = '{"region":"cn","aid":1768,"needFid":false,"service":"www.ixigua.com","migrate_info":{"ticket":"","source":"node"},"cbUrlProtocol":"https","union":true}'
        res = requests.post(url=url, data=data)

        for i, j in res.cookies.items():
            return j

    def getXbogus(self, payload, form='', ua=apiproxy.ua):
        xbogus = self.get_xbogus(payload, ua, form)
        params = payload + "&X-Bogus=" + xbogus
        return params

    def get_xbogus(self, payload, ua, form):
        short_str = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
        arr2 = self.get_arr2(payload, ua, form)

        garbled_string = self.get_garbled_string(arr2)

        xbogus = ""

        for i in range(0, 21, 3):
            char_code_num0 = garbled_string[i]
            char_code_num1 = garbled_string[i + 1]
            char_code_num2 = garbled_string[i + 2]
            base_num = char_code_num2 | char_code_num1 << 8 | char_code_num0 << 16
            str1 = short_str[(base_num & 16515072) >> 18]
            str2 = short_str[(base_num & 258048) >> 12]
            str3 = short_str[(base_num & 4032) >> 6]
            str4 = short_str[base_num & 63]
            xbogus += str1 + str2 + str3 + str4

        return xbogus

    def get_garbled_string(self, arr2):
        p = [
            arr2[0], arr2[10], arr2[1], arr2[11], arr2[2], arr2[12], arr2[3], arr2[13], arr2[4], arr2[14],
            arr2[5], arr2[15], arr2[6], arr2[16], arr2[7], arr2[17], arr2[8], arr2[18], arr2[9]
        ]

        char_array = [chr(i) for i in p]
        f = []
        f.extend([2, 255])
        tmp = ['ÿ']
        bytes_ = self._0x30492c(tmp, "".join(char_array))

        for i in range(len(bytes_)):
            f.append(bytes_[i])

        return f

    def get_arr2(self, payload, ua, form):
        salt_payload_bytes = hashlib.md5(hashlib.md5(payload.encode()).digest()).digest()
        salt_payload = [byte for byte in salt_payload_bytes]

        salt_form_bytes = hashlib.md5(hashlib.md5(form.encode()).digest()).digest()
        salt_form = [byte for byte in salt_form_bytes]

        ua_key = ['\u0000', '\u0001', '\u000e']
        salt_ua_bytes = hashlib.md5(base64.b64encode(self._0x30492c(ua_key, ua))).digest()
        salt_ua = [byte for byte in salt_ua_bytes]

        timestamp = int(time.time())
        canvas = 1489154074

        arr1 = [
            64,  # 固定
            0,  # 固定
            1,  # 固定
            14,  # 固定 这个还要再看一下，14,12,0都出现过
            salt_payload[14],  # payload 相关
            salt_payload[15],
            salt_form[14],  # form 相关
            salt_form[15],
            salt_ua[14],  # ua 相关
            salt_ua[15],
            (timestamp >> 24) & 255,
            (timestamp >> 16) & 255,
            (timestamp >> 8) & 255,
            (timestamp >> 0) & 255,
            (canvas >> 24) & 255,
            (canvas >> 16) & 255,
            (canvas >> 8) & 255,
            (canvas >> 0) & 255,
            64,  # 校验位
        ]

        for i in range(1, len(arr1) - 1):
            arr1[18] ^= arr1[i]

        arr2 = [arr1[0], arr1[2], arr1[4], arr1[6], arr1[8], arr1[10], arr1[12], arr1[14], arr1[16], arr1[18], arr1[1],
                arr1[3], arr1[5], arr1[7], arr1[9], arr1[11], arr1[13], arr1[15], arr1[17]]

        return arr2

    def _0x30492c(self, a, b):
        d = [i for i in range(256)]
        c = 0
        result = bytearray(len(b))

        for i in range(256):
            c = (c + d[i] + ord(a[i % len(a)])) % 256
            e = d[i]
            d[i] = d[c]
            d[c] = e

        t = 0
        c = 0

        for i in range(len(b)):
            t = (t + 1) % 256
            c = (c + d[t]) % 256
            e = d[t]
            d[t] = d[c]
            d[c] = e
            result[i] = ord(b[i]) ^ d[(d[t] + d[c]) % 256]

        return result


if __name__ == "__main__":
    pass
