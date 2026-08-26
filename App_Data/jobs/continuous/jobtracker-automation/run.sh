#!/usr/bin/env bash
set -euo pipefail

cd /home/site/wwwroot
export JOBTRACKER_AUTOMATION_ENABLED=false
exec python automation_worker.py
