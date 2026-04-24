from __future__ import annotations

from typing import Dict, Optional, Type

from core.user_modes import (
    BaseUserModeStrategy,
    CollectMixUserModeStrategy,
    CollectUserModeStrategy,
    LikeUserModeStrategy,
    MixUserModeStrategy,
    MusicUserModeStrategy,
    PostUserModeStrategy,
)


class UserModeRegistry:
    def __init__(self):
        self._registry: Dict[str, Type[BaseUserModeStrategy]] = {
            "post": PostUserModeStrategy,
            "like": LikeUserModeStrategy,
            "mix": MixUserModeStrategy,
            "music": MusicUserModeStrategy,
            "collect": CollectUserModeStrategy,
            "collectmix": CollectMixUserModeStrategy,
        }

    def get(self, mode: str) -> Optional[Type[BaseUserModeStrategy]]:
        return self._registry.get((mode or "").strip())

    def register(self, mode: str, strategy_cls: Type[BaseUserModeStrategy]) -> None:
        self._registry[(mode or "").strip()] = strategy_cls

    def all_modes(self):
        return sorted(self._registry.keys())
