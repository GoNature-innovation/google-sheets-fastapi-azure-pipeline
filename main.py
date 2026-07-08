from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials
import os
import json
import time
import tempfile
import threading
import traceback

app = FastAPI()

SPREADSHEET_NAME = "Copy of SILVERMEMBERSHIP SALES SHEET (L1) | H.I.G.H RAZORPAY 2025-26"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]


def get_google_credentials():
    # Local system
    if os.path.exists("service_account.json"):
        return Credentials.from_service_account_file(
            "service_account.json",
            scopes=SCOPES
        )

    # Azure App Service
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        service_account_info = json.loads(
            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
        )

        return Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES
        )

    raise Exception("Google service account credentials not found.")


creds = get_google_credentials()
client = gspread.authorize(creds)

CACHE_DATA = []
CACHE_TIME = 0
CACHE_SECONDS = 1800

# Shared on-disk cache so multiple workers/processes reuse one another's
# fetches instead of each hitting the Google API independently.
CACHE_FILE = os.environ.get(
    "BUYERS_CACHE_FILE",
    os.path.join(tempfile.gettempdir(), "buyers_cache.json")
)

# Serialize refreshes so concurrent requests don't all hit the API at once
# (prevents a "cache stampede" that quickly exhausts the Google quota).
_LOAD_LOCK = threading.Lock()


def _read_shared_cache():
    """Return (data, timestamp) from the shared file, or (None, 0)."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("data", []), payload.get("time", 0)
    except (OSError, ValueError):
        return None, 0


def _write_shared_cache(data, timestamp):
    """Atomically write the shared cache file (safe under concurrent workers)."""
    payload = {"time": timestamp, "data": data}
    tmp_path = f"{CACHE_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, CACHE_FILE)  # atomic on Windows and POSIX
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

# Resolve and reuse a single Spreadsheet object. Opening by name uses the
# Drive API and pages through the whole file list on every call, which is a
# major quota sink; we resolve it once and reuse it for the process lifetime.
_SPREADSHEET = None


@app.get("/")
def home():
    return {
        "message": "FastAPI Google Sheets API is running"
    }


def _with_retry(func, retries=5, base_delay=2):
    """Call a gspread function, retrying with exponential backoff on 429."""
    for attempt in range(retries):
        try:
            return func()
        except APIError as e:
            status = getattr(e.response, "status_code", None)
            # 429 = RESOURCE_EXHAUSTED (rate limit), 503 = transient backend
            if status in (429, 503) and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise


def get_spreadsheet():
    global _SPREADSHEET
    if _SPREADSHEET is None:
        _SPREADSHEET = _with_retry(lambda: client.open(SPREADSHEET_NAME))
    return _SPREADSHEET


def _fetch_buyers_data():
    spreadsheet = get_spreadsheet()

    buyer_sheets = [
        ws.title for ws in _with_retry(spreadsheet.worksheets)
        if "buyers" in ws.title.lower()
    ]

    ranges = [f"'{sheet}'!A:ZZ" for sheet in buyer_sheets]

    batch_result = _with_retry(lambda: spreadsheet.values_batch_get(ranges))

    final_data = []

    for sheet_name, value_range in zip(buyer_sheets, batch_result["valueRanges"]):
        values = value_range.get("values", [])

        if len(values) < 2:
            continue

        headers = values[0]
        rows = values[1:]

        for row_values in rows:
            row = {}

            for i, header in enumerate(headers):
                clean_header = header.strip()

                if clean_header:
                    row[clean_header] = row_values[i] if i < len(row_values) else ""

            row["Sheet_Name"] = sheet_name
            final_data.append(row)

    return final_data


def load_buyers_data(force=False):
    global CACHE_DATA, CACHE_TIME

    current_time = time.time()

    # Fast path: serve fresh in-memory cache without locking.
    if not force and CACHE_DATA and (current_time - CACHE_TIME) < CACHE_SECONDS:
        return CACHE_DATA

    # Only one thread refreshes at a time; others wait and reuse the result.
    with _LOAD_LOCK:
        current_time = time.time()

        # Re-check: another thread may have refreshed while we waited.
        if not force and CACHE_DATA and (current_time - CACHE_TIME) < CACHE_SECONDS:
            return CACHE_DATA

        # Another worker/process may have refreshed the shared file already.
        if not force:
            shared_data, shared_time = _read_shared_cache()
            if shared_data and (current_time - shared_time) < CACHE_SECONDS:
                CACHE_DATA = shared_data
                CACHE_TIME = shared_time
                return CACHE_DATA

        try:
            CACHE_DATA = _fetch_buyers_data()
            CACHE_TIME = current_time
            _write_shared_cache(CACHE_DATA, CACHE_TIME)
        except APIError:
            # On quota/API failure, keep serving stale data if we have any
            # (in memory or on disk) instead of hammering the API and
            # 500-ing every request.
            if CACHE_DATA:
                return CACHE_DATA
            shared_data, shared_time = _read_shared_cache()
            if shared_data:
                CACHE_DATA = shared_data
                CACHE_TIME = shared_time
                return CACHE_DATA
            raise

    return CACHE_DATA

@app.get("/buyers-data")
def get_buyers_data(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=1000, ge=1, le=5000)
):
    try:
        all_records = load_buyers_data()

        start_index = (page - 1) * limit
        end_index = start_index + limit

        paginated_data = all_records[start_index:end_index]

        return JSONResponse(content=paginated_data)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal Server Error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }
        )


@app.post("/refresh")
def refresh_cache():
    """Force a fresh reload from Google Sheets, bypassing the cache."""
    try:
        records = load_buyers_data(force=True)
        return JSONResponse(content={
            "message": "Cache refreshed",
            "total_records": len(records),
            "refreshed_at": CACHE_TIME
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Refresh failed",
                "message": str(e),
                "traceback": traceback.format_exc()
            }
        )