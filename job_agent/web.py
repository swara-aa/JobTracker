from __future__ import annotations

import json
import os
import secrets
from hmac import compare_digest
from datetime import date, datetime, timedelta, timezone

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from job_agent.linkedin_review import (
    build_manual_job,
    linkedin_bookmarklet,
    linkedin_console_snippet,
    parse_linkedin_json,
)
from job_agent.config import (
    AUTOMATION_PUBLIC_COLLECTION_TIME,
    GREENHOUSE_BOARDS,
    LEVER_SITES,
    configured_boards,
    get_user_setting,
)
from job_agent.company_intelligence import get_company_attributes, initialize_company_intelligence
from job_agent.posting_quality import verification_reasons_by_job
from job_agent.storage import (
    delete_resume,
    analytics_summary,
    distinct_values,
    fetch_job,
    fetch_application_helper,
    fetch_jobs,
    fetch_resume_matches,
    fetch_resumes,
    existing_job_links,
    job_ids_for_links,
    job_count,
    save_resume,
    save_linkedin_descriptions,
    save_jobs,
    skill_gap_summary,
    update_job_pipeline,
    ensure_database,
)
from job_agent.resume_library import extract_resume

PRIORITY_WINDOW_DAYS = 5
MAX_RADAR_SCORE_BATCH = 30
GEMINI_SCORE_BASE_BATCH = 20
RADAR_PAGE_SIZE = 50
PRIORITY_QUEUE_LIMIT = 10
INBOX_URGENT_FRESHNESS_BONUS = 8
INBOX_RECENT_FRESHNESS_BONUS = 4
INBOX_FORTUNE_500_BONUS = 3
POSTED_WITHIN_OPTIONS = {
    "24h": ("Last 24 hours", timedelta(hours=24)),
    "week": ("Past week", timedelta(days=7)),
    "month": ("Past month", timedelta(days=30)),
}
ANALYTICS_PERIODS = {7: "Past 7 days", 30: "Past 30 days", 90: "Past 90 days"}
ACTIONABLE_PIPELINE_STATUSES = {"Saved", "Tailor Resume"}
APPLICATION_STATUSES = [
    "Saved",
    "Tailor Resume",
    "Applied",
    "Interview",
    "Rejected",
    "Offer",
    "Not pursuing",
    "Closed",
]


def _posted_at(job: dict[str, object]) -> datetime | None:
    try:
        posted_at = datetime.fromisoformat(str(job["posting_date"]).replace("Z", "+00:00"))
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    return posted_at.astimezone(timezone.utc)


def _is_priority_job(job: dict[str, object]) -> bool:
    posted_at = _posted_at(job)
    if posted_at is None:
        return True
    return datetime.now(timezone.utc) - posted_at <= timedelta(days=PRIORITY_WINDOW_DAYS)


def _priority_urgency_bonus(
    job: dict[str, object],
    *,
    now: datetime | None = None,
) -> int:
    posted_at = _posted_at(job)
    if posted_at is None:
        return 0
    age = (now or datetime.now(timezone.utc)) - posted_at
    if age <= timedelta(hours=24):
        return INBOX_URGENT_FRESHNESS_BONUS
    if age <= timedelta(days=3):
        return INBOX_RECENT_FRESHNESS_BONUS
    return 0


def _priority_company_bonus(job: dict[str, object]) -> int:
    company = get_company_attributes(str(job.get("company") or ""))
    return INBOX_FORTUNE_500_BONUS if company.get("fortune_500") is True else 0


def _priority_sort_key(
    job: dict[str, object],
    *,
    now: datetime | None = None,
) -> tuple[int, int, bool, str]:
    match_score = int(_effective_match_score(job) or 0)
    return (
        match_score + _priority_urgency_bonus(job, now=now) + _priority_company_bonus(job),
        match_score,
        job.get("application_status") == "Tailor Resume",
        str(job["posting_date"]),
    )


