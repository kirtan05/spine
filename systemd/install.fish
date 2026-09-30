#!/usr/bin/env fish
# Install the spine user units. Nothing here needs root.

set -l here (dirname (status --current-filename))
set -l unit_dir "$HOME/.config/systemd/user"

mkdir -p $unit_dir
chmod +x $here/backup.sh $here/collect-stats.sh

for unit in spine-backup.service spine-backup.timer shelf-ingest.path shelf-ingest.service kostats-import.service kostats-import.timer
    ln -sf (realpath $here/$unit) $unit_dir/$unit
    echo "linked $unit"
end

systemctl --user daemon-reload

# The backup timer goes in on day one, not at Phase 4. The reading history is
# the product; losing it because backups were "coming later" is the one failure
# with no recovery path.
systemctl --user enable --now spine-backup.timer
echo "enabled spine-backup.timer"

mkdir -p "$HOME/inbox/phone"
systemctl --user enable --now shelf-ingest.path
echo "enabled shelf-ingest.path (watching $HOME/inbox)"

# Measured reading time is the only thing here that cannot be regenerated, and it
# only exists on the devices until something collects it.
systemctl --user enable --now kostats-import.timer
echo "enabled kostats-import.timer"

# Without this the units stop when the last session closes, so nothing runs while
# the laptop is logged out.
if not loginctl show-user $USER -p Linger --value | grep -q yes
    echo
    echo "Run this so the timers survive logout:"
    echo "  sudo loginctl enable-linger $USER"
end

echo
systemctl --user list-timers spine-backup.timer --no-pager
