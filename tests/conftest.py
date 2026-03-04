"""
Pytest configuration and shared fixtures.
"""

import pytest


# Mark async tests to use asyncio
pytest_plugins = ["pytest_asyncio"]
