"""
SMTP email notifications for Tripwire.

Three kinds of mail are sent, all sharing one card layout:

  change    a monitored target published something new
  failure   a target has failed to be checked repeatedly and is no longer
            being monitored in any meaningful sense
  recovery  a previously failing target is being checked again

The failure mail exists because a dead monitor and a quiet target look
identical from the analyst's inbox: both produce no mail at all. Without it,
expired cookies or changed markup silently end the surveillance.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import config


# Each kind differs only in colour, wording and framing; sharing the skeleton
# keeps all three rendering the same way in a mail client.
THEMES = {
    "change": {
        "icon": "&#128680;",
        "headline": "Tripwire Alert",
        "subhead": "New activity detected on a monitored profile",
        "plain_title": "TRIPWIRE ALERT",
        "accent": "#c0392b",
        "band": "#f39c12",
        "band_bg": "#fffbe6",
        "time_label": "Detected",
        "summary_label": "Change Summary",
        "cta": "Open Profile",
    },
    "failure": {
        "icon": "&#9888;",
        "headline": "Tripwire Monitor Failing",
        "subhead": "A target cannot be checked — it is no longer being monitored",
        "plain_title": "TRIPWIRE MONITOR FAILURE",
        "accent": "#8e44ad",
        "band": "#8e44ad",
        "band_bg": "#f7f0fb",
        "time_label": "Reported",
        "summary_label": "Last Error",
        "cta": "Open Target",
    },
    "recovery": {
        "icon": "&#10004;",
        "headline": "Tripwire Monitor Recovered",
        "subhead": "Checks are succeeding again on a previously failing target",
        "plain_title": "TRIPWIRE MONITOR RECOVERED",
        "accent": "#1e8449",
        "band": "#27ae60",
        "band_bg": "#eefaf1",
        "time_label": "Recovered",
        "summary_label": "Details",
        "cta": "Open Target",
    },
}


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _build_email(kind: str, subject: str, target: dict,
                 summary: str, extra_rows: tuple = ()) -> MIMEMultipart:
    theme = THEMES[kind]
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    rows = [
        ("Target", target["label"]),
        ("Platform", target["platform"].upper()),
        ("URL", target["url"]),
        (theme["time_label"], stamp),
    ] + list(extra_rows)

    # Plain text body. Built line by line rather than from an indented
    # template: the summary and meta block are themselves multi-line, which
    # leaves a dedented template with no common indent left to strip.
    width = max(len(name) for name, _ in rows)
    plain = "\n".join([
        theme["plain_title"],
        "=" * len(theme["plain_title"]),
        *(f"{name:<{width}} : {value}" for name, value in rows),
        "",
        theme["summary_label"].upper(),
        "-" * len(theme["summary_label"]),
        summary,
        "",
        target["url"],
        "",
        "--",
        "Tripwire OSINT Monitor",
        "To stop monitoring this target, log into the Tripwire dashboard.",
    ])

    # HTML body
    meta_html = "".join(
        f"<tr><td>{_escape(name)}</td><td>"
        + (f'<a href="{target["url"]}">{_escape(target["url"])}</a>'
           if name == "URL" else
           f"<strong>{_escape(str(value))}</strong>" if name == "Target"
           else _escape(str(value)))
        + "</td></tr>"
        for name, value in rows
    )
    escaped_summary = _escape(summary).replace("\n", "<br>")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; }}
        .card {{ background: #fff; border-radius: 8px; max-width: 600px; margin: 0 auto;
                 box-shadow: 0 2px 8px rgba(0,0,0,.1); overflow: hidden; }}
        .header {{ background: {theme['accent']}; color: #fff; padding: 20px 24px; }}
        .header h1 {{ margin: 0; font-size: 20px; letter-spacing: 1px; }}
        .header p {{ margin: 4px 0 0; font-size: 13px; opacity: .85; }}
        .body {{ padding: 24px; }}
        .meta {{ background: #f9f9f9; border: 1px solid #e5e5e5; border-radius: 6px;
                 padding: 14px 18px; margin-bottom: 20px; }}
        .meta table {{ border-collapse: collapse; width: 100%; }}
        .meta td {{ padding: 4px 0; font-size: 14px; vertical-align: top; }}
        .meta td:first-child {{ color: #666; width: 110px; font-weight: bold; }}
        .summary {{ background: {theme['band_bg']}; border-left: 4px solid {theme['band']};
                    padding: 14px 18px; border-radius: 0 6px 6px 0; font-size: 14px;
                    line-height: 1.6; white-space: pre-wrap; word-break: break-word; }}
        .cta {{ margin-top: 24px; text-align: center; }}
        .cta a {{ background: {theme['accent']}; color: #fff; text-decoration: none;
                  padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 14px; }}
        .footer {{ padding: 16px 24px; font-size: 12px; color: #999; border-top: 1px solid #eee; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <h1>{theme['icon']} {theme['headline']}</h1>
          <p>{theme['subhead']}</p>
        </div>
        <div class="body">
          <div class="meta"><table>{meta_html}</table></div>
          <p style="margin:0 0 10px;font-weight:bold;font-size:14px;">{theme['summary_label']}</p>
          <div class="summary">{escaped_summary}</div>
          <div class="cta">
            <a href="{target['url']}">{theme['cta']} &rarr;</a>
          </div>
        </div>
        <div class="footer">Tripwire OSINT Monitor &mdash; To stop monitoring this target, visit your Tripwire dashboard.</div>
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.ANALYST_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def _send(msg: MIMEMultipart, description: str) -> bool:
    """Deliver a prepared message. Returns True on success."""
    if not config.ANALYST_EMAIL:
        print("[notifier] ANALYST_EMAIL not set — skipping email.")
        return False
    if not config.SMTP_USERNAME:
        print("[notifier] SMTP_USERNAME not set — skipping email.")
        return False

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.sendmail(config.SMTP_FROM, config.ANALYST_EMAIL.split(","), msg.as_string())
        print(f"[notifier] {description} → {config.ANALYST_EMAIL}")
        return True
    except Exception as e:
        print(f"[notifier] Failed to send email: {e}")
        return False


def send_alert(target: dict, change_summary: str) -> bool:
    """Send a change alert. Returns True on success."""
    msg = _build_email(
        "change",
        f"[Tripwire] New activity detected — {target['label']}",
        target,
        change_summary or "Content has changed since the last check.",
    )
    return _send(msg, f"Alert sent for '{target['label']}'")


def send_failure_alert(target: dict, error_msg: str,
                       failures: int, since: str = "") -> bool:
    """
    Report that a target has failed enough consecutive checks to be considered
    down. Sent once per outage; the recovery notice closes the loop.
    """
    rows = [("Failed checks", f"{failures} consecutive")]
    if since:
        rows.append(("Failing since", since))

    summary = (
        f"{error_msg}\n\n"
        "No changes can be detected on this target until the checks succeed "
        "again. Common causes: expired scraper cookies, a changed page layout "
        "that yields no text, a blocked or rate-limited IP, or a target URL "
        "that no longer exists."
    )
    msg = _build_email(
        "failure",
        f"[Tripwire] Monitor failing — {target['label']}",
        target, summary, tuple(rows),
    )
    return _send(msg, f"Failure alert sent for '{target['label']}'")


def send_recovery_notice(target: dict, failures: int, since: str = "") -> bool:
    """Report that a previously failing target is being checked again."""
    rows = [("Failed checks", f"{failures} before recovery")]
    if since:
        rows.append(("Failing since", since))

    summary = (
        "This target is being checked successfully again. If a snapshot "
        "existed before the outage, anything that changed in the meantime is "
        "compared against it and reported as a normal change alert; if the "
        "target never completed a first check, this success establishes the "
        "baseline."
    )
    msg = _build_email(
        "recovery",
        f"[Tripwire] Monitor recovered — {target['label']}",
        target, summary, tuple(rows),
    )
    return _send(msg, f"Recovery notice sent for '{target['label']}'")
