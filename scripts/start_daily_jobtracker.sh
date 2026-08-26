#!/bin/zsh

project_dir="${JOBTRACKER_PROJECT_DIR:-${0:A:h:h}}"

if ! /usr/bin/pgrep -x "Google Chrome" >/dev/null 2>&1; then
  /usr/bin/open -g -a "Google Chrome"
fi

cd "$project_dir" || exit 1
exec "$project_dir/.venv/bin/python" "$project_dir/app.py"
