import os
from dotenv import load_dotenv

load_dotenv()

LOCODE_CSV_PATH = os.getenv("LOCODE_CSV_PATH", "data/us_locode.csv")
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "85"))

# NCM
NCM_URL = os.getenv("NCM_URL", "https://www.cradlepointecm.com")
NCM_USERNAME = os.getenv("NCM_USERNAME", "")
NCM_PASSWORD = os.getenv("NCM_PASSWORD", "")

# Playwright
HEADLESS = os.getenv("CONSOLE_CHECK_HEADLESS", "false").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("CONSOLE_CHECK_TIMEOUT", "30000"))  # ms
