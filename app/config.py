import os

DATABASE_PATH = os.getenv("DATABASE_PATH", "console_check.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
LOCODE_CSV_PATH = os.getenv("LOCODE_CSV_PATH", "data/us_locode.csv")
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "85"))
