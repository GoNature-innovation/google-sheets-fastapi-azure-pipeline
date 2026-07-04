from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI()

SERVICE_ACCOUNT_FILE = "service_account.json"

SPREADSHEET_NAME = "Copy of SILVERMEMBERSHIP SALES SHEET (L1) | H.I.G.H RAZORPAY 2025-26"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

client = gspread.authorize(creds)


@app.get("/")
def home():
    return {
        "message": "FastAPI Google Sheets API is running"
    }


@app.get("/buyers-data")
def get_buyers_data(
    page: int = Query(1, ge=1),
    limit: int = Query(500, ge=1, le=5000)
):
    spreadsheet = client.open(SPREADSHEET_NAME)

    final_data = []
    total_records = 0

    start_index = (page - 1) * limit
    end_index = start_index + limit

    for worksheet in spreadsheet.worksheets():
        sheet_name = worksheet.title

        if "buyers" not in sheet_name.lower():
            continue

        values = worksheet.get_all_values()

        if len(values) < 2:
            continue

        headers = values[0]
        rows = values[1:]

        for row_values in rows:
            total_records += 1

            if total_records <= start_index:
                continue

            if len(final_data) >= limit:
                break

            row = {}

            for i, header in enumerate(headers):
                if header.strip() != "":
                    row[header] = row_values[i] if i < len(row_values) else ""

            row["Sheet_Name"] = sheet_name

            final_data.append(row)

        if len(final_data) >= limit:
            break

    return JSONResponse(content={
        "page": page,
        "limit": limit,
        "records_returned": len(final_data),
        "has_more": len(final_data) == limit,
        "data": final_data
    })