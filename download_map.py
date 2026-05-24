import urllib.request, json

url = "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson"
data = urllib.request.urlopen(url, timeout=15).read()
parsed = json.loads(data)
print(f"Features: {len(parsed['features'])}, Size: {len(data)/1024:.0f} KB")
with open("world_map.geojson", "wb") as f:
    f.write(data)
print("Saved world_map.geojson")
