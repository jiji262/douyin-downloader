__all__ = ["create_app"]

def create_app(config_path: str = "config.yml"):
    from web.app import create_app as _create_app
    return _create_app(config_path)
