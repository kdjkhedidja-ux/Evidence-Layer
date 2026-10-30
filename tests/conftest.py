import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_INTEGRATION_DIR = Path(__file__).resolve().parent / "integration"


def pytest_ignore_collect(collection_path, config):
    # Default pytest is the unit suite; live tests require their disposable runner.
    if Path(collection_path).resolve() != _INTEGRATION_DIR:
        return None
    for argument in config.invocation_params.args:
        if argument.startswith("-"):
            continue
        requested = Path(argument)
        if not requested.exists():
            continue
        requested = requested.resolve()
        if (
            requested == _INTEGRATION_DIR
            or _INTEGRATION_DIR in requested.parents
            or requested in _INTEGRATION_DIR.parents
        ):
            return None
    return True
