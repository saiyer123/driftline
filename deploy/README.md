# Deploying Driftline to a VPS

Runs the engine, cognition daemon, and dashboard 24/7 on a small Ubuntu 24.04
server (1 vCPU / 1–2 GB RAM is plenty — e.g. a ~$5–8/mo Hetzner CX22 or
DigitalOcean basic droplet).

## Security model

- Only SSH is exposed (ufw). The engine API (8484) and dashboard (3000) bind
  to localhost on the server — you reach the dashboard through an SSH tunnel.
- Keys live only in `/home/driftline/driftline/.env` on the server (never in git).
- Paper trading only, as everywhere else.

## Steps

1. **You provision the server** (any provider, Ubuntu 24.04, add your SSH key).

2. **Copy the repo + your .env from your laptop:**

   ```bash
   rsync -a --exclude node_modules --exclude .venv --exclude .next \
     ~/driftline/ root@<server-ip>:/home/driftline/driftline/
   ```

   (`.env` is included by rsync since it's only git-ignored; verify with
   `ssh root@<server-ip> ls -la /home/driftline/driftline/.env`.)

3. **Run setup on the server:**

   ```bash
   ssh root@<server-ip> "bash /home/driftline/driftline/deploy/setup.sh"
   ```

4. **Open the dashboard from your laptop** (keep this running while you look):

   ```bash
   ssh -N -L 3000:localhost:3000 driftline@<server-ip>
   ```

   → http://localhost:3000

5. **Stop the laptop processes** — the server owns them now. Running both
   places would double-submit orders (the reconciler would halt on it, but
   don't test that on purpose).

## Operations

```bash
journalctl -u driftline-engine -f        # engine logs
journalctl -u driftline-daemon -f        # Claude jobs
systemctl restart driftline-engine       # after deploying code changes
```

Deploying updates: rsync again (step 2), then
`ssh root@<server-ip> "cd /home/driftline/driftline/engine && sudo -u driftline ~driftline/.local/bin/uv sync && systemctl restart driftline-engine driftline-daemon"`
(add a dashboard rebuild + restart when dashboard code changed).

Candidate branches created by the strategist live in the server's repo clone —
fetch them to your laptop with
`git remote add vps driftline@<server-ip>:/home/driftline/driftline && git fetch vps`.
