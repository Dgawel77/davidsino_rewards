# Davidsino Rewards 🎰

Loyalty and rewards tracking system for The Davidsino.

## Features
- Web-based interface (mobile-friendly)
- RFID/NFC card scanning for quick player lookup
- Admin panel for deposits, losses, and adjustments
- Real-time points calculation
- PNL tracking per player

## Architecture
- **Backend:** FastAPI (Python) + PostgreSQL
- **Frontend:** Vanilla HTML/CSS/JS (mobile-responsive)
- **Card Reading:** USB HID readers (keyboard input) + Web NFC (Android Chrome)

## Setup

### 1. Install PostgreSQL
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### 2. Create Database
```bash
sudo -u postgres psql -c "CREATE USER davidsino WITH PASSWORD 'davidsino_pass';"
sudo -u postgres psql -c "CREATE DATABASE davidsino OWNER davidsino;"
sudo -u postgres psql -c "ALTER USER davidsino CREATEDB;"
```

### 3. Install Python Dependencies
```bash
cd davidsino-rewards
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
# Edit .env and set your DATABASE_URL and ADMIN_PIN
nano .env
```

### 5. Run the Server
```bash
source venv/bin/activate
python3 main.py
```

Server starts on `http://0.0.0.0:8000`

## Usage

### Player View
1. Tap **Scan Card**
2. Hold RFID card to reader, or tap NFC on Android
3. See points balance and PNL

### Admin View
1. Tap **Admin Mode**
2. Enter your PIN (default: `1234`)
3. **Actions tab:** Deposit/deduct/adjust points
4. **Players tab:** View all players and balances
5. **Register tab:** Add new players

## Production Deployment

### Option 1: Direct (current setup)
Run with uvicorn behind nginx reverse proxy.

### Option 2: Systemd Service
Create `/etc/systemd/system/davidsino.service`:
```ini
[Unit]
Description=Davidsino Rewards API
After=network.target postgresql.service

[Service]
Type=notify
User=dgawel
WorkingDirectory=/home/dgawel/.openclaw/workspace/davidsino-rewards
ExecStart=/home/dgawel/.openclaw/workspace/davidsino-rewards/venv/bin/python3 main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable davidsino
sudo systemctl start davidsino
```

### Option 3: Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name rewards.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## API Endpoints

### Public
- `GET /` - Frontend app
- `POST /api/scan` - Scan card, get player info
- `GET /api/health` - Health check

### Admin (PIN required for mutations)
- `POST /api/admin/auth` - Authenticate with PIN
- `POST /api/admin/register` - Register new player
- `POST /api/admin/deposit` - Add deposit/points
- `POST /api/admin/loss` - Record loss/spend
- `POST /api/admin/adjustment` - Manual adjustment
- `GET /api/admin/players` - List all players
- `GET /api/admin/transactions/{id}` - Player transaction history

## Security Notes
- Change default ADMIN_PIN in `.env`
- For production, use HTTPS (Let's Encrypt)
- Don't expose port 8000 directly to internet — use nginx
- Consider adding rate limiting for production

## Hardware
- **USB RFID Reader:** Any USB HID-compatible reader (acts as keyboard)
- **NFC:** Android phones with Chrome (Web NFC API)
- **RFID Cards:** Standard MIFARE 13.56MHz cards work great
