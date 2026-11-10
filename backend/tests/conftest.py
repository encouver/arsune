"""Shared test setup. Pytest loads this file before any test module imports the app."""

import os

# The suite must give the same result with and without a developer's .env (CI has none).
# Settings still come from ARSUNE_* environment variables. Subprocesses inherit this value.
os.environ["ARSUNE_ENV_FILE"] = ""
