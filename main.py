from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import traceback

app = FastAPI()

SPREADSHEET_NAME = "Copy of SILVERMEMBERSHIP SALES SHEET (L1) | H.I.G.H RAZORPAY 2025-26"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])

creds = Credentials.from_service_account_info(
    service_account_info,
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
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=1000, ge=1, le=5000)
):
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)

        all_records = []

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
                row = {}

                for i, header in enumerate(headers):
                    clean_header = header.strip()

                    if clean_header != "":
                        row[clean_header] = row_values[i] if i < len(row_values) else ""

                row["Sheet_Name"] = sheet_name
                all_records.append(row)

        total_records = len(all_records)

        start_index = (page - 1) * limit
        end_index = start_index + limit

        paginated_data = all_records[start_index:end_index]

        has_more = end_index < total_records

        if has_more:
            next_page = page + 1
            base_url = str(request.url).split("?")[0]
            next_url = f"{base_url}?page={next_page}&limit={limit}"
        else:
            next_url = None

        return JSONResponse(content={
            "page": page,
            "limit": limit,
            "total_records": total_records,
            "records_returned": len(paginated_data),
            "has_more": has_more,
            "next_url": next_url,
            "data": paginated_data
        })

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal Server Error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }
        )