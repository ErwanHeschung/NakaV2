"""Weather from Open-Meteo: no account, no key.

The place is set once in the panel and geocoded then, so a question about
the weather costs one request and never a guess at which Paris was meant.
"""

from datetime import date, datetime

from ..tools.registry import tool
from .base import Connection, Failed, Setting, Step

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as Open-Meteo documents them, in words
# she can say.
CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def _sky(code) -> str:
    return CODES.get(int(code or 0), "unsettled")


class Weather(Connection):
    name = "weather"
    label = "Weather"
    icon = "cloud-sun"
    blurb = ("Current conditions and the forecast for one place, from "
             "Open-Meteo. No account needed.")
    settings = [
        Setting("city", "Place",
                help="A town or city. Found once when you press Save and "
                     "check, then remembered."),
    ]

    def guide(self) -> list[Step]:
        return [Step("Type your town and press Save and check. Nothing else "
                     "to set up.", "https://open-meteo.com")]

    def configured(self) -> bool:
        conf = self.conf()
        return bool(conf.get("place")) and bool(conf.get("latitude")
                                                or conf.get("longitude"))

    def extra(self) -> dict:
        conf = self.conf()
        return {"place": conf.get("place", "")}

    def geocode(self, city: str) -> dict:
        response = self.http("GET", GEOCODE, params={
            "name": city, "count": 1, "language": "en", "format": "json"})
        if response.status_code != 200:
            raise Failed(f"Open-Meteo could not look up {city!r} "
                         f"(HTTP {response.status_code}).")
        found = (response.json().get("results") or [None])[0]
        if not found:
            raise Failed(f"No place called {city!r} was found.")
        place = ", ".join(p for p in (found.get("name"), found.get("admin1"),
                                      found.get("country")) if p)
        return {"latitude": float(found["latitude"]),
                "longitude": float(found["longitude"]),
                "place": place, "timezone": found.get("timezone", "")}

    def test(self) -> str:
        city = str(self.conf().get("city", "")).strip()
        if not city:
            raise Failed("Type a place first.")
        # Geocoded again only when the city changed: the coordinates are
        # what every forecast uses, and a lookup per question would be waste.
        if city != self.conf().get("geocoded_from"):
            self.save(**self.geocode(city), geocoded_from=city)
        now = self.forecast()["current"]
        return (f"{self.conf()['place']}: {round(now['temperature_2m'])}°C, "
                f"{_sky(now['weather_code'])}.")

    def forecast(self) -> dict:
        conf = self.conf()
        if not self.configured():
            raise Failed("No place is set for the weather yet.")
        response = self.http("GET", FORECAST, params={
            "latitude": conf["latitude"], "longitude": conf["longitude"],
            "current": "temperature_2m,apparent_temperature,"
                       "relative_humidity_2m,precipitation,weather_code,"
                       "wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,precipitation_sum,"
                     "sunrise,sunset",
            "timezone": "auto", "forecast_days": 7})
        if response.status_code != 200:
            raise Failed(f"Open-Meteo answered HTTP {response.status_code}.")
        return response.json()


weather = Weather()


def _day(daily: dict, index: int) -> str:
    when = date.fromisoformat(daily["time"][index])
    rain = daily["precipitation_probability_max"][index]
    line = (f"{when:%A %d %B}: {_sky(daily['weather_code'][index])}, "
            f"{round(daily['temperature_2m_min'][index])} to "
            f"{round(daily['temperature_2m_max'][index])}°C")
    if rain is not None:
        line += f", {rain}% chance of rain"
    total = daily["precipitation_sum"][index]
    if total:
        line += f" ({total} mm)"
    return line


@tool(
    description="The weather right now where the user lives.",
    parameters={},
    power="conn.weather",
    label="Weather now",
    summary="Current conditions where you are.",
)
def weather_now():
    data = weather.forecast()
    now = data["current"]
    daily = data["daily"]
    return (f"In {weather.conf()['place']} it is {round(now['temperature_2m'])}°C "
            f"(feels like {round(now['apparent_temperature'])}°C), "
            f"{_sky(now['weather_code'])}, wind {round(now['wind_speed_10m'])} "
            f"km/h, humidity {now['relative_humidity_2m']}%. Today: "
            + _day(daily, 0) + ". Sunset at "
            + datetime.fromisoformat(daily["sunset"][0]).strftime("%H:%M") + ".")


@tool(
    description="The weather forecast where the user lives, for today, "
                "tomorrow, or the coming week.",
    parameters={"when": {"type": "string",
                         "enum": ["today", "tomorrow", "week"],
                         "description": "Which days."}},
    required=["when"],
    power="conn.weather",
    label="Forecast",
    summary="Today, tomorrow or the week ahead.",
)
def weather_forecast(when: str):
    daily = weather.forecast()["daily"]
    place = weather.conf()["place"]
    if when == "today":
        return f"{place}. " + _day(daily, 0) + "."
    if when == "tomorrow":
        return f"{place}. " + _day(daily, 1) + "."
    if when == "week":
        return f"{place}. " + "; ".join(
            _day(daily, i) for i in range(len(daily["time"]))) + "."
    raise ValueError("when must be today, tomorrow or week")
