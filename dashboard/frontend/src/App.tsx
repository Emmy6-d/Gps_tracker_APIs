import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";

type Gps = {
  fix: boolean;
  satellites: number;
  latitude: number | null;
  longitude: number | null;
  altitude_m: number | null;
  speed_kph: number | null;
  source?: string | null;
  accuracy_m?: number | null;
};

type Telemetry = {
  id: number;
  device_id: string;
  firmware_version: string | null;
  uptime_seconds: number;
  temperature_c: number | null;
  temperature_source: string | null;
  wifi_rssi: number | null;
  free_heap_bytes: number | null;
  recorded_at: string;
  received_at: string;
  gps: Gps;
  wifi_access_points: { bssid: string; ssid: string; rssi: number; channel: number }[];
};

const API_URL = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8001").replace(/\/$/, "");

function formatValue(value: number | null | undefined, suffix = "") {
  return value == null ? "—" : `${value}${suffix}`;
}

function FitToLocation({ gps }: { gps: Gps }) {
  const map = useMap();
  useEffect(() => {
    if (gps.fix && gps.latitude !== null && gps.longitude !== null) {
      map.setView([gps.latitude, gps.longitude], 15);
    }
  }, [gps.fix, gps.latitude, gps.longitude, map]);
  return null;
}

function App() {
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [history, setHistory] = useState<Telemetry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTelemetry = useCallback(async () => {
    try {
      const latestResponse = await fetch(`${API_URL}/api/telemetry/latest`);
      if (!latestResponse.ok) throw new Error("Unable to reach the telemetry API");
      const latest = (await latestResponse.json()) as Telemetry | null;
      setTelemetry(latest);
      if (latest) {
        const historyResponse = await fetch(
          `${API_URL}/api/telemetry/history?device_id=${encodeURIComponent(latest.device_id)}&limit=30`,
        );
        if (historyResponse.ok) setHistory((await historyResponse.json()) as Telemetry[]);
      }
      setError(null);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "Request failed";
      setError(`${message}. API: ${API_URL}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTelemetry();
    const timer = window.setInterval(() => void loadTelemetry(), 10000);
    return () => window.clearInterval(timer);
  }, [loadTelemetry]);

  const gps = telemetry?.gps;
  const hasLocation = Boolean(gps?.fix && gps.latitude !== null && gps.longitude !== null);
  const coordinates = hasLocation ? `${gps!.latitude!.toFixed(6)}, ${gps!.longitude!.toFixed(6)}` : "No GPS fix";
  const lastSeen = telemetry ? new Date(telemetry.recorded_at).toLocaleString() : "—";
  const mapCenter: [number, number] = hasLocation ? [gps!.latitude!, gps!.longitude!] : [20, 0];
  const chartPoints = useMemo(() => history.slice().reverse(), [history]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">FIELD OPERATIONS / TELEMETRY</div>
          <h1>Tracker command center</h1>
        </div>
        <div className={`connection ${telemetry ? "online" : "offline"}`}>
          <span className="pulse" /> {telemetry ? "Receiving data" : "Waiting for device"}
        </div>
      </header>

      {error && <div className="alert">{error}. Check that FastAPI is running and CORS is configured.</div>}
      {loading && <div className="loading">Loading telemetry…</div>}

      {!loading && !telemetry ? (
        <section className="empty-state"><span className="empty-icon">⌁</span><h2>No telemetry yet</h2><p>Power the tracker and send its first reading to see the dashboard.</p></section>
      ) : telemetry ? (
        <>
          <section className="device-row">
            <div><span className="label">ACTIVE DEVICE</span><strong>{telemetry.device_id}</strong></div>
            <div><span className="label">LAST READING</span><strong>{lastSeen}</strong></div>
            <div><span className="label">FIRMWARE</span><strong>{telemetry.firmware_version ?? "—"}</strong></div>
            <div><span className="label">GPS STATUS</span><strong className={hasLocation ? "good" : "muted"}>{hasLocation ? "FIX ACQUIRED" : "NO FIX"}</strong></div>
          </section>

          <section className="metrics">
            <Metric title="Coordinates" value={coordinates} detail={hasLocation ? `${gps!.source?.includes("wifi") ? "Wi-Fi estimate" : `${gps!.satellites} satellites`}${gps!.accuracy_m ? ` · ±${Math.round(gps!.accuracy_m)} m` : ""}` : "Waiting for location estimate"} accent="blue" />
            <Metric title="Temperature" value={formatValue(telemetry.temperature_c, "°C")} detail={telemetry.temperature_source === "open_meteo" ? "Live local weather estimate" : telemetry.temperature_source === "device_sensor" ? "ESP sensor" : "Waiting for location"} accent="orange" />
            <Metric title="Speed" value={formatValue(gps?.speed_kph, " km/h")} detail={hasLocation ? "Current movement" : "Unavailable without fix"} accent="purple" />
            <Metric title="Wi-Fi signal" value={formatValue(telemetry.wifi_rssi, " dBm")} detail={`${telemetry.wifi_access_points.length} networks scanned`} accent="green" />
          </section>

          <section className="main-grid">
            <div className="panel map-panel">
              <div className="panel-heading"><div><span className="label">LIVE POSITION</span><h2>Tracker location</h2></div><span className={`status-tag ${hasLocation ? "active" : ""}`}>{hasLocation ? "LIVE" : "NO SIGNAL"}</span></div>
              <div className="map-wrap">
                <MapContainer center={mapCenter} zoom={hasLocation ? 15 : 2} scrollWheelZoom className="map">
                  <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <FitToLocation gps={gps!} />
                  {hasLocation && <CircleMarker center={[gps!.latitude!, gps!.longitude!]} radius={10} pathOptions={{ color: "#f8b84e", fillColor: "#f8b84e", fillOpacity: 0.9 }}><Popup><strong>{telemetry.device_id}</strong><br />{coordinates}<br />{lastSeen}</Popup></CircleMarker>}
                </MapContainer>
                {!hasLocation && <div className="map-overlay"><div className="target">◎</div><strong>Location unavailable</strong><span>Waiting for a valid GPS fix from the device</span></div>}
              </div>
              <div className="coordinates-bar"><span>LAT / LONG</span><strong>{coordinates}</strong><span>ALTITUDE</span><strong>{formatValue(gps?.altitude_m, " m")}</strong></div>
            </div>

            <div className="side-column">
              <div className="panel health-panel"><div className="panel-heading"><div><span className="label">DEVICE HEALTH</span><h2>System status</h2></div></div><HealthRow label="Uptime" value={`${Math.floor(telemetry.uptime_seconds / 3600)}h ${Math.floor((telemetry.uptime_seconds % 3600) / 60)}m`} /><HealthRow label="Free memory" value={formatValue(telemetry.free_heap_bytes, " bytes")} /><HealthRow label="Access points" value={`${telemetry.wifi_access_points.length} detected`} /></div>
              <div className="panel history-panel"><div className="panel-heading"><div><span className="label">RECENT ACTIVITY</span><h2>Signal trend</h2></div></div><div className="bars">{chartPoints.length ? chartPoints.slice(-12).map((point) => <div key={point.id} className="bar" style={{ height: `${Math.max(12, Math.min(100, (point.wifi_rssi ? point.wifi_rssi + 100 : 10) * 2.2))}%` }} title={`${point.wifi_rssi ?? "—"} dBm`} />) : <span className="muted">No history available</span>}</div><div className="chart-axis"><span>older</span><span>latest</span></div></div>
            </div>
          </section>
        </>
      ) : null}
    </main>
  );
}

function Metric({ title, value, detail, accent }: { title: string; value: string; detail: string; accent: string }) {
  return <div className={`metric ${accent}`}><span className="label">{title}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function HealthRow({ label, value }: { label: string; value: string }) {
  return <div className="health-row"><span>{label}</span><strong>{value}</strong></div>;
}

export default App;
