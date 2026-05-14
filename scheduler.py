from .scraper import get_latest_chartwatch_url, parse_chartwatch_page
from .database import SessionLocal
from .crud import create_scan_entries
import logging

logging.basicConfig(level=logging.INFO)

def daily_scrape():
    db = SessionLocal()
    try:
        url = get_latest_chartwatch_url()
        if url:
            data = parse_chartwatch_page(url)
            if data:
                create_scan_entries(db, data)
                logging.info(f"Successfully scraped {len(data)} records from {url}")
            else:
                logging.info("No data parsed")
        else:
            logging.info("No ChartWatch article found")
    except Exception as e:
        logging.error(f"Error in daily scrape: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    daily_scrape()
