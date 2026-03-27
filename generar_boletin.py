import os
import json
import requests
import sys
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Fechas ---
hoy = datetime.now(timezone.utc)
hace_30 = hoy - timedelta(days=30)
FECHA_HOY = hoy.strftime("%Y-%m-%d")
FECHA_30 = hace_30.strftime("%Y-%m-%d")

# --- Leer prompt ---
with open("prompt_template.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

prompt = prompt.replace("{{FECHA_HOY}}", FECHA_HOY).replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)

# Asegurar marcador final para detectar truncado
END_MARKER = "<!-- FIN_DE_BOLETIN -->"
if END_MARKER not in prompt:
    prompt = prompt.rstrip() + "\n\n" + END_MARKER + "\n"

# --- Headers / API token handling ---
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
if not API_TOKEN:
    print("[generar_boletin] ERROR: API_TOKEN no definido en el entorno.", file=sys.stderr)
    raise SystemExit("API_TOKEN missing")

# Construir dos opciones de cabecera; usaremos la que funcione
headers_key = {"Content-Type": "application/json", "x-goog-api-key": API_TOKEN}
headers_bearer = {"Content-Type": "application/json", "Authorization": f"Bearer {API_TOKEN}"}

# --- Payload conservador (sin 'tools' ni campos experimentales) ---
payload = {
    "contents": [
        {"parts": [{"text": prompt}]}
    ]
}

# --- Sesión con reintentos ---
session = requests.Session()
retries = Retry(total=5, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["GET","POST"]))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Helpers ---
def ensure_content_dir():
    os.makedirs("content", exist_ok=True)

def save_raw(data):
    ensure_content_dir()
    with open("content/raw.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def save_boletin(html):
    ensure_content_dir()
    with open("content/boletin.html", "w", encoding="utf-8") as f:
        f.write(html)

def log_error(msg):
    ensure_content_dir()
    with open("content/error.log", "a", encoding="utf-8") as ef:
        ef.write(f"{datetime.now().isoformat()} - {msg}\n")

def extract_text_from_response(data):
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None

def is_valid_html_fragment(h):
    if not h or len(h) < 200:
        return False
    if END_MARKER not in h:
        return False
    # simple tag balance heuristic
    if h.count("<div") < h.count("</div>") or h.count("<section") < h.count("</section>"):
        return False
    return True

# --- Función para intentar llamar la API con una cabecera dada ---
def call_api_with_headers(headers):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    try:
        r = session.post(url, headers=headers, json=payload, timeout=(10, 90))
        # Guardar cuerpo de respuesta aunque sea error para depuración
        try:
            body = r.json()
        except Exception:
            body = r.text
        print(f"[generar_boletin] HTTP {r.status_code} recibido.")
        return r.status_code, body
    except requests.exceptions.RequestException as e:
        print(f"[generar_boletin] RequestException: {e}")
        return None, str(e)

# --- Intentos: primero con x-goog-api-key, si falla con 400 probamos Authorization Bearer ---
MAX_ATTEMPTS = 3
attempt = 0
last_body = None

while attempt < MAX_ATTEMPTS:
    attempt += 1
    print(f"[generar_boletin] Intento {attempt} de {MAX_ATTEMPTS}...")
    status, body = call_api_with_headers(headers_key)
    last_body = body
    if status is None:
        log_error(f"Request failed (attempt {attempt}): {body}")
        if attempt >= MAX_ATTEMPTS:
            raise SystemExit("Request failed repeatedly")
        continue

    # Si 400 con x-goog-api-key, intentar con Authorization Bearer (una sola vez)
    if status == 400 and attempt == 1:
        print("[generar_boletin] 400 con x-goog-api-key; intentando con Authorization Bearer...")
        status2, body2 = call_api_with_headers(headers_bearer)
        last_body = body2
        if status2 and 200 <= status2 < 300:
            data = body2 if isinstance(body2, dict) else {}
            save_raw(data)
            html = extract_text_from_response(data)
            if is_valid_html_fragment(html):
                save_boletin(html.replace(END_MARKER, ""))
                print("[generar_boletin] Boletín generado y guardado (bearer).")
                break
            else:
                log_error("Respuesta bearer inválida o truncada.")
                raise SystemExit("Generación inválida: revisar content/raw.json")
        else:
            # registrar el cuerpo de error y continuar con reintentos normales
            log_error(f"400 con x-goog-api-key y bearer intento devolvió status {status2}. Body: {json.dumps(body2) if isinstance(body2, dict) else str(body2)}")
            if attempt >= MAX_ATTEMPTS:
                print("[generar_boletin] No se pudo generar tras intentar ambas cabeceras.")
                save_raw(body2 if isinstance(body2, dict) else {"error": str(body2)})
                raise SystemExit("Generación inválida: revisar content/raw.json")
            continue

    # Si status 2xx procesar
    if 200 <= status < 300:
        data = body if isinstance(body, dict) else {}
        save_raw(data)
        html = extract_text_from_response(data)
        if is_valid_html_fragment(html):
            save_boletin(html.replace(END_MARKER, ""))
            print("[generar_boletin] Boletín generado y guardado en content/boletin.html")
            break
        else:
            log_error(f"Respuesta inválida o truncada en intento {attempt}. Guardado raw.json.")
            if attempt >= MAX_ATTEMPTS:
                save_raw(data)
                raise SystemExit("Generación inválida tras reintentos. Revisar content/raw.json")
            else:
                print("[generar_boletin] Reintentando por respuesta inválida...")
                continue
    else:
        # status no 2xx (por ejemplo 400)
        # Guardar cuerpo de error para inspección
        ensure_content_dir()
        err_path = "content/api_error_response.json"
        with open(err_path, "w", encoding="utf-8") as ef:
            if isinstance(body, dict):
                json.dump(body, ef, indent=2, ensure_ascii=False)
            else:
                ef.write(str(body))
        print(f"[generar_boletin] API devolvió status {status}. Cuerpo guardado en {err_path}")
        log_error(f"API status {status}. Ver content/api_error_response.json")
        # Si quedan intentos, reintentar; si no, salir con error
        if attempt >= MAX_ATTEMPTS:
            raise SystemExit(f"API returned status {status}. Revisar content/api_error_response.json")
        else:
            continue
