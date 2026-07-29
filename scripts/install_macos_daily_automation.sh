#!/bin/zsh

script_dir="${0:A:h}"
project_dir="${script_dir:h}"
template_path="$project_dir/macos/com.jobtracker.daily.plist.example"
launch_agents_dir="/Users/$(/usr/bin/id -un)/Library/LaunchAgents"
plist_path="$launch_agents_dir/com.jobtracker.daily.plist"
application_support_dir="/Users/$(/usr/bin/id -un)/Library/Application Support/JobTracker"
installed_script="$application_support_dir/start_daily_jobtracker.sh"
service_target="gui/$(/usr/bin/id -u)/com.jobtracker.daily"

/bin/mkdir -p "$launch_agents_dir"
/bin/mkdir -p "$application_support_dir"
/bin/cp "$project_dir/scripts/start_daily_jobtracker.sh" "$installed_script"
/bin/chmod 755 "$installed_script"
/usr/bin/sed \
  -e "s|__START_SCRIPT__|$installed_script|g" \
  -e "s|__PROJECT_DIR__|$project_dir|g" \
  "$template_path" > "$plist_path"
/bin/launchctl bootout "$service_target" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$plist_path"
/bin/launchctl enable "$service_target"

echo "Installed $plist_path"
echo "Chrome and JobTracker will start daily at 7:55 AM."
