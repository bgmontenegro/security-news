import os
import json
import requests
import re
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuración de fechas ---
hoy = datetime.now(timezone.utc)
hace_30 = hoy - timedelta(days=30)

FECHA_HOY = hoy.strftime("%Y-%m-%d")
FECHA_30 = hace_30.strftime("%Y-%m-%d")

# --- Leer template ---
with open("prompt_template.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

# Inyectar fechas en el prompt
prompt = prompt.replace("{{FECHA_HOY}}", FECHA_HOY).replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)

# Asegurar marcador final para detectar truncado
END_MARKER = "<!-- FIN_DE_BOLETIN -->"
if END_MARKER not in prompt:
    prompt = prompt.rstrip() + "\n\n" + END_MARKER + "\n"

# Leer whitelist externa (opcional)
whitelist_path = "config/whitelist.txt"
whitelist_lines = []
if os.path.exists(whitelist_path):
    with open(whitelist_path, "r", encoding="utf-8") as wf:
        whitelist_lines = [ln.strip() for ln in wf if ln.strip() and not ln.strip().startswith("#")]
if whitelist_lines:
    whitelist_block = "\nFUENTES PERMITIDAS (WHITELIST):\n" + "\n".join(whitelist_lines) + "\n"
    prompt = prompt + "\n" + whitelist_block

# --- Preparar payload y headers ---
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
headers = {
    "Content-Type": "application/json",
    "x-goog-api-key": os.environ.get("API_TOKEN", "")
}

payload = {
    "contents": [
        {"parts": [{"text": prompt}]}
    ],
    "tools": [{"googleSearch": {}}],
    # Si tu endpoint soporta control de tokens, ajusta:
    "maxOutputTokens": 2000
}

# --- Sesión con reintentos ---
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

# --- Helpers ---
def save_raw(data):
    os.makedirs("content", exist_ok=True)
    with open("content/raw.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def save_boletin(html):
    os.makedirs("content", exist_ok=True)
    with open("content/boletin.html", "w", encoding="utf-8") as f:
        f.write(html)

def log_error(msg):
    os.makedirs("content", exist_ok=True)
    with open("content/error.log", "a", encoding="utf-8") as ef:
        ef.write(f"{datetime.now().isoformat()} - {msg}\n")

def extract_text_from_response(data):
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None

def ends_with_incomplete_word(s):
    # Si termina en letra y la última "palabra" es muy corta (fragmento), consideramos truncado
    s = s.strip()
    if not s:
        return True
    tail = s[-40:]
    # Si termina con una letra y no hay espacio en los últimos 10 caracteres, posible truncado
    if tail[-1].isalpha() and tail[-10:].count(" ") == 0:
        return True
    return False

def is_valid_html_fragment(h):
    if not h or len(h) < 200:
        return False
    # Debe contener el marcador final
    if END_MARKER not in h:
        return False
    # Comprobar que no termina en palabra cortada
    if ends_with_incomplete_word(h.replace(END_MARKER, "")):
        return False
    # Comprobar que las secciones obligatorias existen (al menos resumen o cves)
    required_ids = ["id='resumen'", "id='incidentes'", "id='cves'", "id='ransomware'", "id='recomendaciones'"]
    # Si no contiene ninguna de las secciones, consideramos inválido
    if not any(req in h for req in required_ids):
        return False
    # Comprobación básica de tags balanceados (heurística)
    if h.count("<div") < h.count("</div>") or h.count("<section") < h.count("</section>"):
        return False
    return True

# --- Llamada y reintentos de contenido ---
MAX_ATTEMPTS = 3
attempt = 0
last_data = None

while attempt < MAX_ATTEMPTS:
    attempt += 1
    try:
        print(f"[generar_boletin] Intento {attempt} de {MAX_ATTEMPTS}...")
        r = session.post(url, headers=headers, json=payload, timeout=(10, 90))
        r.raise_for_status()
        data = r.json()
        last_data = data
        save_raw(data)  # siempre guardar raw para depuración
        html = extract_text_from_response(data)
        if is_valid_html_fragment(html):
            # Guardar boletín válido (remover marcador antes de guardar si lo deseas)
            html_to_save = html
            # Opcional: eliminar el marcador visible en el HTML final
            html_to_save = html_to_save.replace(END_MARKER, "")
            save_boletin(html_to_save)
            print("[generar_boletin] Boletín generado y guardado en content/boletin.html")
            break
        else:
            print("[generar_boletin] Respuesta inválida o truncada detectada.")
            log_error(f"Respuesta inválida en intento {attempt}. Guardado raw.json para inspección.")
            # Si quedan intentos, reintentar; si no, salir sin sobrescribir
            if attempt < MAX_ATTEMPTS:
                print("[generar_boletin] Reintentando generación...")
                continue
            else:
                print("[generar_boletin] Alcanzado máximo de reintentos. No se sobrescribirá el boletín actual.")
                log_error("Generación inválida tras reintentos. Revisar content/raw.json")
                raise SystemExit("Generación inválida: revisar content/raw.json")
    except requests.exceptions.RequestException as e:
        log_error(f"HTTP error en intento {attempt}: {str(e)}")
        print(f"[generar_boletin] Error HTTP: {e}")
        if attempt >= MAX_ATTEMPTS:
            raise
        else:
            continue
    except Exception as ex:
        log_error(f"Error inesperado en intento {attempt}: {str(ex)}")
        print(f"[generar_boletin] Error inesperado: {ex}")
        if attempt >= MAX_ATTEMPTS:
            raise
        else:
            continue
