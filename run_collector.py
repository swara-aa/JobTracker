from job_agent.collector import run_collection_job


if __name__ == "__main__":
    collected = run_collection_job()
    print(f"Saved {collected} jobs")
