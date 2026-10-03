"""
Tests for scripts/setup_peloton_oauth.py's authorization-code input
handling (issue #7 follow-up bug). Live use found that most people paste
the full redirect URL from the address bar rather than hand-extracting the
'code' query parameter, which the original strip()-only handling submitted
verbatim as an invalid authorization code. Only `_extract_authorization_code`
is exercised here — the rest of the script talks to a real browser/Peloton
account and can only be verified by running it directly, per its own
module docstring.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup_peloton_oauth.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("setup_peloton_oauth", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


def test_bare_code_passed_through_unchanged():
    assert script._extract_authorization_code("abc123XYZ") == "abc123XYZ"


def test_bare_code_with_surrounding_whitespace_is_stripped():
    assert script._extract_authorization_code("  abc123XYZ  \n") == "abc123XYZ"


def test_full_redirect_url_extracts_code_query_param():
    url = "https://members.onepeloton.com/callback?code=abc123XYZ&state=xyz"
    assert script._extract_authorization_code(url) == "abc123XYZ"


def test_full_redirect_url_with_only_code_param():
    url = "https://members.onepeloton.com/callback?code=abc123XYZ"
    assert script._extract_authorization_code(url) == "abc123XYZ"


def test_full_redirect_url_regardless_of_param_order():
    url = "https://members.onepeloton.com/callback?state=xyz&code=abc123XYZ"
    assert script._extract_authorization_code(url) == "abc123XYZ"


def test_empty_input_returns_empty_string():
    assert script._extract_authorization_code("   ") == ""
