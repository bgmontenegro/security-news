import os
import json
import requests
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Fechas reales
hoy = datetime.utcnow()
hace_30 = hoy - timedelta(days=30)

FECHA_HOY = hoy.strftime("%Y-%m-%d")
FECHA_30 = hace_30.strftime("%Y-%m-%d")

# Leer template
with open("prompt_template.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

prompt = (
    prompt
    .replace("{{FECHA_HOY}}", FECHA_HOY)
    .replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)
)

# Llamada a Gemini (GitHub Models)
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": os.environ["API_TOKEN"]
}

payload = {
    "contents": [
        {
            "parts": [
                {"text": prompt}
            ]
        }
    ],
    "tools": [
        {
            "googleSearch": {} # Esto habilita la búsqueda en internet en tiempo real
        }
    ]    
}

session = requests.Session()

retries = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"]
)

adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)

r = session.post(
    url,
    headers=headers,
    json=payload,
    timeout=30
)

r.raise_for_status()

data = r.json()
html = data["candidates"][0]["content"]["parts"][0]["text"]

# Guardar salidas
os.makedirs("content", exist_ok=True)

with open("content/boletin.html", "w", encoding="utf-8") as f:
    f.write(html)

with open("content/raw.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
