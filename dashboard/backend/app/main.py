from datetime import datetime
import logging
from pathlib import Path
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from supabase import Client, create_client


class Settings(BaseSettings):
    # Resolve the backend env file from this module, not from the shell's cwd.
    # This keeps `uvicorn app.main:app` and `uvicorn dashboard.backend.app.main:app`
    # behaving the same way.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )

    supabase_url: str
    supabase_service_role_key: str
    device_api_key: str
    frontend_origins: str = "http://localhost:5174"
    unwiredlabs_token: str | None = None
    unwiredlabs_region: str = "eu1"


settings = Settings()
logger = logging.getLogger(__name__)
supabase: Client = create_client(
    settings.supabase_url, settings.supabase_service_role_key
)

# External lookups are cached because the ESP32 reports every 10 seconds.
location_cache: dict[str, tuple[float, "GPS"]] = {}
weather_cache: dict[str, tuple[float, float]] = {}
LOCATION_CACHE_SECONDS = 60
WEATHER_CACHE_SECONDS = 300


class GPS(BaseModel):
    fix: bool = False
    satellites: int = Field(default=0, ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    altitude_m: float | None = None
    speed_kph: float | None = Field(default=None, ge=0)
    source: str | None = None
    accuracy_m: float | None = Field(default=None, ge=0)


class AccessPoint(BaseModel):
    bssid: str
    ssid: str = ""
    rssi: int
    channel: int


class TelemetryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    device_id: str = Field(min_length=1, max_length=100)
    firmware_version: str | None = None
    uptime_seconds: int = Field(default=0, ge=0)
    temperature_c: float | None = None
    temperature_source: str | None = None
    wifi_rssi: int | None = None
    free_heap_bytes: int | None = Field(default=None, ge=0)
    recorded_at: datetime | None = None
    gps: GPS = Field(default_factory=GPS)
    wifi_access_points: list[AccessPoint] = Field(default_factory=list, max_length=10)


def resolve_wifi_location(payload: TelemetryPayload) -> GPS | None:
    """Estimate location from scanned Wi-Fi access points via Unwired Labs."""
    token = settings.unwiredlabs_token
    if not token or token.lower().startswith(("your", "replace", "change")):
        logger.warning(
            "Wi-Fi location unavailable: UNWIREDLABS_TOKEN is not configured"
        )
        return None

    now = time.monotonic()
    cached = location_cache.get(payload.device_id)
    if cached and now - cached[0] < LOCATION_CACHE_SECONDS:
        return cached[1]

    access_points = [
        {
            "bssid": point.bssid.lower(),
            "signal": point.rssi,
            "channel": point.channel,
        }
        for point in payload.wifi_access_points
        if point.bssid.upper() != "FF:FF:FF:FF:FF:FF"
        and not point.bssid.upper().startswith("00:00:5E")
    ]
    # Unwired Labs requires at least two valid access points for Wi-Fi location.
    if len(access_points) < 2:
        return None

    try:
        response = httpx.post(
            f"https://{settings.unwiredlabs_region}.unwiredlabs.com/v2/process",
            json={
                "token": token,
                "id": payload.device_id[:20],
                "wifi": access_points,
                "address": 0,
            },
            timeout=8,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "ok":
            raise ValueError(result.get("message") or result.get("status"))
        resolved = GPS(
            fix=True,
            latitude=float(result["lat"]),
            longitude=float(result["lon"]),
            source="unwiredlabs_wifi",
            accuracy_m=float(result["accuracy"]) if result.get("accuracy") is not None else None,
        )
        location_cache[payload.device_id] = (now, resolved)
        return resolved
    except httpx.HTTPStatusError as error:
        logger.warning(
            "Unwired Labs Wi-Fi geolocation rejected the request (%s): %s",
            error.response.status_code,
            error.response.text[:500],
        )
        return None
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        logger.warning("Unwired Labs Wi-Fi geolocation failed: %s", error)
        return None


def resolve_weather_temperature(device_id: str, gps: GPS) -> float | None:
    """Get the current environmental temperature for the resolved coordinates."""
    if gps.latitude is None or gps.longitude is None:
        return None

    now = time.monotonic()
    cached = weather_cache.get(device_id)
    if cached and now - cached[0] < WEATHER_CACHE_SECONDS:
        return cached[1]

    try:
        response = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": gps.latitude,
                "longitude": gps.longitude,
                "current": "temperature_2m",
                "temperature_unit": "celsius",
                "timezone": "UTC",
            },
            timeout=8,
        )
        response.raise_for_status()
        temperature = float(response.json()["current"]["temperature_2m"])
        weather_cache[device_id] = (now, temperature)
        return temperature
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        logger.warning("Open-Meteo temperature lookup failed: %s", error)
        return None


def require_device_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.device_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device API key",
        )


def latest_query(device_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    query = (
        supabase.table("telemetry")
        .select("*")
        .order("recorded_at", desc=True)
        .limit(limit)
    )
    if device_id:
        query = query.eq("device_id", device_id)
    return query.execute().data


app = FastAPI(title="ESP GPS Tracker API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.frontend_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/telemetry", status_code=status.HTTP_201_CREATED)
def receive_telemetry(
    payload: TelemetryPayload,
    _: None = Depends(require_device_key),
) -> dict[str, Any]:
    if not payload.gps.fix or payload.gps.latitude is None or payload.gps.longitude is None:
        resolved_gps = resolve_wifi_location(payload)
        if resolved_gps:
            payload.gps = resolved_gps

    if payload.temperature_c is None:
        payload.temperature_c = resolve_weather_temperature(payload.device_id, payload.gps)
        if payload.temperature_c is not None:
            payload.temperature_source = "open_meteo"
    elif payload.temperature_source is None:
        payload.temperature_source = "device_sensor"

    row = payload.model_dump(mode="json")
    # The ESP32 may not have synchronized NTP yet. Let Supabase use its
    # server-side default instead of inserting an explicit NULL timestamp.
    if row["recorded_at"] is None:
        row.pop("recorded_at")
    # The database stores GPS and scan arrays as JSONB, preserving the ESP payload.
    result = supabase.table("telemetry").insert(row).execute()
    return {"accepted": True, "telemetry": result.data[0] if result.data else None}


@app.get("/api/devices")
def list_devices() -> list[dict[str, Any]]:
    rows = (
        supabase.table("telemetry")
        .select("device_id, recorded_at, gps, temperature_c, wifi_rssi")
        .order("recorded_at", desc=True)
        .limit(500)
        .execute()
        .data
    )
    devices: dict[str, dict[str, Any]] = {}
    for row in rows:
        devices.setdefault(row["device_id"], row)
    return list(devices.values())


@app.get("/api/telemetry/latest")
def get_latest(
    device_id: str | None = Query(default=None),
) -> dict[str, Any] | None:
    rows = latest_query(device_id=device_id, limit=1)
    return rows[0] if rows else None


@app.get("/api/telemetry/history")
def get_history(
    device_id: str = Query(min_length=1),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return latest_query(device_id=device_id, limit=limit)
