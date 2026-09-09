# lingclaude managed daemon

Install without touching the system service database:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user/lingclaude-daemon-watch.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lingclaude-daemon-watch.service
systemctl --user status lingclaude-daemon-watch.service --no-pager
```

`Restart=always` plus `RestartSec=30s` avoids the historical immediate-restart
storm pattern; `OOMScoreAdjust=-500` is intentionally conservative.