def _build_priority_queue(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    verification_reasons = verification_reasons_by_job(jobs)
    actionable_jobs = [
        job
        for job in jobs
        if _is_priority_job(job)
        and job.get("application_status") in ACTIONABLE_PIPELINE_STATUSES
        and not _has_match_hard_no(job)
        and int(job["id"]) not in verification_reasons
        and _effective_match_score(job) is not None
    ]
    actionable_jobs.sort(key=_priority_sort_key, reverse=True)
    for job in actionable_jobs:
        job["best_match_score"] = _effective_match_score(job)
        job["best_match_source"] = _effective_match_source(job)
        job["is_fortune_500"] = _priority_company_bonus(job) > 0
    return actionable_jobs[:PRIORITY_QUEUE_LIMIT]


def _effective_match_score(job: dict[str, object]) -> int | None:
    gemini_score = job.get("resume_match_score")
    if gemini_score is not None:
        return int(gemini_score)
    local_score = job.get("local_match_score")
    return int(local_score) if local_score is not None else None


def _effective_match_source(job: dict[str, object]) -> str:
    return "Gemini" if job.get("resume_match_score") is not None else "Local pre-score"


def _has_match_hard_no(job: dict[str, object]) -> bool:
    return bool(job.get("resume_match_hard_no")) or bool(job.get("local_match_hard_no"))


def _score_bound(value: str) -> int | None:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return score if 0 <= score <= 100 else None


def _was_posted_within(job: dict[str, object], posted_within: str) -> bool:
    option = POSTED_WITHIN_OPTIONS.get(posted_within)
    if option is None:
        return True
    try:
        posted_at = datetime.fromisoformat(str(job["posting_date"]).replace("Z", "+00:00"))
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - posted_at.astimezone(timezone.utc) <= option[1]


def _filter_dashboard_jobs(
    jobs: list[dict[str, object]],
    posted_within: str,
    minimum_score: int | None,
    maximum_score: int | None,
) -> list[dict[str, object]]:
    filtered_jobs: list[dict[str, object]] = []
    for job in jobs:
        score = _effective_match_score(job)
        if not _was_posted_within(job, posted_within):
            continue
        if minimum_score is not None and (score is None or score < minimum_score):
            continue
        if maximum_score is not None and (score is None or score > maximum_score):
            continue
        filtered_jobs.append(job)
    return filtered_jobs


def _matches_company_tier(job: dict[str, object], company_tier: str) -> bool:
    if company_tier != "fortune500":
        return True
    return get_company_attributes(str(job.get("company") or "")).get("fortune_500") is True


def _access_control_enabled() -> bool:
    return os.getenv("JOBTRACKER_AUTH_REQUIRED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _extension_import_token_valid() -> bool:
    configured_token = get_user_setting("JOBTRACKER_EXTENSION_IMPORT_TOKEN")
    if not configured_token:
        return False
    supplied_token = request.headers.get("X-JobTracker-Import-Token", "").strip()
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        supplied_token = authorization[7:].strip()
    return bool(supplied_token) and compare_digest(supplied_token, configured_token)


def _is_linkedin_import_endpoint() -> bool:
    return request.endpoint in {
        "linkedin_import_api",
        "linkedin_finalize_collection_api",
        "linkedin_descriptions_api",
    }


def _safe_next_url(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("index")


def create_app() -> Flask:
    initialize_company_intelligence()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_user_setting("FLASK_SECRET_KEY") or secrets.token_urlsafe(48)
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    app.config["AUTH_REQUIRED"] = _access_control_enabled()

    @app.before_request
    def require_private_access():
        if request.method == "OPTIONS":
            return None
        if not app.config["AUTH_REQUIRED"]:
            return None
        if request.endpoint in {"health_check", "login", "static"}:
            return None
        if _is_linkedin_import_endpoint() and _extension_import_token_valid():
            return None
        if session.get("jobtracker_authenticated") is True:
            return None
        if _is_linkedin_import_endpoint():
            return jsonify({"error": "A valid JobTracker import token is required."}), 401
        return redirect(url_for("login", next=request.full_path))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not app.config["AUTH_REQUIRED"]:
            return redirect(url_for("index"))

        next_url = _safe_next_url(request.values.get("next", ""))
        if request.method == "POST":
            configured_password = get_user_setting("JOBTRACKER_ACCESS_PASSWORD")
            supplied_password = request.form.get("password", "")
            if configured_password and compare_digest(supplied_password, configured_password):
                session.clear()
                session["jobtracker_authenticated"] = True
                return redirect(next_url)
            return render_template("login.html", error="Incorrect password.", next_url=next_url), 401

        return render_template("login.html", error="", next_url=next_url)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/api/health")
    def health_check():
        try:
            ensure_database()
        except Exception as exc:
            return jsonify({"status": "degraded", "error": str(exc)[:160]}), 503
        return jsonify(
            {
                "status": "ok",
                "service": "jobtracker",
                "release": os.getenv("JOBTRACKER_RELEASE_VERSION", "local"),
            }
        )

    @app.template_filter("relative_time")
    def relative_time(value: str) -> str:
        try:
            posted_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            seconds = max(
                0,
                int((datetime.now(timezone.utc) - posted_at.astimezone(timezone.utc)).total_seconds()),
            )
        except (AttributeError, TypeError, ValueError):
            return value

        if seconds < 60:
            return "just now"
        if seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"

    @app.route("/")
    def index():
        all_jobs = fetch_jobs()
        verification_reasons = verification_reasons_by_job(all_jobs)
        active_jobs = [
            job
            for job in all_jobs
            if job.get("application_status") != "Closed"
            and int(job["id"]) not in verification_reasons
        ]
        inbox_jobs = _build_priority_queue(active_jobs)
        captured = request.args.get("captured", "").strip()
        accepted = request.args.get("accepted", "").strip()
        saved = request.args.get("saved", "").strip()
        import_message = ""
        if all(value.isdigit() for value in [captured, accepted, saved]):
            import_message = (
                f"Saved {saved} new job(s) from {accepted} readable LinkedIn card(s)."
                if int(saved)
                else "The LinkedIn jobs were already in your tracker."
            )
        return render_template(
            "inbox.html",
            inbox_jobs=inbox_jobs,
            inbox_count=len(inbox_jobs),
            total_jobs=len(all_jobs),
            verification_job_count=sum(
                job.get("application_status") != "Closed"
                for job in all_jobs
                if int(job["id"]) in verification_reasons
            ),
            closed_job_count=sum(job.get("application_status") == "Closed" for job in all_jobs),
            skill_gaps=skill_gap_summary(limit=20)[:5],
            import_message=import_message,
            message=request.args.get("message", "").strip(),
        )

    @app.route("/analytics")
    def analytics():
        try:
            days = int(request.args.get("days", "30"))
        except ValueError:
            days = 30
        if days not in ANALYTICS_PERIODS:
            days = 30
        return render_template(
            "analytics.html",
            analytics=analytics_summary(days),
            periods=ANALYTICS_PERIODS,
        )

    @app.route("/jobs")
    def browse_jobs():
        role = request.args.get("role", "").strip()
        location = request.args.get("location", "").strip()
        company = request.args.get("company", "").strip()
        company_tier = request.args.get("company_tier", "").strip()
        visa = request.args.get("visa", "").strip()
        application_status = request.args.get("application_status", "").strip()
        view = request.args.get("view", "").strip()
        showing_closed = view == "closed"
        showing_verification = view == "verification"
        posted_within = request.args.get("posted_within", "").strip()
        minimum_score = _score_bound(request.args.get("minimum_score", ""))
        maximum_score = _score_bound(request.args.get("maximum_score", ""))
        sort = request.args.get("sort", "").strip()
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        captured = request.args.get("captured", "").strip()
        accepted = request.args.get("accepted", "").strip()
        saved = request.args.get("saved", "").strip()
        radar_message = request.args.get("message", "").strip()

        import_message = ""
        if all(value.isdigit() for value in [captured, accepted, saved]):
            if int(saved):
                import_message = (
                    f"LinkedIn sent {captured} card(s); {accepted} were readable "
                    f"and {saved} new job(s) were saved."
                )
            elif int(accepted):
                import_message = (
                    f"LinkedIn sent {captured} card(s), but all {accepted} matching "
                    "job(s) were already in the tracker."
                )
            else:
                import_message = (
                    f"LinkedIn sent {captured} card(s), but none contained a job link."
                )

        all_jobs = fetch_jobs()
        verification_reasons = verification_reasons_by_job(all_jobs)
        closed_job_count = sum(
            job.get("application_status") == "Closed" for job in all_jobs
        )
        verification_job_count = sum(
            job.get("application_status") != "Closed"
            for job in all_jobs
            if int(job["id"]) in verification_reasons
        )
        matching_jobs = fetch_jobs(
            role=role,
            location=location,
            company=company,
            visa=visa,
            application_status=application_status,
        )
        matching_jobs = _filter_dashboard_jobs(
            matching_jobs, posted_within, minimum_score, maximum_score
        )
        matching_jobs = [
            job for job in matching_jobs if _matches_company_tier(job, company_tier)
        ]
        if showing_closed:
            matching_jobs = [job for job in matching_jobs if job.get("application_status") == "Closed"]
        elif showing_verification:
            matching_jobs = [
                job
                for job in matching_jobs
                if job.get("application_status") != "Closed"
                and int(job["id"]) in verification_reasons
            ]
        else:
            matching_jobs = [
                job
                for job in matching_jobs
                if job.get("application_status") != "Closed"
                and int(job["id"]) not in verification_reasons
            ]
        for job in matching_jobs:
            job["verification_reasons"] = verification_reasons.get(int(job["id"]), [])
        if sort == "match":
            matching_jobs.sort(
                key=lambda job: (
                    not _has_match_hard_no(job),
                    int(_effective_match_score(job) or -1),
                ),
                reverse=True,
            )
        total_pages = max(1, (len(matching_jobs) + RADAR_PAGE_SIZE - 1) // RADAR_PAGE_SIZE)
        page = min(page, total_pages)
        page_start = (page - 1) * RADAR_PAGE_SIZE
        posted_label = POSTED_WITHIN_OPTIONS.get(posted_within, ("Any posting date",))[0]
        return render_template(
            "index.html",
            jobs=matching_jobs[page_start : page_start + RADAR_PAGE_SIZE],
            page=page,
            total_pages=total_pages,
            jobs_in_view=len(matching_jobs),
            posted_label=posted_label,
            showing_closed=showing_closed,
            showing_verification=showing_verification,
            closed_job_count=closed_job_count,
            verification_job_count=verification_job_count,
            total_jobs=job_count(),
            roles=distinct_values("role_query"),
            locations=distinct_values("location"),
            companies=distinct_values("company"),
            visa_assessments=distinct_values("visa_assessment"),
            application_statuses=APPLICATION_STATUSES,
            filters={
                "role": role,
                "location": location,
                "company": company,
                "company_tier": company_tier,
                "visa": visa,
                "application_status": application_status,
                "sort": sort,
                "posted_within": posted_within,
                "minimum_score": "" if minimum_score is None else str(minimum_score),
                "maximum_score": "" if maximum_score is None else str(maximum_score),
                "view": view,
            },
            import_message=import_message,
            radar_message=radar_message,
        )

    @app.post("/jobs/score-radar")
    def score_radar_jobs():
        from job_agent.resume_matcher import compare_resumes

        if not fetch_resumes():
            return redirect(url_for("resumes", error="Upload at least one resume before scoring jobs."))

        role = request.form.get("role", "").strip()
        location = request.form.get("location", "").strip()
        company = request.form.get("company", "").strip()
        company_tier = request.form.get("company_tier", "").strip()
        visa = request.form.get("visa", "").strip()
        application_status = request.form.get("application_status", "").strip()
        view = request.form.get("view", "").strip()
        posted_within = request.form.get("posted_within", "").strip()
        minimum_score = _score_bound(request.form.get("minimum_score", ""))
        maximum_score = _score_bound(request.form.get("maximum_score", ""))
        sort = request.form.get("sort", "").strip()
        matching_jobs = fetch_jobs(
            role=role,
            location=location,
            company=company,
            visa=visa,
            application_status=application_status,
        )
        verification_reasons = verification_reasons_by_job(fetch_jobs())
        candidates = [
            job
            for job in _filter_dashboard_jobs(
                matching_jobs, posted_within, minimum_score, maximum_score
            )
            if _matches_company_tier(job, company_tier)
        ]
        candidates = [
            job
            for job in candidates
            if job.get("application_status") != "Closed"
            and int(job["id"]) not in verification_reasons
            and job.get("resume_match_score") is None
        ]
        candidates.sort(key=lambda job: int(_effective_match_score(job) or 0), reverse=True)
        if len(candidates) > GEMINI_SCORE_BASE_BATCH:
            cutoff = int(_effective_match_score(candidates[GEMINI_SCORE_BASE_BATCH - 1]) or 0)
            selected_jobs = [
                job for job in candidates if int(_effective_match_score(job) or 0) >= cutoff - 10
            ][:MAX_RADAR_SCORE_BATCH]
        else:
            selected_jobs = candidates

        completed = 0
        failed = 0
        for job in selected_jobs:
            try:
                compare_resumes(int(job["id"]))
                completed += 1
            except Exception:  # noqa: BLE001
                failed += 1

        if not selected_jobs:
            message = "Every job in this radar view already has a saved resume score."
        elif failed:
            message = f"Scored {completed} job(s); {failed} could not be scored. Review those job pages for details."
        else:
            message = f"Scored {completed} job(s). Use Best resume match first to prioritize them."
        return redirect(
            url_for(
                "browse_jobs",
                role=role,
                location=location,
                company=company,
                company_tier=company_tier,
                visa=visa,
                application_status=application_status,
                sort=sort,
                posted_within=posted_within,
                minimum_score="" if minimum_score is None else minimum_score,
                maximum_score="" if maximum_score is None else maximum_score,
                view=view,
                message=message,
            )
        )

    @app.post("/jobs/<int:job_id>/quick-status")
    def quick_update_pipeline(job_id: int):
        application_status = request.form.get("application_status", "").strip()
        if application_status not in {"Tailor Resume", "Applied", "Not pursuing"}:
            return redirect(url_for("index", message="Choose a valid Inbox action."))
        job = fetch_job(job_id)
        if job is None:
            return redirect(url_for("index", message="That job is no longer available."))
        applied_date = str(job.get("applied_date") or "")
        if application_status == "Applied" and not applied_date:
            applied_date = date.today().isoformat()
        update_job_pipeline(
            job_id,
            application_status,
            applied_date,
            str(job.get("application_link") or ""),
            str(job.get("application_notes") or ""),
            str(job.get("follow_up_date") or ""),
        )
        labels = {
            "Tailor Resume": "Moved to Tailor Resume.",
            "Applied": "Marked as applied.",
            "Not pursuing": "Removed from your active Inbox.",
        }
        return redirect(url_for("index", message=labels[application_status]))

    @app.get("/operations")
    def operations():
        from job_agent.automation import automation_status
        from job_agent.gemini_batch import batch_status
        from job_agent.gemini_queue import gemini_queue_status
        from job_agent.public_enrichment import overnight_public_backfill_status
        from job_agent.storage import job_ids_without_gemini_match, public_description_missing_count

        return render_template(
            "operations.html",
            total_jobs=job_count(),
            resume_count=len(fetch_resumes()),
            descriptions_waiting=public_description_missing_count(),
            gemini_waiting=len(job_ids_without_gemini_match()),
            gemini_configured=bool(get_user_setting("GEMINI_API_KEY")),
            overnight_status=overnight_public_backfill_status(),
            gemini_status=gemini_queue_status(),
            gemini_batch_status=batch_status(refresh=False),
            automation_status=automation_status(),
            automation_public_collection_time=AUTOMATION_PUBLIC_COLLECTION_TIME,
            greenhouse_boards=configured_boards(GREENHOUSE_BOARDS),
            lever_sites=configured_boards(LEVER_SITES),
            message=request.args.get("message", "").strip(),
        )

    @app.post("/operations/collect-public-boards")
    def collect_public_boards():
        from job_agent.automation import schedule_public_postprocessing
        from job_agent.collector import run_collection_and_prepare_matches

        result = run_collection_and_prepare_matches(submit_gemini=False)
        schedule_public_postprocessing(result["saved_job_ids"])
        message = (
            f"Saved {result['saved']} new public-board job(s); "
            f"locally scored {result['local_scored']}. {result['gemini_batch_message']}"
        )
        return redirect(url_for("operations", message=message))

    @app.post("/jobs/submit-gemini-batch")
    def submit_gemini_batch():
        from job_agent.gemini_batch import submit_gemini_resume_batch

        try:
            state = submit_gemini_resume_batch()
            return redirect(url_for("operations", message=str(state["message"])))
        except Exception as exc:  # noqa: BLE001
            return redirect(url_for("operations", message=str(exc)))

    @app.post("/jobs/local-score-all")
    def local_score_all_jobs():
        from job_agent.local_scoring import score_all_jobs_locally

        try:
            result = score_all_jobs_locally()
            message = (
                f"Locally pre-scored {result['scored']} jobs against {result['resumes']} resume(s). "
                "No Gemini or network calls were used."
            )
            return redirect(url_for("operations", message=message))
        except Exception as exc:  # noqa: BLE001
            return redirect(url_for("operations", message=str(exc)))

    @app.post("/jobs/backfill-public-details")
    def backfill_public_details():
        from job_agent.public_enrichment import enqueue_public_description_backfill

        queued = enqueue_public_description_backfill()
        if queued:
            message = (
                f"Queued {queued} jobs for public description backfill. "
                "The local app waits 8 seconds between requests and stops after 3 unavailable pages."
            )
        else:
            message = "No eligible jobs are waiting for public description backfill."
        return redirect(url_for("operations", message=message))

    @app.post("/jobs/start-overnight-public-backfill")
    def start_overnight_public_details_backfill():
        from job_agent.public_enrichment import start_overnight_public_backfill

        status = start_overnight_public_backfill()
        return redirect(url_for("operations", message=str(status["message"])))

    @app.post("/jobs/stop-overnight-public-backfill")
    def stop_overnight_public_details_backfill():
        from job_agent.public_enrichment import stop_overnight_public_backfill

        status = stop_overnight_public_backfill()
        return redirect(url_for("operations", message=str(status["message"])))

    @app.route("/linkedin-review", methods=["GET", "POST"])
    def linkedin_review():
        message = ""
        error = ""

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            try:
                if action == "import-json":
                    payload = request.form.get("payload", "")
                    raw = json.loads(payload)
                    captured = len(raw) if isinstance(raw, list) else 1
                    jobs = parse_linkedin_json(payload)
                    existing_links = existing_job_links(job.link for job in jobs)
                    saved = save_jobs(jobs)
                    if saved:
                        new_links = [job.link for job in jobs if job.link not in existing_links]
                        from job_agent.public_enrichment import start_overnight_public_backfill

                        start_overnight_public_backfill()
                    if request.form.get("capture_mode") == "bookmark":
                        return redirect(
                            url_for(
                                "index",
                                captured=captured,
                                accepted=len(jobs),
                                saved=saved,
                            )
                        )
                    message = f"Imported {saved} new job(s) from LinkedIn review JSON."
                elif action == "manual-add":
                    job = build_manual_job(
                        title=request.form.get("title", ""),
                        company=request.form.get("company", ""),
                        location=request.form.get("location", ""),
                        link=request.form.get("link", ""),
                        posting_date_text=request.form.get("posting_date_text", ""),
                        role_query=request.form.get("role_query", ""),
                    )
                    saved = save_jobs([job])
                    if saved:
                        from job_agent.public_enrichment import start_overnight_public_backfill

                        start_overnight_public_backfill()
                        message = "Saved 1 job to the tracker."
                    else:
                        message = "That job was already in the tracker."
                else:
                    error = "Unsupported action."
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

        return render_template(
            "linkedin_review.html",
            message=message,
            error=error,
            bookmarklet=linkedin_bookmarklet(),
            snippet=linkedin_console_snippet(),
        )

    @app.route("/jobs/<int:job_id>")
    def job_detail(job_id: int):
        job = fetch_job(job_id)
        if job is None:
            abort(404)
        for field in [
            "gemini_skills_required",
            "gemini_skills_preferred",
            "gemini_responsibilities",
            "gemini_requirements",
            "local_match_evidence",
            "local_match_missing",
            "local_match_hard_no_reasons",
            "public_capture_metadata",
        ]:
            try:
                job[field] = json.loads(str(job.get(field) or "[]"))
            except json.JSONDecodeError:
                job[field] = []
        resume_matches = fetch_resume_matches(job_id)
        for match in resume_matches:
            for field in [
                "matched_skills",
                "missing_skills",
                "improvements",
                "hard_no_reasons",
            ]:
                try:
                    match[field] = json.loads(str(match.get(field) or "[]"))
                except json.JSONDecodeError:
                    match[field] = []
        resumes = fetch_resumes()
        selected_resume_id = next(
            (int(match["resume_id"]) for match in resume_matches if match.get("is_best")),
            int(job["local_match_resume_id"]) if job.get("local_match_resume_id") else None,
        )
        if selected_resume_id is None and resumes:
            selected_resume_id = int(resumes[0]["id"])
        application_helper = (
            fetch_application_helper(job_id, selected_resume_id) if selected_resume_id is not None else None
        )
        if application_helper:
            try:
                application_helper["content"] = json.loads(str(application_helper["content"]))
            except json.JSONDecodeError:
                application_helper = None
        return render_template(
            "job_detail.html",
            job=job,
            resumes=resumes,
            resume_matches=resume_matches,
            selected_resume_id=selected_resume_id,
            application_helper=application_helper,
            application_statuses=APPLICATION_STATUSES,
            gemini_configured=bool(get_user_setting("GEMINI_API_KEY")),
            message=request.args.get("message", ""),
            error=request.args.get("error", ""),
        )

    @app.post("/jobs/<int:job_id>/analyze")
    def analyze_job_with_gemini(job_id: int):
        from job_agent.gemini_analysis import analyze_job

        try:
            analyze_job(job_id)
            return redirect(
                url_for("job_detail", job_id=job_id, message="Gemini analysis completed.")
            )
        except Exception as exc:  # noqa: BLE001
            return redirect(url_for("job_detail", job_id=job_id, error=str(exc)))

    @app.post("/jobs/<int:job_id>/match-resumes")
    def match_resumes(job_id: int):
        from job_agent.resume_matcher import compare_resumes

        try:
            compare_resumes(job_id)
            return redirect(
                url_for(
                    "job_detail",
                    job_id=job_id,
                    message="Resume comparison completed.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            return redirect(url_for("job_detail", job_id=job_id, error=str(exc)))

    @app.post("/jobs/<int:job_id>/application-helper")
    def create_application_helper(job_id: int):
        from job_agent.application_helper import generate_application_helper

        try:
            resume_id = int(request.form.get("resume_id", ""))
            generate_application_helper(job_id, resume_id)
            return redirect(
                url_for(
                    "job_detail",
                    job_id=job_id,
                    message="Personalized application helper and cover letter created.",
                )
                + "#application-helper"
            )
        except (TypeError, ValueError) as exc:
            return redirect(url_for("job_detail", job_id=job_id, error=str(exc)) + "#application-helper")
        except Exception as exc:  # noqa: BLE001
            return redirect(url_for("job_detail", job_id=job_id, error=str(exc)) + "#application-helper")

    @app.post("/jobs/<int:job_id>/pipeline")
    def update_pipeline(job_id: int):
        application_status = request.form.get("application_status", "").strip()
        if application_status not in APPLICATION_STATUSES:
            return redirect(url_for("job_detail", job_id=job_id, error="Choose a valid status."))

        applied_date = request.form.get("applied_date", "").strip()
        if application_status == "Applied" and not applied_date:
            applied_date = date.today().isoformat()
        if update_job_pipeline(
            job_id,
            application_status,
            applied_date,
            request.form.get("application_link", "").strip()[:2000],
            request.form.get("application_notes", "").strip()[:5000],
            request.form.get("follow_up_date", "").strip(),
        ):
            return redirect(url_for("job_detail", job_id=job_id, message="Application pipeline updated."))
        return redirect(url_for("job_detail", job_id=job_id, error="Job not found."))

    @app.post("/jobs/<int:job_id>/mark-closed")
    def mark_job_closed(job_id: int):
        job = fetch_job(job_id)
        if not job:
            return redirect(url_for("index", error="Job not found."))
        update_job_pipeline(
            job_id,
            "Closed",
            str(job.get("applied_date") or ""),
            str(job.get("application_link") or ""),
            str(job.get("application_notes") or ""),
            str(job.get("follow_up_date") or ""),
        )
        return redirect(
            url_for(
                "job_detail",
                job_id=job_id,
                message="Moved to No Longer Accepting Applications.",
            )
        )

    @app.post("/jobs/<int:job_id>/resume-advice")
    def resume_advice(job_id: int):
        from job_agent.resume_matcher import advise_resume_implementation

        payload = request.get_json(silent=True) or {}
        try:
            resume_id = int(payload.get("resume_id"))
            selected_improvement = str(payload.get("selected_improvement") or "")
            advice = advise_resume_implementation(
                job_id,
                resume_id,
                selected_improvement,
            )
            return jsonify({"advice": advice})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502

    @app.route("/resumes", methods=["GET", "POST"])
    def resumes():
        error = request.args.get("error", "")
        message = request.args.get("message", "")
        if request.method == "POST":
            try:
                uploads = [file for file in request.files.getlist("resumes") if file.filename]
                current = fetch_resumes()
                if not uploads:
                    raise ValueError("Choose at least one resume file.")
                if len(current) + len(uploads) > 4:
                    raise ValueError(
                        f"You can store four resumes. There are {4 - len(current)} slot(s) available."
                    )
                extracted = [extract_resume(file) for file in uploads]
                for filename, content in extracted:
                    save_resume(filename.rsplit(".", 1)[0], filename, content)
                return redirect(
                    url_for("resumes", message=f"Uploaded {len(extracted)} resume(s).")
                )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        return render_template(
            "resumes.html",
            resumes=fetch_resumes(),
            error=error,
            message=message,
        )

    @app.post("/resumes/<int:resume_id>/delete")
    def remove_resume(resume_id: int):
        if delete_resume(resume_id):
            message = "Resume deleted."
            return redirect(url_for("resumes", message=message))
        return redirect(url_for("resumes", error="Resume not found."))

    @app.route("/api/linkedin/import", methods=["POST"])
    def linkedin_import_api():
        try:
            raw = request.get_json(force=True)
            captured = len(raw) if isinstance(raw, list) else 1
            payload = json.dumps(raw)
            jobs = parse_linkedin_json(payload)
            existing_links = existing_job_links(job.link for job in jobs)
            saved = save_jobs(jobs)
            new_links = [job.link for job in jobs if job.link not in existing_links]
            deferred = request.args.get("defer_enrichment") == "1"
            capture_status = None
            if saved and not deferred:
                from job_agent.public_enrichment import start_overnight_public_backfill

                capture_status = start_overnight_public_backfill()
            return jsonify(
                {
                    "captured": captured,
                    "accepted": len(jobs),
                    "saved": saved,
                    "new_links": new_links,
                    "automatic_capture_running": bool(capture_status and capture_status["running"]),
                    "enrichment_deferred": deferred,
                    "total": job_count(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/linkedin/finalize-collection")
    def linkedin_finalize_collection_api():
        try:
            raw = request.get_json(force=True)
            links = raw.get("links", []) if isinstance(raw, dict) else []
            if not isinstance(links, list):
                raise ValueError("Expected a links array.")
            from job_agent.automation import schedule_linkedin_postprocessing

            job_ids = job_ids_for_links(links)
            status = schedule_linkedin_postprocessing(job_ids)
            return jsonify(
                {
                    "scheduled": True,
                    "job_count": len(job_ids),
                    "message": str(status["message"]),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/linkedin/descriptions")
    def linkedin_descriptions_api():
        try:
            raw = request.get_json(force=True)
            items = raw.get("jobs", []) if isinstance(raw, dict) else []
            if not isinstance(items, list):
                raise ValueError("Expected a jobs array.")
            updated_job_ids = save_linkedin_descriptions(items)
            return jsonify(
                {
                    "captured": len(items),
                    "descriptions_saved": len(updated_job_ids),
                    "gemini_batch_submission_required": bool(updated_job_ids),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400

    return app
