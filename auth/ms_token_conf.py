"""Bundled snapshot of F2's Douyin msToken generation config.

Source: ``f2.douyin.msToken`` in
https://raw.githubusercontent.com/Johnserf-Seed/f2/main/f2/conf/conf.yaml
(captured 2026-09-06).

:class:`auth.ms_token_manager.MsTokenManager` prefers the live remote config and
only reads this snapshot when GitHub is unreachable inside its short probe budget.
Without a fallback, every client on a network that cannot reach
raw.githubusercontent.com quickly ended up on a random msToken, and Douyin now
answers HTTP 403 to ``/aweme/v1/web/aweme/post/`` after a few pages with such a
token, so profile downloads silently stopped early.

Refresh (only needed if mssdk starts rejecting this payload)::

    curl -sS https://raw.githubusercontent.com/Johnserf-Seed/f2/main/f2/conf/conf.yaml \
      -o /tmp/f2conf.yaml
    python -c "import yaml; print(yaml.safe_load(open('/tmp/f2conf.yaml'))['f2']['douyin']['msToken'])"

then paste the six fields below.
"""

from __future__ import annotations

from typing import Any, Dict

BUNDLED_MS_TOKEN_CONF: Dict[str, Any] = {
    "url": (
        "https://mssdk.bytedance.com/web/r/token?ms_appid=6383&msToken=T4bNG9W2rKF7hBNwaYssDE"
        "rnJEobDAk641DFaOn4hcsfAM8slpbZeKPM4Ml4rhDQq18iY8nQ0JR3J87SLZtDiDqtZdZawfBjCWAgtolQso"
        "EtG6MLETvo4fwr7F28zGJUFDdJgKEZHibNR0QshVBv28ygsQsJDzerKAtsgj9Pn5WsxyS1vfkiX3I%3D"
    ),
    "magic": 538969122,
    "version": 1,
    "dataType": 8,
    "ulr": 0,
    "strData": (
        "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"
        "p0uF9MwDpyu="
    ),
}
