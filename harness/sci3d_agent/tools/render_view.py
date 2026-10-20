def run_render_view(args: dict, bridge, asset_path: str) -> dict:
    return bridge.render_view(asset_path=asset_path, **args)
