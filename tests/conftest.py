import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from hondalog.data import open_log
from hondalog.data.synth import write_synthetic


@pytest.fixture(scope="session")
def synth_path(tmp_path_factory):
    """A 10-minute multi-rate log shared by the read-only tests."""
    return write_synthetic(tmp_path_factory.mktemp("synth") / "synth.hlog", duration_s=600)


@pytest.fixture()
def synth(synth_path):
    with open_log(synth_path) as log:
        yield log
