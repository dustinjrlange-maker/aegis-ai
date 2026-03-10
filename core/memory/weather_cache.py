"""
Weather Service -- Aegis AI
Fetches weather from Open-Meteo API with in-memory caching.
"""

import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache duration in seconds (30 minutes for current, 1 hour for full forecast)
_CACHE_TTL = 1800
_FULL_CACHE_TTL = 3600


class WeatherService:
    """Fetches current weather from Open-Meteo (free, no API key)."""

    def __init__(self, data_dir=None):
        self._cache = {}
        self._cache_time = 0.0
        self._cache_times = {}  # Per-key timestamps for full forecast cache
        # Persist user location preference
        self._location_file = Path(data_dir) / "weather_location.json" if data_dir else None
        self._location = self._load_location()

    def _load_location(self):
        """Load saved location preference."""
        if self._location_file and self._location_file.exists():
            try:
                with open(self._location_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def set_location(self, lat: float, lon: float, name: str = ""):
        """Set and persist the user's weather location."""
        self._location = {"lat": lat, "lon": lon, "name": name}
        if self._location_file:
            self._location_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._location_file, "w", encoding="utf-8") as f:
                json.dump(self._location, f, indent=2)
        # Invalidate cache
        self._cache = {}
        self._cache_time = 0.0
        self._cache_times = {}
        return self._location

    def get_location(self):
        """Get the saved location or None."""
        return self._location

    def get_weather(self, lat: float | None = None, lon: float | None = None,
                     detail: str = "current"):
        """Fetch weather data. Uses saved location if lat/lon not provided.

        Args:
            lat: Latitude (optional if location is saved).
            lon: Longitude (optional if location is saved).
            detail: "current" for current conditions only (default),
                    "full" for current + 24h hourly + 7-day daily forecast.
        """
        if lat is None or lon is None:
            if self._location:
                lat = self._location["lat"]
                lon = self._location["lon"]
            else:
                return {"error": "No location set. Use /api/weather/location to set one."}

        if detail == "full":
            return self._get_full_weather(lat, lon)

        # Check cache (current-only)
        cache_key = f"{lat:.4f},{lon:.4f}"
        if cache_key in self._cache and (time.time() - self._cache_time) < _CACHE_TTL:
            return self._cache[cache_key]

        try:
            import requests
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,weather_code,"
                f"wind_speed_10m,relative_humidity_2m"
                f"&temperature_unit=fahrenheit"
                f"&wind_speed_unit=mph"
                f"&timezone=auto"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current", {})
            result = {
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "weather_code": current.get("weather_code"),
                "condition": self._weather_code_to_text(current.get("weather_code", 0)),
                "wind_speed": current.get("wind_speed_10m"),
                "humidity": current.get("relative_humidity_2m"),
                "location": self._location.get("name", "") if self._location else "",
                "updated": data.get("current", {}).get("time", ""),
            }

            self._cache[cache_key] = result
            self._cache_time = time.time()
            return result

        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)
            return {"error": f"Could not fetch weather: {e}"}

    def _get_full_weather(self, lat: float, lon: float):
        """Fetch current + hourly (24h) + daily (7-day) forecast."""
        cache_key = f"{lat:.4f},{lon:.4f}_full"
        cached_time = self._cache_times.get(cache_key, 0.0)
        if cache_key in self._cache and (time.time() - cached_time) < _FULL_CACHE_TTL:
            return self._cache[cache_key]

        try:
            import requests
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,apparent_temperature,weather_code,"
                f"wind_speed_10m,relative_humidity_2m"
                f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
                f"&forecast_hours=24"
                f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code"
                f"&forecast_days=7"
                f"&temperature_unit=fahrenheit"
                f"&wind_speed_unit=mph"
                f"&timezone=auto"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            # Parse current conditions
            current_data = data.get("current", {})
            current = {
                "temperature": current_data.get("temperature_2m"),
                "feels_like": current_data.get("apparent_temperature"),
                "weather_code": current_data.get("weather_code"),
                "condition": self._weather_code_to_text(current_data.get("weather_code", 0)),
                "wind_speed": current_data.get("wind_speed_10m"),
                "humidity": current_data.get("relative_humidity_2m"),
                "location": self._location.get("name", "") if self._location else "",
                "updated": current_data.get("time", ""),
            }

            # Parse hourly forecast
            hourly_data = data.get("hourly", {})
            hourly_times = hourly_data.get("time", [])
            hourly_temps = hourly_data.get("temperature_2m", [])
            hourly_precip = hourly_data.get("precipitation_probability", [])
            hourly_wind = hourly_data.get("wind_speed_10m", [])
            hourly = []
            for i in range(len(hourly_times)):
                hourly.append({
                    "hour": hourly_times[i],
                    "temp": hourly_temps[i] if i < len(hourly_temps) else None,
                    "precip_prob": hourly_precip[i] if i < len(hourly_precip) else None,
                    "wind": hourly_wind[i] if i < len(hourly_wind) else None,
                })

            # Parse daily forecast
            daily_data = data.get("daily", {})
            daily_dates = daily_data.get("time", [])
            daily_highs = daily_data.get("temperature_2m_max", [])
            daily_lows = daily_data.get("temperature_2m_min", [])
            daily_precip = daily_data.get("precipitation_sum", [])
            daily_codes = daily_data.get("weather_code", [])
            daily = []
            for i in range(len(daily_dates)):
                code = daily_codes[i] if i < len(daily_codes) else 0
                daily.append({
                    "date": daily_dates[i],
                    "high": daily_highs[i] if i < len(daily_highs) else None,
                    "low": daily_lows[i] if i < len(daily_lows) else None,
                    "precip_sum": daily_precip[i] if i < len(daily_precip) else None,
                    "condition": self._weather_code_to_text(code),
                    "weather_code": code,
                })

            result = {
                "current": current,
                "hourly": hourly,
                "daily": daily,
            }

            self._cache[cache_key] = result
            self._cache_times[cache_key] = time.time()
            return result

        except Exception as e:
            logger.warning("Full weather fetch failed: %s", e)
            return {"error": f"Could not fetch weather forecast: {e}"}

    @staticmethod
    def _weather_code_to_text(code: int) -> str:
        """Convert WMO weather code to human text."""
        codes = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy",
            3: "Overcast", 45: "Foggy", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            77: "Snow grains", 80: "Slight rain showers",
            81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail",
        }
        return codes.get(code, f"Unknown ({code})")
