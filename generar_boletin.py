import os
import json
import requests
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Fechas reales (timezone-aware)
hoy = datetime.now(timezone.utc)
hace_30 = hoy - timedelta(days=30)

FECHA_HOY = hoy.strftime("%Y-%m-%d")
FECHA_30 = hace_30.strftime("%Y-%m-%d")

# Leer template
with open("prompt_template.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

# Leer whitelist externa (opcional)
whitelist_path = "config/whitelist.txt"
whitelist_lines = []
if os.path.exists(whitelist_path):
    with open(whitelist_path, "r", encoding="utf-8") as wf:
        whitelist_lines = [ln.strip() for ln in wf if ln.strip() and not ln.strip().startswith("#")]

# Inyectar fechas y whitelist en el prompt
prompt = (
    prompt
    .replace("{{FECHA_HOY}}", FECHA_HOY)
    .replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)
)

if whitelist_lines:
    whitelist_block = "\nFUENTES PERMITIDAS (WHITELIST):\n" + "\n".join(whitelist_lines) + "\n"
    prompt = prompt + "\n" + whitelist_block

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

# Guardar salidas
os.makedirs("content", exist_ok=True)

with open("content/boletin.html", "w", encoding="utf-8") as f:
    f.write(html)

# Guardar raw.json localmente para depuración, pero NO versionarlo
with open("content/raw.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
