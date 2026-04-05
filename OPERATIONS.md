# Davidsino Rewards - Operations Cheat Sheet

## Quick Access
- **Public URL:** Changes on each restart (check tunnel logs)
- **Local URL:** http://187.124.183.3:8000
- **Code Dir:** /home/dgawel/.openclaw/workspace/davidsino-rewards/

## PINs
- **Dealer/Admin:** strongpin (change in .env)
- **Worker:** workerpin (change in .env)

## Start the App
```bash
cd /home/dgawel/.openclaw/workspace/davidsino-rewards
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Start the Tunnel (in a separate terminal)
```bash
cd /home/dgawel/.openclaw/workspace/davidsino-rewards
./cloudflared tunnel --url http://localhost:8000 --no-autoupdate
```

## Check if Running
```bash
# Check API server
curl http://localhost:8000/api/health

# Check tunnel logs
cat /home/dgawel/.openclaw/workspace/davidsino-rewards/tunnel.log 2>/dev/null

# Find the current tunnel URL
ps aux | grep cloudflared | grep -v grep
```

## Install as System Services (survives reboot)
```bash
sudo cp /home/dgawel/.openclaw/workspace/davidsino-rewards/davidsino-api.service /etc/systemd/system/
sudo cp /home/dgawel/.openclaw/workspace/davidsino-rewards/davidsino-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable davidsino-api davidsino-tunnel
sudo systemctl start davidsino-api
sudo systemctl start davidsino-tunnel
```

## Database Reset (wipe all data)
```bash
sudo -u postgres psql -d davidsino -c "DELETE FROM transactions; DELETE FROM players;"
```

## Key Files
- **Backend:** main.py
- **Frontend:** static/index.html
- **Config:** .env (PINs, DB URL)
- **Rewards config:** PRESET_REWARDS dict in main.py

## Points System
- $1 deposit = 100 reward points
- PNL = cash_out - cash_in (positive = player ahead)

## Common Tasks
- **Change reward rates:** Edit `reward_earned = request.amount * 100` in main.py
- **Change preset rewards:** Edit PRESET_REWARDS dict in main.py
- **Change PINs:** Edit .env and restart server
