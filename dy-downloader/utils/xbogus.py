# ==============================================================================
# Copyright (C) 2021 Evil0ctal
#
# This file is part of the Douyin_TikTok_Download_API project.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import base64
import hashlib
import time
from typing import List, Optional, Tuple, Union


class XBogus:
    def __init__(self, user_agent: Optional[str] = None) -> None:
        # fmt: off
        self._array = [
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            0, 1, 2, 3, 4, 5, 6, 7, 8, 9, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None,
            None, None, None, None, None, None, None, None, None, None, None, None, 10, 11, 12, 13, 14, 15
        ]
        self._character = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
        # fmt: on
        self._ua_key = b"\x00\x01\x0c"
        self._user_agent = (
            user_agent
            if user_agent
            else (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        )

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def _md5_str_to_array(self, md5_str: str) -> List[int]:
        if isinstance(md5_str, str) and len(md5_str) > 32:
            return [ord(char) for char in md5_str]

        array: List[int] = []
        idx = 0
        while idx < len(md5_str):
            array.append(
                (self._array[ord(md5_str[idx])] << 4)
                | self._array[ord(md5_str[idx + 1])]
            )
            idx += 2
        return array

    def _md5(self, input_data: Union[str, List[int]]) -> str:
        if isinstance(input_data, str):
            data = self._md5_str_to_array(input_data)
        else:
            data = input_data
        md5_hash = hashlib.md5()
        md5_hash.update(bytes(data))
        return md5_hash.hexdigest()

    def _md5_encrypt(self, url_path: str) -> List[int]:
        hashed = self._md5(self._md5_str_to_array(self._md5(url_path)))
        return self._md5_str_to_array(hashed)

    def _encoding_conversion(
        self, a, b, c, e, d, t, f, r, n, o, i, _, x, u, s, l, v, h, p
    ) -> str:
        payload = [a]
        payload.append(int(i))
        payload.extend([b, _, c, x, e, u, d, s, t, l, f, v, r, h, n, p, o])
        return bytes(payload).decode("ISO-8859-1")

    def _encoding_conversion2(self, a: int, b: int, c: str) -> str:
        return chr(a) + chr(b) + c

    @staticmethod
    def _rc4_encrypt(key: bytes, data: bytes) -> bytearray:
        s = list(range(256))
        j = 0
        encrypted = bytearray()

        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) % 256
            s[i], s[j] = s[j], s[i]

        i = j = 0
        for byte in data:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            encrypted.append(byte ^ s[(s[i] + s[j]) % 256])

        return encrypted

    def _calculation(self, a1: int, a2: int, a3: int) -> str:
        x3 = ((a1 & 255) << 16) | ((a2 & 255) << 8) | (a3 & 255)
        return (
            self._character[(x3 & 16515072) >> 18]
            + self._character[(x3 & 258048) >> 12]
            + self._character[(x3 & 4032) >> 6]
            + self._character[x3 & 63]
        )

    def build(self, url: str) -> Tuple[str, str, str]:
        ua_md5_array = self._md5_str_to_array(
            self._md5(
                base64.b64encode(
                    self._rc4_encrypt(
                        self._ua_key, self._user_agent.encode("ISO-8859-1")
                    )
                ).decode("ISO-8859-1")
            )
        )

        empty_md5_array = self._md5_str_to_array(
            self._md5(self._md5_str_to_array("d41d8cd98f00b204e9800998ecf8427e"))
        )
        url_md5_array = self._md5_encrypt(url)

        timer = int(time.time())
        ct = 536919696

        new_array = [
            64,
            0.00390625,
            1,
            12,
            url_md5_array[14],
            url_md5_array[15],
            empty_md5_array[14],
            empty_md5_array[15],
            ua_md5_array[14],
            ua_md5_array[15],
            timer >> 24 & 255,
            timer >> 16 & 255,
            timer >> 8 & 255,
            timer & 255,
            ct >> 24 & 255,
            ct >> 16 & 255,
            ct >> 8 & 255,
            ct & 255,
        ]

        xor_result = new_array[0]
        for value in new_array[1:]:
            if isinstance(value, float):
                value = int(value)
            xor_result ^= value
        new_array.append(xor_result)

        array3: list[int] = []
        array4: list[int] = []
        idx = 0
        while idx < len(new_array):
            value = new_array[idx]
            array3.append(value)
            if idx + 1 < len(new_array):
                array4.append(new_array[idx + 1])
            idx += 2

        merged = array3 + array4

        garbled = self._encoding_conversion2(
            2,
            255,
            self._rc4_encrypt(
                "ÿ".encode("ISO-8859-1"),
                self._encoding_conversion(*merged).encode("ISO-8859-1"),
            ).decode("ISO-8859-1"),
        )

        xb = ""
        idx = 0
        while idx < len(garbled):
            xb += self._calculation(
                ord(garbled[idx]),
                ord(garbled[idx + 1]),
                ord(garbled[idx + 2]),
            )
            idx += 3

        signed_url = f"{url}&X-Bogus={xb}"
        return signed_url, xb, self._user_agent


def generate_x_bogus(url: str, user_agent: Optional[str] = None) -> Tuple[str, str, str]:
    signer = XBogus(user_agent=user_agent)
    return signer.build(url)
