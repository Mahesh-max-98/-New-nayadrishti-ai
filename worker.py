import logging
import os
from apscheduler.schedulers.blocking import BlockingScheduler
from news_monitor import check_feeds
from database import init_db

logging.basicConfig(level=logging.INFO)
init_db()

def job():
    logging.info("Starting NayaDrishti feed check")
    try:
        logging.info("Result: %s", check_feeds())
    except Exception:
        logging.exception("Scheduled check failed")

if __name__ == "__main__":
    seconds = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
    scheduler = BlockingScheduler(timezone="Asia/Kathmandu")
    scheduler.add_job(job, "interval", seconds=max(60, seconds), max_instances=1, coalesce=True)
    job()
    scheduler.start()
