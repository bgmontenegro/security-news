#!/usr/bin/env python3
# generar_boletin.py - Genera el boletín usando la API de Generative Language
# Reemplaza completamente el archivo anterior por este contenido.

import os
import json
import time
import random
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

# --- Leer whitelist (fallback si no existe) ---
try:
    whitelist_path = "config/whitelist.txt"
    if os.path.exists(whitelist_path):
        with open(whitelist_path, "r", encoding="utf-8") as wf:
            whitelist_lines = [ln.strip() for ln in wf if ln.strip() and not ln.strip().startswith("#")]
            whitelist = "\n".join(whitelist_lines) if whitelist_lines else "fuentes oficiales"
    else:
        whitelist = "fuentes oficiales"
except Exception:
    whitelist = "fuentes oficiales"

# --- Leer prompt_template y reemplazos ---
prompt_file = "prompt_template.txt"
if not os.path.exists(prompt_file):
    print(f"[generar_boletin] ERROR: No se encontró {prompt_file}", file=sys.stderr)
    raise SystemExit(f"{prompt_file} missing")

with open(prompt_file, "r", encoding="utf-8") as f:
    prompt = f.read()

prompt = (prompt
    .replace("{{FECHA_HOY}}", FECHA_HOY)
    .replace("{{FECHA_HACE_30_DIAS}}", FECHA_30)
    .replace("{{WHITELIST_DOMAINS}}", whitelist)
)

# Asegurar marcador final para detectar truncado
END_MARKER = "<!-- FIN_DE_BOLETIN -->"
if END_MARKER not in prompt:
    prompt = prompt.rstrip() + "\n\n" + END_MARKER + "\n"

# --- Preparar headers y payload ---
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
if not API_TOKEN:
    print("[generar_boletin] ERROR: API_TOKEN no definido en el entorno.", file=sys.stderr)
    raise SystemExit("API_TOKEN missing")

# Cabeceras: probamos x-goog-api-key primero, luego Authorization Bearer si hace falta
headers_key = {"Content-Type": "application/json", "x-goog-api-key": API_TOKEN}
headers_bearer = {"Content-Type": "application/json", "Authorization": f"Bearer {API_TOKEN}"}

# Payload con grounding (tools restaurado)
payload = {
    "contents": [
        {"parts": [{"text": prompt}]}
    ],
    "tools": [
        {"googleSearch": {}}
    ]
}

# Endpoint (ajusta si tu endpoint difiere)
url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# --- Sesión con reintentos de transporte (para errores transitorios de red) ---
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["GET","POST"]))
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- Helpers de archivo y validación ---
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

def save_api_error(body):
    ensure_content_dir()
    with open("content/api_error_response.json", "w", encoding="utf-8") as ef:
        if isinstance(body, dict):
            json.dump(body, ef, indent=2, ensure_ascii=False)
        else:
            ef.write(str(body))

def log_error(msg):
    ensure_content_dir()
    with open("content/error.log", "a", encoding="utf-8") as ef:
        ef.write(f"{datetime.now().isoformat()} - {msg}\n")

def extract_text_from_response(data):
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None

def ends_with_incomplete_word(s):
    s = s.strip()
    if not s:
        return True
    tail = s[-40:]
    if tail[-1].isalpha() and tail[-10:].count(" ") == 0:
        return True
    return False

def is_valid_html_fragment(h):
    if not h or len(h) < 200:
        return False
    if END_MARKER not in h:
        return False
    if ends_with_incomplete_word(h.replace(END_MARKER, "")):
        return False
    required_ids = ["id='resumen'", "id='incidentes'", "id='cves'", "id='ransomware'", "id='recomendaciones'"]
    if not any(req in h for req in required_ids):
        return False
    if h.count("<div") < h.count("</div>") or h.count("<section") < h.count("</section>"):
        return False
    return True

# Guardar preview del payload (sin token) para depuración
try:
    ensure_content_dir()
    with open("content/last_payload_preview.json", "w", encoding="utf-8") as pf:
        json.dump({"prompt_preview": prompt[:2000], "whitelist_preview": whitelist[:500]}, pf, indent=2, ensure_ascii=False)
except Exception:
    pass

# --- Backoff exponencial con jitter para 429 y reintentos controlados ---
MAX_ATTEMPTS = 6
base_sleep = 1.0
success = False
last_response_body = None

