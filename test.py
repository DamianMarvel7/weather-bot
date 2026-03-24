import requests

BASE = "https://gamma-api.polymarket.com"

tags = requests.get(f"{BASE}/tags").json()

weather_like_tags = []
keywords = ["weather", "climate", "warming", "temperature", "environment"]

for t in tags:
    label = t.get("label", "").lower()
    if any(k in label for k in keywords):
        weather_like_tags.append((t["id"], label))

print("Weather-related tags:")
for tid, label in weather_like_tags:
    print(tid, label)