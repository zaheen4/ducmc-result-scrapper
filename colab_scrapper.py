#!/usr/bin/env python3
"""DUCMC Result Scraper — Google Colab entry point.

Upload this file AND scraper_common.py to Colab, then run:
  %run colab_scrapper.py

All prompts use plain input() (no InquirerPy — it hangs in Colab).
"""

import sys
import os
import subprocess

# --- Install dependencies ---
subprocess.run(["apt-get", "update"], check=True)
subprocess.run(
    ["pip", "install",
     "selenium==4.33.0", "gspread==6.2.1",
     "beautifulsoup4==4.13.4"],
    check=True,
)

# --- Install Google Chrome + matching ChromeDriver ---
_CHROME_DEB = "/tmp/chrome-stable.deb"
subprocess.run(
    ["wget", "-q", "-O", _CHROME_DEB,
     "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"],
    check=True,
)
subprocess.run(["apt-get", "install", "-y", "-qq", _CHROME_DEB], check=True)
_CHROME_VER = subprocess.check_output(
    ["google-chrome", "--version"], text=True
).strip().split()[-1]
subprocess.run(
    ["wget", "-q", "-O", "/tmp/chromedriver.zip",
     f"https://storage.googleapis.com/chrome-for-testing-public/{_CHROME_VER}/linux64/chromedriver-linux64.zip"],
    check=True,
)
subprocess.run(
    ["unzip", "-q", "-o", "/tmp/chromedriver.zip", "-d", "/tmp/chromedriver"],
    check=True,
)
subprocess.run(
    ["cp", "/tmp/chromedriver/chromedriver-linux64/chromedriver", "/usr/local/bin/chromedriver"],
    check=True,
)

# --- Mount Google Drive ---
from google.colab import drive  # pyright: ignore[reportMissingImports]  # noqa: E402

if not os.path.exists("/content/drive/MyDrive"):
    drive.mount("/content/drive")
else:
    print("[INFO] Google Drive already mounted")

DATA_DIR = "/content/drive/MyDrive/ResultScraperData"
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
ENV_FILE = os.path.join(DATA_DIR, ".env")

if not os.path.exists(CREDENTIALS_FILE):
    print(f"[ERROR] credentials.json not found at {CREDENTIALS_FILE}")
    print("Please place it in Google Drive → ResultScraperData/")
    sys.exit(1)

import scraper_common  # noqa: E402

scraper_common.configure(
    data_dir=DATA_DIR,
    credentials_file=CREDENTIALS_FILE,
    env_file=ENV_FILE,
    browser="chrome",
    use_inquirerpy=False,
)

if __name__ == "__main__":
    scraper_common.run()
