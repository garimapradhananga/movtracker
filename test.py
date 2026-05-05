import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("TMDB_API_KEY")
url = f"https://api.themoviedb.org/3/search/movie?api_key={API_KEY}&query=Inception"

response = requests.get(url)
data = response.json()

print(data["results"][0]["title"])
print(data["results"][0]["overview"])