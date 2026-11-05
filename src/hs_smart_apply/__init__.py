"""Smart preprocessor for herdr-spreader YAML layouts."""

from ._version import __version__
from .cli import expand_str, process_node

__all__ = ["__version__", "expand_str", "process_node"]
