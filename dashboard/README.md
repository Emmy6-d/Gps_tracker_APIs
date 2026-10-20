# ESP GPS Tracker dashboard

This dashboard follows the firmware contract in `firmware/ESP_Firmware.ino`:

`ESP32 -> FastAPI -> Supabase`, with the React client reading only from FastAPI.

## 1. Create the Supabase table

Run [`backend/supabase_schema.sql`](./backend/supabase_schema.sql) in the Supabase SQL editor.

## 2. Start the API

```powershell
cd dashboard/backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Fill in Supabase URL, service-role key, the same API key used by the ESP32,
# and UNWIREDLABS_TOKEN for Wi-Fi positioning.
uvicorn app.main:app --host 0.0.0.0 --reload --port 8001
```

`UNWIREDLABS_TOKEN` must be a real token from
`https://my.unwiredlabs.com/`. Use `UNWIREDLABS_REGION=eu1` for Europe, then
restart FastAPI after editing `.env`. The ESP firmware remains unchanged: it
continues sending nearby Wi-Fi access points to FastAPI.

The ingestion endpoint is `POST /api/telemetry` with the firmware's `X-API-Key` header. The service-role key stays in FastAPI and is never exposed to React. This project uses port `8001` to avoid conflicting with another local service commonly using port `8000`.

Use `--host 0.0.0.0` so the ESP32 can reach the API over the Wi-Fi LAN. The
firmware currently targets `192.168.8.15:8001`; update that address in
`firmware/ESP_Firmware.ino` if the computer's LAN address changes.
If Windows Firewall prompts for Python, allow it on the Private network.

The ESP sends nearby Wi-Fi access points instead of requiring a GPS or
temperature module. FastAPI sends those access points to Google's Geolocation
API, stores the estimated coordinates, then requests the current temperature
for those coordinates from Open-Meteo. The dashboard displays the coordinate
source and temperature source so an estimate is not mistaken for an onboard
sensor reading.

## 3. Start the React dashboard

```powershell
cd dashboard/frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Open the Vite URL shown in the terminal. The map uses OpenStreetMap tiles and shows a marker only when `gps.fix` is true and both coordinates are present. It polls FastAPI every 10 seconds, matching the firmware telemetry interval.
