#!/usr/bin/env python3
"""DUCMC Result Scraper — Local (Firefox) entry point.

Requires: Firefox + geckodriver, dependencies from requirements.txt.
InquirerPy is used for interactive prompts (fuzzy exam selector).
"""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

import scraper_common  # noqa: E402

scraper_common.configure(
    data_dir=DATA_DIR,
    credentials_file=CREDENTIALS_FILE,
    env_file=ENV_FILE,
    browser="firefox",
    use_inquirerpy=True,
)

if __name__ == "__main__":
    scraper_common.run()
