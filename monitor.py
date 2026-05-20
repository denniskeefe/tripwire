"""
Background polling scheduler for Tripwire.

Each active target is checked on its own interval using APScheduler.
When content changes, an alert is recorded and an email is sent.
"""

import difflib
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import database as db
from notifier import send_alert
from scrapers import get_scraper

log = logging.getLogger("tripwire.monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

scheduler = BackgroundScheduler(timezone="UTC")


def _build_change_summary(old_excerpt: str, new_excerpt: str) -> str:
    """Produce a human-readable diff of the two excerpts."""
    old_lines = old_excerpt.splitlines()
    new_lines = new_excerpt.splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="previous snapshot",
        tofile="current snapshot",
        lineterm="",
        n=3,
    ))
    if diff:
        summary = "\n".join(diff[:80])  # cap at 80 diff lines
    else:
        summary = "Content hash changed but text diff is empty (binary or encoding change)."
    return summary


def check_target(target_id: int):
    target = db.get_target(target_id)
    if not target or not target["active"]:
        return

    log.info(f"Checking [{target['label']}] {target['url']}")
    scraper = get_scraper(target["platform"])
    result = scraper.scrape(target["url"])

    if not result.success:
        log.warning(f"  ✗ Scrape failed: {result.error}")
        db.mark_target_error(target_id, result.error or "unknown error")
        return

    prev = db.get_latest_snapshot(target_id)

    if prev is None:
        # First check — just store the baseline, no alert
        db.save_snapshot(target_id, result.content_hash, result.content_excerpt)
        log.info(f"  ✓ Baseline saved for [{target['label']}]")
        return

    if result.content_hash == prev["content_hash"]:
        db.save_snapshot(target_id, result.content_hash, result.content_excerpt)
        log.info(f"  ✓ No change for [{target['label']}]")
        return

    # Content changed!
    log.info(f"  ★ Change detected for [{target['label']}]!")
    change_summary = _build_change_summary(
        prev.get("content_excerpt", ""),
        result.content_excerpt,
    )

    email_sent = send_alert(target, change_summary)
    db.record_alert(target_id, target["label"], target["url"], change_summary, email_sent)
    db.save_snapshot(target_id, result.content_hash, result.content_excerpt)


def _make_job_id(target_id: int) -> str:
    return f"target_{target_id}"


def schedule_target(target: dict):
    """Add or replace a polling job for one target."""
    job_id = _make_job_id(target["id"])
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        check_target,
        trigger=IntervalTrigger(minutes=int(target["check_interval"])),
        args=[target["id"]],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=60,
    )
    log.info(f"Scheduled [{target['label']}] every {target['check_interval']} min")


def unschedule_target(target_id: int):
    job_id = _make_job_id(target_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        log.info(f"Unscheduled target {target_id}")


def check_now(target_id: int):
    """Trigger an immediate check outside the normal schedule."""
    check_target(target_id)


def start():
    """Start the scheduler and load all active targets."""
    if not scheduler.running:
        scheduler.start()
    for target in db.get_targets(active_only=True):
        schedule_target(target)
    log.info("Monitor started.")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
