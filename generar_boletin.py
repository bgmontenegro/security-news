import os
import json
import requests
from datetime import datetime, timedelta

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
url = "https://models.inference.ai.azure.com/chat/completions"
headers = {
    "Authorization": f"Bearer {os.environ['API_TOKEN']}",
    "Content-Type": "application/json"
}

payload = {
    "model": "gemini-2.5-flash",
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.2
}

r = requests.post(url, headers=headers, json=payload)
r.raise_for_status()

data = r.json()
html = data["choices"][0]["message"]["content"]

# Guardar salidas
os.makedirs("content", exist_ok=True)

with open("content/boletin.html", "w", encoding="utf-8") as f:
    f.write(html)

with open("content/raw.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
