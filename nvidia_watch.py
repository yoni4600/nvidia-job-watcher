import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from playwright.sync_api import sync_playwright


URL = (
    "https://nvidia.wd5.myworkdayjobs.com/"
    "NVIDIAExternalCareerSite?locationHierarchy1=2fcb99c455831013ea52bbe14cf9326c"
)

STORE_FILE = "notified.json"


def load_notified_ids() -> set[str]:
    if not os.path.exists(STORE_FILE):
        return set()
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("notified", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_notified_ids(ids: set[str]) -> None:
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump({"notified": list(ids)}, f, indent=2)


def extract_job_id(url: str) -> str:
    """
    Extract stable job id from URL.
    Examples:
      ..._JR2005814-1?location...  -> JR2005814
      ..._JR2004391?location...    -> JR2004391
    """
    m = re.search(r"(JR\d+)", url)
    if m:
        return m.group(1)

    # Fallback – defensive
    base = url.split("_")[-1]
    base = base.split("?")[0]
    base = base.split("-")[0]
    return base


def get_today_jobs() -> list[dict]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="load")

        # Wait for jobs counter to be visible
        page.wait_for_selector('[data-automation-id="jobFoundText"]', timeout=30000)

        # Scroll to trigger job list rendering
        page.mouse.wheel(0, 2000)

        # Wait for at least one job title
        page.wait_for_selector('[data-automation-id="jobTitle"]', timeout=30000)

        title_elements = page.query_selector_all('[data-automation-id="jobTitle"]')
        posted_elements = page.query_selector_all('[data-automation-id="postedOn"]')

        jobs: list[dict] = []

        for t, p in zip(title_elements, posted_elements):
            posted_txt = p.inner_text().strip()
            posted_lower = posted_txt.lower()

            # Only keep "today" jobs; stop at first non-today
            if "today" in posted_lower:
                jobs.append(
                    {
                        "title": t.inner_text().strip(),
                        "url": t.get_attribute("href"),
                        "posted": posted_txt,
                    }
                )
            else:
                break

        browser.close()
        return jobs


def send_email(new_jobs: list[dict]) -> None:
    mail_user = os.environ["MAIL_USER"]
    mail_pass = os.environ["MAIL_PASS"]
    to_email = os.environ.get("TO_EMAIL", mail_user)

    subject = f"NVIDIA — {len(new_jobs)} new job(s) posted today"
    lines: list[str] = []

    for job in new_jobs:
        lines.append(f"{job['title']}\n{job['posted']}\n{job['url']}\n")

    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = mail_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(mail_user, mail_pass)
        smtp.send_message(msg)

    print("Email sent with", len(new_jobs), "new job(s).")


def main() -> None:
    print("Starting NVIDIA watcher run...")

    jobs = get_today_jobs()
    print(f"Scraped {len(jobs)} job(s) with 'today' in posted date.")

    if not jobs:
        print("No 'today' jobs found. Exiting without email.")
        return

    notified = load_notified_ids()

    new_jobs: list[dict] = []
    new_ids: list[str] = []

    for job in jobs:
        url = job["url"] or ""
        job_id = extract_job_id(url)
        if job_id not in notified:
            print("NEW job:", job["title"], "->", job_id)
            new_jobs.append(job)
            new_ids.append(job_id)
        else:
            print("Already notified:", job_id)

    if not new_jobs:
        print("No new jobs since last run. Exiting without email.")
        return

    # Send mail only for new jobs
    send_email(new_jobs)

    # Update notified store
    notified.update(new_ids)
    save_notified_ids(notified)
    print("Updated notified.json with new job IDs.")


if __name__ == "__main__":
    main()
