## Wingfoil Forecast Service

A Flask-based service that aggregates marine and weather data to analyze wingfoil suitability, provide equipment recommendations by rider weight, and serve a responsive dashboard plus JSON APIs. Deployed behind Traefik and Cloudflare Tunnel.

### Key Features
- Real-time conditions and Today/Tomorrow forecasts
- Wingfoil-specific scoring (0-100) and advice (wing size by weight)
- Multi-model consensus (Open-Meteo primary, OpenWeatherMap secondary)
- Mobile-friendly dashboard and health endpoint
- Protected by Traefik middleware (global basic auth)

## Architecture
- Flask app container built via `Dockerfile`
- Traefik routes `wingfoil.felixmrak.com` to the container (port 5000)
- Cloudflare Tunnel terminates TLS at the edge and forwards internally
- Config and data mounted into the container (`./config`, `./data`)

## Endpoints
- GET `/` — Dashboard UI
- GET `/health` — Service health JSON
- GET `/api/current-conditions` — Current conditions with wingfoil analysis
- GET `/api/hourly-forecast` — Today’s hourly forecast (daylight)
- GET `/api/tomorrow-forecast` — Tomorrow’s hourly forecast (daylight)
- GET `/api/inkypi/morning-report` — Simplified report for e‑ink displays

## Configuration
Edit `config/config.json` (copy from `config/config.example.json` if needed):
- `location`: `name`, `latitude`, `longitude`, `shore_direction`
- `wingfoil_preferences`: thresholds for wind/waves
- `user`: `rider_weight_kg`, `skill_level`
- `models`: list of forecast models (e.g., `gfs`, `icon_seamless`, `ecmwf_ifs04`)

## Local Development
Requirements (`requirements.txt`): Flask, requests, aiohttp, python-dateutil.
You can run locally inside Docker (recommended) or with Python:

```bash
# Python (dev) — requires env vars and config present
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FLASK_ENV=development
python app/main.py
```

## Deploy
### Using the repository script
```bash
cd ~/websites/data/wingfoil-forecast
bash deploy.sh
```
The script builds, starts the container, waits briefly, and checks `/health`.

### Using Docker Compose directly
```bash
cd ~/websites/data/wingfoil-forecast
docker compose up -d --build
```

## Runtime & Routing
The service is defined in `docker-compose.yml`:

```yaml
services:
  wingfoil-forecast:
    build: .
    container_name: wingfoil-forecast
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./config:/app/config
    networks:
      - web
    environment:
      - FLASK_ENV=production
      - TZ=Europe/Berlin
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.wingfoil-forecast.rule=Host(`wingfoil.felixmrak.com`)"
      - "traefik.http.routers.wingfoil-forecast.entrypoints=web"
      - "traefik.http.routers.wingfoil-forecast.middlewares=auth-global@file"
      - "traefik.http.services.wingfoil-forecast.loadbalancer.server.port=5000"

networks:
  web:
    external: true
```

Notes:
- Domain: `wingfoil.felixmrak.com`
- Traefik middleware `auth-global@file` is expected to be defined in the parent stack under `config/traefik/dynamic_conf/auth.yml`.
- TLS is terminated at Cloudflare; Traefik listens on the internal entrypoint.

## Manage via central script
From the repo root:
```bash
cd ~/websites
./manage-websites.sh
```
Select `wingfoil-forecast` to view options (deploy/start/stop/restart/logs/status/update).

## Troubleshooting
- Check container: `docker ps --filter "name=wingfoil-forecast"`
- View logs: `docker logs wingfoil-forecast --tail 100 -f`
- Traefik logs: `docker logs traefik`
- Cloudflare Tunnel: `systemctl status cloudflared`
- Health: `curl https://wingfoil.felixmrak.com/health`

## Security
- Protected by Traefik basic auth middleware (global)
- Origin is not exposed publicly; access is via Cloudflare Tunnel
- Avoid committing secrets; see `GITLEAKS_README.md` and `.gitleaks.toml`

## License
Internal project; see repository policies.



