import sys
import os
import json
import types
from unittest.mock import MagicMock

# Add hermes source directory to sys.path
hermes_dir = os.path.join(os.path.dirname(__file__), '..', 'hermes')
sys.path.insert(0, hermes_dir)

# Mock the secrets module before any hermes imports
mock_secrets = types.ModuleType('hermes_utils.secrets')
mock_secrets.TOKEN = "fake-token-for-testing"
mock_secrets.DB = {
    "database": "test_db",
    "host": "localhost",
    "user": "test_user",
    "password": "test_pass",
    "port": "5432"
}
mock_secrets.WORKDIR = "/tmp/"
sys.modules['hermes_utils.secrets'] = mock_secrets

# Prevent logging_config from writing to /data/hermes.log during tests
import logging
import hermes_utils.logging_config as _logging_config
_logging_config._initialized = True
logging.basicConfig(level=logging.DEBUG)

# Mock telegram.Bot to avoid network calls during import of meta.py
import telegram
telegram.Bot = MagicMock()

import pytest
import requests


@pytest.fixture
def mock_response():
    """Factory fixture that creates mock requests.models.Response objects."""
    def _make(content, status_code=200):
        r = MagicMock(spec=requests.models.Response)
        # Commonly used by parsers for content-type sniffing.
        r.headers = {}
        if isinstance(content, dict) or isinstance(content, list):
            r.content = json.dumps(content).encode('utf-8')
        elif isinstance(content, str):
            r.content = content.encode('utf-8')
        elif isinstance(content, bytes):
            r.content = content
        else:
            r.content = str(content).encode('utf-8')
        r.status_code = status_code
        return r
    return _make
