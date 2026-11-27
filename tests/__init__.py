"""Lets a test module run on its own as tests.test_<name>, as `python3 -m
unittest tests/test_<name>.py` from the repository root imports it: each test
module imports process_guard, which lives here, by that plain name."""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
