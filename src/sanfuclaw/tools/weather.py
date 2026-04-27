"""Weather tool — wttr.in, no API key needed.

We added this because the agent was previously answering weather queries
via web_search, which only sees search-result snippets. Specific numbers
(humidity, wind speed, sunrise) often aren't in snippets, so the model
filled the gaps from training-time priors — i.e. silently hallucinated
plausible values. wttr.in returns structured JSON so the model just
formats facts it actually has.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session


class WeatherTool:
    """Look up weather and forecast for a location via wttr.in."""

    name = "weather"
    description = (
        "Look up current weather and short-term forecast for a city or "
        "geographic location. Returns structured data: temperature, "
        "humidity, wind, conditions, sunrise/sunset, plus a multi-day "
        "forecast with hourly breakdown. ALWAYS prefer this tool over "
        "web_search for any weather question — search snippets only "
        "carry headline conditions, and using them risks fabricating "
        "specific numbers (humidity, wind speed, etc.) that weren't in "
        "the snippet."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "City name (e.g. 'Beijing', 'Shanghai', '武汉', 'Tokyo'), "
                    "airport IATA code (e.g. 'PEK', 'JFK'), or 'lat,lon' "
                    "(e.g. '30.59,114.27')."
                ),
            },
            "days": {
                "type": "integer",
                "description": (
                    "Number of forecast days to include (0-3). 0 = current "
                    "conditions only. Default 1 (today + tomorrow)."
                ),
            },
        },
        "required": ["location"],
    }

    def __init__(self, timeout: int = 15, lang: str = "zh", impersonate: str = "chrome120"):
        self._timeout = timeout
        self._lang = lang
        self._impersonate = impersonate

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        location = (params.get("location") or "").strip()
        if not location:
            raise ToolError("No location provided")
        try:
            days = int(params.get("days") if params.get("days") is not None else 1)
        except (TypeError, ValueError):
            days = 1
        days = max(0, min(days, 3))

        url = f"https://wttr.in/{urllib.parse.quote(location)}"
        try:
            async with AsyncSession() as client:
                response = await client.get(
                    url,
                    params={"format": "j1", "lang": self._lang},
                    timeout=self._timeout,
                    impersonate=self._impersonate,
                    allow_redirects=True,
                    verify=False,
                )
        except RequestsError as e:
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                raise ToolError(f"Weather lookup timed out after {self._timeout}s")
            raise ToolError(f"Weather lookup failed: {e}")
        except Exception as e:
            raise ToolError(f"Weather lookup failed: {e}")

        if not 200 <= response.status_code < 300:
            raise ToolError(f"HTTP {response.status_code} from wttr.in")

        try:
            data = response.json()
        except Exception:
            raise ToolError(
                f"wttr.in returned non-JSON for {location!r} — the location "
                "was probably not recognized. Try a different spelling, an "
                "airport code, or 'lat,lon'."
            )

        return _format_weather(data, self._lang, days)


# --- Formatting -------------------------------------------------------------

def _desc(d: dict, lang: str) -> str:
    """Pick localized description if present, fall back to English."""
    items = d.get(f"lang_{lang}") or d.get("weatherDesc") or []
    return items[0].get("value", "") if items else ""


def _hhmm(time_str: str) -> str:
    """wttr.in encodes hourly times as int-like strings: 0, 300, 600 ...
    2100. Convert to HH:MM."""
    try:
        n = int(time_str)
    except (TypeError, ValueError):
        return str(time_str)
    return f"{n // 100:02d}:{n % 100:02d}"


def _format_weather(data: dict, lang: str, days: int) -> str:
    nearest = (data.get("nearest_area") or [{}])[0]
    area = (nearest.get("areaName") or [{}])[0].get("value", "")
    region = (nearest.get("region") or [{}])[0].get("value", "")
    country = (nearest.get("country") or [{}])[0].get("value", "")
    lat = nearest.get("latitude", "")
    lon = nearest.get("longitude", "")
    location_label = ", ".join(s for s in [area, region, country] if s) or "(unknown)"

    cur = (data.get("current_condition") or [{}])[0]
    obs_time = cur.get("localObsDateTime", "")
    temp = cur.get("temp_C", "?")
    feels = cur.get("FeelsLikeC", "?")
    humidity = cur.get("humidity", "?")
    wind_kmh = cur.get("windspeedKmph", "?")
    wind_dir = cur.get("winddir16Point", "")
    pressure = cur.get("pressure", "?")
    uv = cur.get("uvIndex", "?")
    visibility = cur.get("visibility", "?")
    cloud = cur.get("cloudcover", "?")
    precip = cur.get("precipMM", "?")

    lines = [
        f"Weather for {location_label} (lat={lat}, lon={lon})",
        f"Current ({obs_time} local):",
        f"  {_desc(cur, lang)} — {temp}°C (feels {feels}°C)",
        f"  Humidity {humidity}%, wind {wind_dir} {wind_kmh} km/h",
        f"  UV {uv}, visibility {visibility} km, pressure {pressure} hPa, "
        f"cloud {cloud}%, precip {precip} mm",
    ]

    forecast_days = (data.get("weather") or [])[: days + 1] if days else []
    for i, day in enumerate(forecast_days):
        date_str = day.get("date", "")
        max_c = day.get("maxtempC", "?")
        min_c = day.get("mintempC", "?")
        astro = (day.get("astronomy") or [{}])[0]
        sunrise = astro.get("sunrise", "")
        sunset = astro.get("sunset", "")

        # Pick a few representative hourly slots so the forecast is
        # informative without dumping all 8 three-hour entries.
        hourly = day.get("hourly") or []
        picks = [h for h in hourly if h.get("time") in ("0", "600", "1200", "1800")]
        slots = []
        for h in picks:
            t = _hhmm(h.get("time", ""))
            slots.append(f"{t} {_desc(h, lang)} {h.get('tempC','?')}°C")

        label = "Today" if i == 0 else f"Day +{i}"
        lines.append("")
        lines.append(f"{label} ({date_str}):")
        lines.append(f"  High {max_c}°C / Low {min_c}°C, sunrise {sunrise}, sunset {sunset}")
        if slots:
            lines.append("  " + " | ".join(slots))

    return "\n".join(lines)
