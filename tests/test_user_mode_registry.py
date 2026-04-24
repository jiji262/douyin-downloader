from core.user_mode_registry import UserModeRegistry
from core.user_modes.like_strategy import LikeUserModeStrategy
from core.user_modes.collect_strategy import CollectUserModeStrategy
from core.user_modes.collect_mix_strategy import CollectMixUserModeStrategy
from core.user_modes.mix_strategy import MixUserModeStrategy
from core.user_modes.music_strategy import MusicUserModeStrategy
from core.user_modes.post_strategy import PostUserModeStrategy


def test_user_mode_registry_contains_default_modes():
    registry = UserModeRegistry()

    assert registry.get("post") is PostUserModeStrategy
    assert registry.get("like") is LikeUserModeStrategy
    assert registry.get("mix") is MixUserModeStrategy
    assert registry.get("music") is MusicUserModeStrategy
    assert registry.get("collect") is CollectUserModeStrategy
    assert registry.get("collectmix") is CollectMixUserModeStrategy
    assert registry.get("unknown") is None
