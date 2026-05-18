"""Shared pytest fixtures and import-time stubs.

moviepy requires ImageMagick at import time (not at call time), which is not
installed in the test environment. Stub it out at sys.modules level before any
test module triggers the import. Real tiktok_adapter behaviour is covered by
patching tiktok_adapter.upload_from_options in individual tests.
"""
import sys
from unittest.mock import MagicMock

for _mod in [
    "moviepy",
    "moviepy.editor",
    "moviepy.video",
    "moviepy.video.io",
    "moviepy.audio",
    "moviepy.audio.io",
    "moviepy.config",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
