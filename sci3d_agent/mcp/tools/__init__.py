from .catalog import TOOLS as _catalog
from .inspect import TOOLS as _inspect
from .pipeline_read import TOOLS as _pipe_r
from .pipeline_write import TOOLS as _pipe_w
from .render_static import TOOLS as _render
from .urls import TOOLS as _urls
from .vision3d import TOOLS as _vision3d

ALL_TOOLS = [*_catalog, *_urls, *_inspect, *_pipe_r, *_render, *_pipe_w, *_vision3d]
