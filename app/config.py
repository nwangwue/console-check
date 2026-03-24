import os

DATABASE_PATH = os.getenv("DATABASE_PATH", "console_check.db")
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
LOCODE_CSV_PATH = os.getenv("LOCODE_CSV_PATH", "data/us_locode.csv")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "85"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
