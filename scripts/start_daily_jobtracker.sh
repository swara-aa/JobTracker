#!/bin/zsh

project_dir="${JOBTRACKER_PROJECT_DIR:-${0:A:h:h}}"

if ! /usr/bin/pgrep -x "Google Chrome" >/dev/null 2>&1; then
  /usr/bin/open -g -a "Google Chrome"
fi

if /usr/bin/curl -fsS --max-time 3 http://127.0.0.1:5000/ >/dev/null 2>&1; then
  exit 0
fi

flask_command="cd '$project_dir' && nohup '$project_dir/.venv/bin/python' '$project_dir/app.py' > /private/tmp/jobtracker-flask.log 2>&1 </dev/null & disown; exit"
/usr/bin/osascript -e 'tell application "Terminal"' -e "do script \"$flask_command\"" -e 'end tell'
