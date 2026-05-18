"""
SMTP email notifications for Tripwire.
"""

import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import config


def _build_email(target: dict, change_summary: str) -> MIMEMultipart:
    detected_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"[Tripwire] New activity detected — {target['label']}"

    # Plain text body
    plain = textwrap.dedent(f"""
        TRIPWIRE ALERT
        ==============
        Target   : {target['label']}
        Platform : {target['platform'].upper()}
        URL      : {target['url']}
        Detected : {detected_at}

        CHANGE SUMMARY
        --------------
        {change_summary or 'Content has changed since the last check.'}

        Open the profile for further research:
        {target['url']}

        --
        Tripwire OSINT Monitor
        To stop monitoring this target, log into the Tripwire dashboard.
    """).strip()

    # HTML body
    escaped_summary = (change_summary or "Content has changed since the last check.") \
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; }}
        .card {{ background: #fff; border-radius: 8px; max-width: 600px; margin: 0 auto;
                 box-shadow: 0 2px 8px rgba(0,0,0,.1); overflow: hidden; }}
        .header {{ background: #c0392b; color: #fff; padding: 20px 24px; }}
        .header h1 {{ margin: 0; font-size: 20px; letter-spacing: 1px; }}
        .header p {{ margin: 4px 0 0; font-size: 13px; opacity: .85; }}
        .body {{ padding: 24px; }}
        .meta {{ background: #f9f9f9; border: 1px solid #e5e5e5; border-radius: 6px;
                 padding: 14px 18px; margin-bottom: 20px; }}
        .meta table {{ border-collapse: collapse; width: 100%; }}
        .meta td {{ padding: 4px 0; font-size: 14px; vertical-align: top; }}
        .meta td:first-child {{ color: #666; width: 90px; font-weight: bold; }}
        .summary {{ background: #fffbe6; border-left: 4px solid #f39c12;
                    padding: 14px 18px; border-radius: 0 6px 6px 0; font-size: 14px;
                    line-height: 1.6; white-space: pre-wrap; word-break: break-word; }}
        .cta {{ margin-top: 24px; text-align: center; }}
        .cta a {{ background: #c0392b; color: #fff; text-decoration: none;
                  padding: 12px 28px; border-radius: 6px; font-weight: bold; font-size: 14px; }}
        .footer {{ padding: 16px 24px; font-size: 12px; color: #999; border-top: 1px solid #eee; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <h1>&#128680; Tripwire Alert</h1>
          <p>New activity detected on a monitored profile</p>
        </div>
        <div class="body">
          <div class="meta">
            <table>
              <tr><td>Target</td><td><strong>{target['label']}</strong></td></tr>
              <tr><td>Platform</td><td>{target['platform'].upper()}</td></tr>
              <tr><td>URL</td><td><a href="{target['url']}">{target['url']}</a></td></tr>
              <tr><td>Detected</td><td>{detected_at}</td></tr>
            </table>
          </div>
          <p style="margin:0 0 10px;font-weight:bold;font-size:14px;">Change Summary</p>
          <div class="summary">{escaped_summary}</div>
          <div class="cta">
            <a href="{target['url']}">Open Profile &rarr;</a>
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


def send_alert(target: dict, change_summary: str) -> bool:
    """Send an alert email. Returns True on success."""
    if not config.ANALYST_EMAIL:
        print("[notifier] ANALYST_EMAIL not set — skipping email.")
        return False
    if not config.SMTP_USERNAME:
        print("[notifier] SMTP_USERNAME not set — skipping email.")
        return False

    msg = _build_email(target, change_summary)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.sendmail(config.SMTP_FROM, config.ANALYST_EMAIL.split(","), msg.as_string())
        print(f"[notifier] Alert sent for '{target['label']}' → {config.ANALYST_EMAIL}")
        return True
    except Exception as e:
        print(f"[notifier] Failed to send email: {e}")
        return False
