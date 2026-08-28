"""
Background polling scheduler for Tripwire.

Each active target is checked on its own interval using APScheduler.
When content changes, an alert is recorded and an email is sent.
"""

import difflib
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
import database as db
from notifier import send_alert, send_failure_alert, send_recovery_notice
from scrapers import get_scraper

log = logging.getLogger("tripwire.monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

scheduler = BackgroundScheduler(timezone="UTC")


MAX_DIFF_LINES = 200


def _build_change_summary(old_text: str, new_text: str) -> str:
    """
    Produce a human-readable diff over the full normalized content.

    Diffing the complete text (rather than a truncated excerpt) means changes
    anywhere on the page show up in the alert, not just changes near the top.
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="previous snapshot",
        tofile="current snapshot",
        lineterm="",
        n=3,
    ))

    if not diff:
        return "Content hash changed but text diff is empty (binary or encoding change)."

    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    header = f"{added} line(s) added, {removed} removed"

    body = diff[:MAX_DIFF_LINES]
    if len(diff) > MAX_DIFF_LINES:
        body.append(f"… diff truncated, {len(diff) - MAX_DIFF_LINES} more line(s)")

    return "\n".join([header, ""] + body)


def _previous_text(snapshot: dict) -> str:
    """Full text of a snapshot, falling back to the excerpt for older rows."""
    return snapshot.get("content") or snapshot.get("content_excerpt") or ""


def _handle_failure(target: dict, error: str):
    """
    Record a failed check and, once a target has failed enough times in a row,
    email about it.

    A failing target produces no change alerts, which is indistinguishable from
    a target that simply has nothing to report — so silence here would end the
    monitoring without ever saying so. The alert is sent once per outage and
    the recovery notice closes it; re-sending on every check would train the
    analyst to ignore it.
    """
    target_id = target["id"]
    log.warning(f"  ✗ Scrape failed: {error}")
    state = db.mark_target_error(target_id, error)
    failures = state["consecutive_failures"]

    threshold = config.FAILURE_ALERT_THRESHOLD
    if threshold <= 0 or failures < threshold or state["failure_alert_sent"]:
        return

    # Only record the alert as sent if it actually went out, so a mail outage
    # that coincides with a scrape outage retries on the next check.
    if send_failure_alert(target, error, failures, state["first_failure_at"] or ""):
        db.mark_failure_alert_sent(target_id)
        log.warning(f"  ! Failure alert sent for [{target['label']}] after {failures} failures")
    else:
        log.error(f"  ! Failure alert could not be sent for [{target['label']}]")


def _handle_recovery(target: dict):
    """Notify that a target which had been reported as failing is back."""
    if not target.get("failure_alert_sent"):
        return
    log.info(f"  ✓ [{target['label']}] recovered after "
             f"{target.get('consecutive_failures', 0)} failed check(s)")
    send_recovery_notice(
        target,
        target.get("consecutive_failures", 0),
        target.get("first_failure_at") or "",
    )


def check_target(target_id: int):
    target = db.get_target(target_id)
    if not target or not target["active"]:
        return

    log.info(f"Checking [{target['label']}] {target['url']}")
    scraper = get_scraper(target["platform"])
    result = scraper.scrape(target["url"], target.get("ignore_patterns", ""))

    if not result.success:
        _handle_failure(target, result.error or "unknown error")
        return

    # `target` was read before this check, so it still carries the failure
    # state this success is about to clear.
    _handle_recovery(target)

    prev = db.get_latest_snapshot(target_id)

    if prev is None:
        # First check — just store the baseline, no alert
        db.save_snapshot(target_id, result.content_hash, result.content, result.content_excerpt)
        log.info(f"  ✓ Baseline saved for [{target['label']}]")
        return

    if result.content_hash == prev["content_hash"]:
        db.touch_target(target_id)
        log.info(f"  ✓ No change for [{target['label']}]")
        return

    # Content changed!
    log.info(f"  ★ Change detected for [{target['label']}]!")
    change_summary = _build_change_summary(_previous_text(prev), result.content)

    email_sent = send_alert(target, change_summary)
    # The alert is recorded before the snapshot advances the baseline. The
    # reverse order would drop the change entirely if the alert write failed,
    # whereas this way a failure at worst re-reports it on the next check.
    db.record_alert(target_id, target["label"], target["url"], change_summary, email_sent)
    db.save_snapshot(target_id, result.content_hash, result.content,
                     result.content_excerpt, status="changed")


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