for attempt in range(1, MAX_ATTEMPTS + 1):
    try:
        print(f"[generar_boletin] Intento {attempt} de {MAX_ATTEMPTS}...")
        # Intentamos primero con x-goog-api-key
        r = session.post(url, headers=headers_key, json=payload, timeout=(10, 120))
        # Si 429, manejar con backoff y jitter
        if r.status_code == 429:
            body = r.text
            print(f"[generar_boletin] 429 recibido. Body (preview): {body[:500]}")
            last_response_body = body
            if attempt == MAX_ATTEMPTS:
                save_api_error(body)
                log_error("Máximo reintentos alcanzado por 429 con x-goog-api-key")
                raise SystemExit("Máximo reintentos alcanzado por 429")
            sleep_time = base_sleep * (2 ** (attempt - 1))
            sleep_time = sleep_time * (0.5 + random.random()/1.0)
            print(f"[generar_boletin] Durmiendo {sleep_time:.1f}s antes del reintento por 429...")
            time.sleep(sleep_time)
            continue

        # Si 400 con x-goog-api-key, intentar con Authorization Bearer una vez
        if r.status_code == 400 and attempt == 1:
            try:
                print("[generar_boletin] 400 con x-goog-api-key; intentando con Authorization Bearer...")
                r2 = session.post(url, headers=headers_bearer, json=payload, timeout=(10, 120))
                if r2.status_code == 429:
                    body2 = r2.text
                    print(f"[generar_boletin] 429 recibido con bearer. Body preview: {body2[:500]}")
                    last_response_body = body2
                    # aplicar backoff antes de reintentar el bucle
                    sleep_time = base_sleep * (2 ** (attempt - 1)) * (0.5 + random.random()/1.0)
                    time.sleep(sleep_time)
                    continue
                if not (200 <= r2.status_code < 300):
                    # guardar cuerpo de error y continuar con reintentos normales
                    save_api_error(r2.text)
                    log_error(f"Bearer intento devolvió status {r2.status_code}")
                    if attempt >= MAX_ATTEMPTS:
                        raise SystemExit("Generación inválida: revisar content/api_error_response.json")
                    time.sleep(base_sleep * (2 ** (attempt - 1)))
                    continue
                # r2 es 2xx
                data = r2.json()
                save_raw(data)
                html = extract_text_from_response(data)
                if is_valid_html_fragment(html):
                    save_boletin(html.replace(END_MARKER, ""))
                    print("[generar_boletin] Boletín generado y guardado (bearer).")
                    success = True
                    break
                else:
                    save_raw(data)
                    log_error("Respuesta bearer inválida o truncada.")
                    raise SystemExit("Generación inválida: revisar content/raw.json")
            except requests.exceptions.RequestException as e:
                log_error(f"RequestException con bearer: {e}")
                # dejar que el bucle principal maneje reintentos
                last_response_body = str(e)
                time.sleep(base_sleep * (2 ** (attempt - 1)))
                continue

        # Si no 2xx y no manejado arriba, guardar error y reintentar
        if not (200 <= r.status_code < 300):
            body = None
            try:
                body = r.json()
            except Exception:
                body = r.text
            save_api_error(body)
            log_error(f"API status {r.status_code}. Guardado en content/api_error_response.json")
            last_response_body = body
            if r.status_code == 429:
                # ya manejado arriba, pero por seguridad
                sleep_time = base_sleep * (2 ** (attempt - 1)) * (0.5 + random.random()/1.0)
                time.sleep(sleep_time)
                continue
            if attempt >= MAX_ATTEMPTS:
                raise SystemExit(f"API returned status {r.status_code}. Revisar content/api_error_response.json")
            # esperar antes de reintentar
            time.sleep(base_sleep * (2 ** (attempt - 1)))
            continue

        # Si 2xx procesar respuesta
        data = r.json()
        save_raw(data)
        html = extract_text_from_response(data)
        if is_valid_html_fragment(html):
            save_boletin(html.replace(END_MARKER, ""))
            print("[generar_boletin] Boletín generado y guardado en content/boletin.html")
            success = True
            break
        else:
            log_error(f"Respuesta inválida o truncada en intento {attempt}. Guardado raw.json.")
            last_response_body = data
            if attempt >= MAX_ATTEMPTS:
                save_raw(data)
                raise SystemExit("Generación inválida tras reintentos. Revisar content/raw.json")
            sleep_time = base_sleep * (2 ** (attempt - 1)) * (0.5 + random.random()/1.0)
            print(f"[generar_boletin] Respuesta inválida; durmiendo {sleep_time:.1f}s antes de reintentar...")
            time.sleep(sleep_time)
            continue

    except requests.exceptions.RequestException as e:
        print(f"[generar_boletin] RequestException: {e}")
        log_error(f"RequestException intento {attempt}: {e}")
        last_response_body = str(e)
        if attempt == MAX_ATTEMPTS:
            raise
        sleep_time = base_sleep * (2 ** (attempt - 1)) * (0.5 + random.random()/1.0)
        print(f"[generar_boletin] Reintentando en {sleep_time:.1f}s...")
        time.sleep(sleep_time)
        continue

# --- Resultado final ---
if not success:
    # Guardar último cuerpo de respuesta si existe
    try:
        save_api_error(last_response_body)
    except Exception:
        pass
    raise SystemExit("No se pudo generar boletín correctamente. Revisar content/api_error_response.json o content/error.log")

# Si llegamos aquí, el boletín fue generado y guardado en content/boletin.html
print("[generar_boletin] Proceso completado con éxito.")
