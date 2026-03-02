import importlib.metadata
import tomllib
from pathlib import Path

try:
    __version__ = importlib.metadata.version('absranobedb')
except importlib.metadata.PackageNotFoundError:
    __version__ = '(Unknown Version)'
    try:
        with Path('/app/pyproject.toml').open('rb') as f:
            __version__ = tomllib.load(f)['project']['version']
    except (FileNotFoundError, KeyError):
        pass
