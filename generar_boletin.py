import os
import json
import requests
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Fechas reales
hoy = datetime.now(timezone.utc)
hace_30 = hoy - timedelta(days=30)

FECHA_HOY = hoy.strftime("%Y-%m-%d")
FECHA_30 = hace_30.strftime("%Y-%m-%d")

# Leer template
with open("prompt_template.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

# Leer whitelist externa (opcional)
whitelist = []
if os.path.exists("whitelist.txt"):
    with open("whitelist.txt", "r", encoding="utf-8") as w:
        whitelist = [line.strip() for line in w if line.strip()]

# Inyectar fechas y whitelist en el prompt
prompt = (
    prompt
    .replace("{{FECHA_HOY}}", FECHA_HOY)
    .replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)
    .replace("{{WHITELIST_DOMAINS}}", "\n".join(whitelist) if whitelist else "")
)

# Preparar payload y headers
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": os.environ.get("API_TOKEN", "")
}
payload = {
    "contents": [
        {"parts": [{"text": prompt}]}
    ],
    "tools": [{"googleSearch": {}}]
}

# Configurar sesión con reintentos y timeouts
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"])
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# Llamada con timeout (10s conexión, 90s lectura)
r = session.post(
    url,
    headers=headers,
    json=payload,
    timeout=(10, 90)
)
r.raise_for_status()
data = r.json()
html = data["candidates"][0]["content"]["parts"][0]["text"]

# Guardar salidas localmente
os.makedirs("content", exist_ok=True)

with open("content/boletin.html", "w", encoding="utf-8") as f:
    f.write(html)

# Guardar raw.json para depuración local (NO commitear)
with open("content/raw.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
