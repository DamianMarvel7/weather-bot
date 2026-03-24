from src.weatherbot.weatherbet import get_polymarket_event
from datetime import datetime, timezone, timedelta

today    = datetime.now(timezone.utc).date().isoformat()
tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()

for city in ['atlanta', 'chicago', 'seoul', 'london', 'miami', 'tokyo']:
    event = get_polymarket_event(city, today) or get_polymarket_event(city, tomorrow)
    if event:
        print(f"FOUND {city}: {event['title']}")
        print(f"  hours_left={event['hours_left']:.1f}  volume=${event['volume']:.0f}")
        for o in event['outcomes']:
            print(f"  {o['label']:30} bid={o['bid']}  ask={o['ask']}")
    else:
        print(f"NOT FOUND {city}")
    print()
