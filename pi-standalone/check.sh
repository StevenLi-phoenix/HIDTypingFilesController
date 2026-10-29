#!/bin/sh
# Is the SD card full / is the gadget link up? Read-only. Safe to run anywhere.
echo "== udc =="
for u in /sys/class/udc/*; do
  [ -e "$u/state" ] || continue
  echo "$(basename "$u"): state=$(cat "$u/state") speed=$(cat "$u/current_speed" 2>/dev/null)"
done
echo
echo "== disk =="
df -h / /boot 2>/dev/null | sed -n '1,4p'
echo
echo "== biggest dirs in \$HOME =="
du -sh "$HOME"/* 2>/dev/null | sort -h | tail -8
echo
echo "== log volume =="
du -sh /var/log 2>/dev/null
sudo du -sh /var/log/journal 2>/dev/null || true
echo
echo "== if root fs is 100%, free space with =="
echo "  sudo journalctl --vacuum-size=50M"
echo "  sudo rm -f /var/log/*.gz /var/log/*.1"
echo "  rm -f ~/nohup.out ~/*.patch ~/__pycache__/*.pyc"
echo "  ls -la ~ | sort -k5 -n | tail   # then delete what is not needed"
