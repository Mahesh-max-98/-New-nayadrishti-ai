import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from database import init_db
from news_monitor import check_feeds

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("nayadrishti-worker")


def scheduled_check():
    log.info("Starting scheduled NayaDrishti feed check")
    try:
        log.info("Feed check result: %s", check_feeds())
    except Exception:
        log.exception("Scheduled feed check failed")


if __name__ == "__main__":
    init_db()
    interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))
    scheduler = BlockingScheduler(timezone="Asia/Kathmandu")
    scheduler.add_job(
        scheduled_check,
        trigger="interval",
        seconds=interval,
        max_instances=1,
        coalesce=True,
    )
    scheduled_check()
    log.info("Worker started; checking every %s seconds", interval)
    scheduler.start()
