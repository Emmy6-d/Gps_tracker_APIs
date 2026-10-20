#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>

// Keep these values device-specific. Do not commit real credentials.
const char *WIFI_SSID = "MERITE EQ";
const char *WIFI_PASSWORD = "laurent2";

// Use the FastAPI server's LAN address; localhost is the ESP32 itself.
const char *TELEMETRY_URL =
      "http://192.168.0.103:8001/api/telemetry";
const char *DEVICE_API_KEY = "YOUR_DEVICE_API_KEY";
const char *DEVICE_ID = "ESP32_Tracker_01";
const char *FIRMWARE_VERSION = "1.0.0";

const unsigned long TELEMETRY_INTERVAL_MS = 10000UL;
const unsigned long WIFI_RECONNECT_INTERVAL_MS = 5000UL;
const unsigned long HTTP_TIMEOUT_MS = 5000UL;
const uint8_t MAX_ACCESS_POINTS = 10;

unsigned long lastTelemetryAt = 0;
unsigned long lastWifiReconnectAt = 0;

// No temperature hardware is required for this build. The backend fills this
// field from Open-Meteo after it resolves the ESP's position from Wi-Fi.
float readTemperatureCelsius() {
  return NAN;
}

// No GPS module is required. The backend uses wifi_access_points with a
// geolocation API to estimate the ESP's position.
bool readGps(double &latitude, double &longitude, double &altitudeMeters,
            double &speedKph, uint8_t &satellites, bool &fix) {
  latitude = NAN;
  longitude = NAN;
  altitudeMeters = NAN;
  speedKph = NAN;
  satellites = 0;
  fix = false;
  return false;
}

void connectToWifi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("Connecting to Wi-Fi: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < 15000UL) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" connected");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(" failed");
  }
}

void configureClock() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
}

void addNullable(JsonObject object, const char *key, double value) {
  if (isnan(value)) object[key] = nullptr;
  else object[key] = value;
}

bool buildTelemetryPayload(String &payload) {
  JsonDocument doc;
  doc["schema_version"] = 1;
  doc["device_id"] = DEVICE_ID;
  doc["firmware_version"] = FIRMWARE_VERSION;
  doc["uptime_seconds"] = millis() / 1000UL;
  addNullable(doc.as<JsonObject>(), "temperature_c", readTemperatureCelsius());
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["free_heap_bytes"] = ESP.getFreeHeap();

  struct tm timeInfo;
  if (getLocalTime(&timeInfo, 1000)) {
    char timestamp[25];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &timeInfo);
    doc["recorded_at"] = timestamp;
  } else {
    doc["recorded_at"] = nullptr;
  }

  JsonObject gps = doc["gps"].to<JsonObject>();
  double latitude, longitude, altitudeMeters, speedKph;
  uint8_t satellites;
  bool fix;
  readGps(latitude, longitude, altitudeMeters, speedKph, satellites, fix);
  gps["fix"] = fix;
  gps["satellites"] = satellites;
  addNullable(gps, "latitude", latitude);
  addNullable(gps, "longitude", longitude);
  addNullable(gps, "altitude_m", altitudeMeters);
  addNullable(gps, "speed_kph", speedKph);

  JsonArray accessPoints = doc["wifi_access_points"].to<JsonArray>();
  int networkCount = WiFi.scanNetworks(false, true);
  if (networkCount < 0) networkCount = 0;

  int count = min(networkCount, static_cast<int>(MAX_ACCESS_POINTS));
  for (int i = 0; i < count; ++i) {
    JsonObject accessPoint = accessPoints.add<JsonObject>();
    accessPoint["bssid"] = WiFi.BSSIDstr(i);
    accessPoint["ssid"] = WiFi.SSID(i);
    accessPoint["rssi"] = WiFi.RSSI(i);
    accessPoint["channel"] = WiFi.channel(i);
  }

  serializeJson(doc, payload);
  WiFi.scanDelete();
  return payload.length() > 0;
}

bool sendTelemetry(const String &payload) {
  HTTPClient http;
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);

  if (!http.begin(TELEMETRY_URL)) {
    Serial.println("Could not initialize HTTP client");
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("Accept", "application/json");
  http.addHeader("X-Device-ID", DEVICE_ID);
  http.addHeader("X-API-Key", DEVICE_API_KEY);

  int responseCode = http.POST(payload);
  bool success = responseCode >= 200 && responseCode < 300;
  Serial.printf("Telemetry response: %d%s\n", responseCode,
                success ? " (accepted)" : " (not accepted)");
  if (responseCode > 0) Serial.println(http.getString());
  else Serial.println(http.errorToString(responseCode));

  http.end();
  return success;
}

void setup() {
  Serial.begin(115200);
  delay(100);
  connectToWifi();
  configureClock();
}

void loop() {
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiReconnectAt >= WIFI_RECONNECT_INTERVAL_MS) {
      lastWifiReconnectAt = now;
      connectToWifi();
    }
    delay(10);
    return;
  }

  if (now - lastTelemetryAt < TELEMETRY_INTERVAL_MS) {
    delay(10);
    return;
  }
  lastTelemetryAt = now;

  String payload;
  if (buildTelemetryPayload(payload)) {
    Serial.println("Telemetry payload:");
    Serial.println(payload);
    sendTelemetry(payload);
  } else {
    Serial.println("Could not build telemetry payload");
  }
}
