# ChartWatch ASX Tracker

Daily scanner for Carl Capolingua's ChartWatch ASX Scans from marketindex.com.au.

## Setup on Render

1. Set **Root Directory** = (blank)
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add PostgreSQL database and set `DATABASE_URL` environment variable.

Run manual scrape at `/run-scrape` endpoint.
