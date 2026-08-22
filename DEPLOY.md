# Deployment Guide

## Option 1: Docker (Recommended)

Works on any OS with Docker installed (Linux, macOS, Windows).

No clone required — the prebuilt multi-arch image (`amd64` / `arm64`) is pulled
from the GitHub Container Registry.

```bash
curl -O https://raw.githubusercontent.com/yelosheng/clipboard-push-server/master/docker-compose.yml

cat > .env <<EOF
FLASK_SECRET_KEY=$(openssl rand -hex 32)
ADMIN_PASSWORD=change_this
EOF

docker compose up -d
```

Run it in its own directory — `./data` is created alongside the compose file and
holds the database, settings, and uploads. See the Configuration section of
[README.md](README.md) for the optional settings.

The server starts on port `5055`. To expose it via HTTPS, put Nginx or a reverse proxy in front.

To update:

```bash
docker compose pull
docker compose up -d
```

To build from source instead (only needed if you changed the code), see the
"Building From Source" section of [README.md](README.md).

---

## Option 2: Manual — Linux (Debian / Ubuntu / CentOS / RHEL)

### 1. Install Python

**Debian / Ubuntu:**
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

**CentOS / RHEL 8+:**
```bash
sudo dnf install python3 python3-pip
```

### 2. Set Up the App

```bash
sudo mkdir -p /opt/clipboard-push
sudo chown $USER:$USER /opt/clipboard-push
git clone https://github.com/clipboardpush/clipboard-push-server.git /opt/clipboard-push
cd /opt/clipboard-push
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn gevent geventwebsocket
```

### 3. Configure Environment

```bash
cp .env.example .env
nano .env  # Fill in FLASK_SECRET_KEY, ADMIN_PASSWORD, and R2 credentials
```

### 4. Run with systemd

Create `/etc/systemd/system/clipboard-push.service`:

```ini
[Unit]
Description=Clipboard Push Relay Server
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/clipboard-push
Environment="PATH=/opt/clipboard-push/venv/bin"
EnvironmentFile=/opt/clipboard-push/.env
ExecStart=/opt/clipboard-push/venv/bin/gunicorn \
    --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    --workers 1 --bind 127.0.0.1:5055 wsgi:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo chown -R www-data:www-data /opt/clipboard-push
sudo systemctl daemon-reload
sudo systemctl enable --now clipboard-push
sudo systemctl status clipboard-push
```

### 5. Nginx Reverse Proxy (HTTPS)

Install Nginx and create `/etc/nginx/sites-available/clipboard-push`:

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:5055;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/clipboard-push /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

For free SSL certificates, use [Certbot](https://certbot.eff.org/):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com
```

---

## Option 3: Local Development (macOS / Linux)

```bash
git clone https://github.com/clipboardpush/clipboard-push-server.git
cd clipboard-push-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set FLASK_SECRET_KEY and ADMIN_PASSWORD
python wsgi.py
```

The server starts at `http://localhost:5055`. No HTTPS needed for local dev.

**macOS prerequisites** (if Python 3 is not installed):
```bash
brew install python
```

---

## Connecting Clients

Once the server is running, point your clients to it:

- **Android app:** Settings → Server Address → `your.domain.com:443` (HTTPS) or `your.domain.com:5055` (HTTP)
- **PC client:** Edit `config.json` → `"relay_server_url": "https://your.domain.com"`

Both devices must use the **same Room ID** to sync.
