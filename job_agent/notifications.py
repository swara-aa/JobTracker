from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from job_agent import config
from job_agent.storage import fetch_jobs


logger = logging.getLogger(__name__)


def send_collection_digest(new_jobs: int) -> None:
    if not (config.SMTP_HOST and config.SMTP_TO):
        logger.info("Collection digest not sent: SMTP is not configured.")
        return
    jobs = sorted(
        fetch_jobs(),
        key=lambda job: int(job.get("resume_match_score") or job.get("local_match_score") or -1),
        reverse=True,
    )[:3]
    lines = [f"{new_jobs} new job(s) were saved.", "", "Top matches:"]
    lines.extend(
        f"- {job['title']} at {job['company']} — "
        f"{job.get('resume_match_score') or job.get('local_match_score') or 'not scored'}/100"
        for job in jobs
    )
    message = EmailMessage()
    message["Subject"] = f"JobTracker daily digest: {new_jobs} new jobs"
    message["From"] = config.SMTP_USERNAME or config.SMTP_TO
    message["To"] = config.SMTP_TO
    message.set_content("\n".join(lines))
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
        server.starttls()
        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(message)


def send_operational_alert(subject: str, body: str) -> bool:
    recipient = config.ALERT_EMAIL or config.SMTP_TO
    sender = config.SMTP_USERNAME or config.SMTP_TO
    if not (config.SMTP_HOST and sender and recipient):
        logger.info("Operational alert not sent: SMTP alert settings are not configured.")
        return False

    message = EmailMessage()
    message["Subject"] = f"JobTracker alert: {subject}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    if config.SMTP_PORT == 465:
        server_context = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    else:
        server_context = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)

    with server_context as server:
        if config.SMTP_PORT != 465:
            server.starttls()
        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(message)
    return True
