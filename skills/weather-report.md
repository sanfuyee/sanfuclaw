---
name: weather-report
description: Format a friendly weather report for a given city using web_fetch.
---

When the user asks about weather in a city:

1. Use the `web_fetch` tool to get current conditions from
   `https://wttr.in/<city>?format=j1` (JSON format).
2. Parse the JSON and extract: temperature (C), feels-like, condition,
   humidity, wind speed.
3. Reply in this exact format:

   ```
   📍 <city>
   🌡  <temp>°C (feels like <feels>°C)
   ☁️  <condition>
   💧 Humidity: <humidity>%
   💨 Wind: <wind> km/h
   ```

4. Keep the reply to these 5 lines — no extra commentary unless the user asks.
