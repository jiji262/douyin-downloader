from core.user_modes.base_strategy import BaseUserModeStrategy


class LikeUserModeStrategy(BaseUserModeStrategy):
    mode_name = "like"
    api_method_name = "get_user_like"
