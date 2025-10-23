from flask import Flask, render_template, request, jsonify,json
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from dotenv import load_dotenv
import requests
import os
import logging

#________________________________________________________________________________________
"""
App middleware Zoho

Versión: 1.0

Descripción: 

Es una App de puente entre, la App de WABA y Zoho SalesIQ, orientado la comunición hacia el 
agente humano y que permite utilizar las caracteristicas de Sales IQ como Chat Center.

Caracteristicas: 
- Cargar variables de entorno desde .env
- no cuenta con bd
- Captura mensaja a mensaje de la App A hacia App b y finalmente a Zoho SalesIQ

Versión: 1.1

- Se agrega creacion de tabla de visitantes zoho, para capturar el visitor_id y evitar crea
un chat por cada mensaje del usuario


"""
#________________________________________________________________________________________
# app.py — Integración WABA ↔ Zoho SalesIQ (App B, middleware)

load_dotenv()
app = Flask(__name__)

#creacion de la tabla en la base de datos de database_bot_l6t7
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

#creacion de tabla
class visitor(db.Model):
    id = db.Column(db.Integer, primary_key =True)
    telefono_usuario_id = db.Column(db.Text)
    visitor_id = db.Column(db.Text) #identificador de zoho del visitante
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)

class visitantes_zoho(db.Model):
    id = db.Column(db.Integer, primary_key =True)
    visitor_id = db.Column(db.Text) #identificador de zoho del visitante
    telefono_usuario_id = db.Column(db.Text)
    session_id = db.Column(db.Text) #sesion id, sera para almacenr los chat del visitante
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_ultimo_mensaje = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.Text) #activo, cerrado, pendiente

#crear tabla si no exite
with app.app_context():
    db.create_all()

#________________________________________________________________________________________
#Funciones bd

def get_visitor_id(telefono_usuario_id):
    registro = visitantes_zoho.query.filter_by(telefono_usuario_id=telefono_usuario_id).first()
    return registro.visitor_id if registro else None


# Guardar un nuevo visitor_id o actualizar si ya existe
def save_visitor_id(telefono_usuario_id, visitor_id):
    registro = visitantes_zoho.query.filter_by(telefono_usuario_id=telefono_usuario_id).first()

    if registro:
        # Si ya existe, actualizamos solo el visitor_id
        registro.visitor_id = visitor_id
        registro.fecha_ultimo_mensaje = datetime.utcnow()
        registro.status = "activo"
    else:
        # Si no existe, creamos un nuevo registro
        registro = visitantes_zoho(
            visitor_id=visitor_id,
            telefono_usuario_id=telefono_usuario_id,
            session_id=f"session_{telefono_usuario_id}_{int(datetime.utcnow().timestamp())}",
            fecha_creacion=datetime.utcnow(),
            fecha_ultimo_mensaje=datetime.utcnow(),
            status="activo"
        )
        db.session.add(registro)

    db.session.commit()


#________________________________________________________________________________________

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------
# Variables de entorno y configuración 
# -----------------------
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
# Puedes usar ZOHO_ACCESS_TOKEN temporal si prefieres, pero la app intentará refrescar si hay refresh token.
ZOHO_ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN")

ZOHO_PORTAL_NAME = os.getenv("ZOHO_PORTAL_NAME")            # ej: "ticallmedia"
ZOHO_SALESIQ_BASE = os.getenv("ZOHO_SALESIQ_BASE", "https://salesiq.zoho.com/api/v2")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")                    # para /webhook GET verification
APP_A_URL = os.getenv("APP_A_URL")                          # URL de App A para reenviar respuestas
SALESIQ_APP_ID = os.getenv("SALESIQ_APP_ID")                # opcional (para crear conversación)
SALESIQ_DEPARTMENT_ID = os.getenv("SALESIQ_DEPARTMENT_ID")  # opcional

#________________________________________________________________________________________
"""
OAuth callback endpoint: intercambia code -> refresh_token (AUTORIZACION MANUAL y solo se usa una vez)
Se encarga de establecer las credenciales iniciales para poder interactuar con zoho
esto se hace cuando se copia la URL que incluye el ZOHO_CLIENT_ID mas direcion uri, como 
resultado se obtiene el ZOHO_ACCESS_TOKEN, luego se corre el codigo en POSTMAN para obtener el 
ZOHO_REFRESH_TOKEN. Si ya se cuenta con un ZOHO_REFRESH_TOKEN, no se vuelve a llamar la función

"""
#________________________________________________________________________________________
@app.route('/oauth2callback', methods=['GET'])
def oauth_callback():
    code = request.args.get('code')
    if not code:
        return "No se recibió 'code' en la URL.", 400

    if not (ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        return "Faltan ZOHO_CLIENT_ID o ZOHO_CLIENT_SECRET en variables de entorno.", 500

    REDIRECT_URI = "https://api-middleware-zoho.onrender.com/oauth2callback"

    # Intercambia el authorization code por tokens
    token_url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "code": code,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, #request.base_url,  # debe ser exactamente lo registrado
        "grant_type": "authorization_code"
    }
    try:
        r = requests.post(token_url, params=params, timeout=10)
        data = r.json()
        logging.info(f"oauth2callback: token exchange -> {data}")

        # mostrar refresh_token para que lo copiar a Render ENV (seguridad: solo use una vez)
        refresh_token = data.get("refresh_token")
        access_token = data.get("access_token")
        return jsonify({"token_response": data, "note": "Copia refresh_token a Render env var ZOHO_REFRESH_TOKEN"})
    except Exception as e:
        logging.error(f"oauth2callback: exception -> {e}")
        return jsonify({"error": str(e)}), 500
#________________________________________________________________________________________
"""
La función "get_access_token" Se ejecuta automáticamente cada vez que la aplicación necesita 
comunicarse con Zoho, es decir que obtine un access_token válido para hacer peticiones a Zoho,
sin tener que volver a pasar por el navegador o el code.
"""
#________________________________________________________________________________________

"""
def get_access_token():
    # Prioritize explicitly set access token (useful for quick testing)
    if ZOHO_ACCESS_TOKEN:
        logging.info("get_access_token: using ZOHO_ACCESS_TOKEN from env")
        return ZOHO_ACCESS_TOKEN

    # Otherwise, try to refresh using refresh token (recommended)
    if not (ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        logging.error("get_access_token: no ZOHO_ACCESS_TOKEN and missing refresh/client credentials")
        return None

    url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    try:
        r = requests.post(url, params=params, timeout=10)
        data = r.json()
        logging.info(f"get_access_token: token response -> {data}")
        if "access_token" in data:
            return data["access_token"]
        else:
            logging.error(f"get_access_token: no access_token in response -> {data}")
            return None
    except Exception as e:
        logging.error(f"get_access_token: exception -> {e}")
        return None
"""
def get_access_token():
    """Refresca o usa token Zoho."""
    if ZOHO_ACCESS_TOKEN:
        return ZOHO_ACCESS_TOKEN

    if not (ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        logging.error("get_access_token: missing credentials")
        return None

    url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    try:
        r = requests.post(url, params=params, timeout=10)
        data = r.json()
        return data.get("access_token")
    except Exception as e:
        logging.error(f"get_access_token: exception -> {e}")
        return None

#________________________________________________________________________________________
#________________________________________________________________________________________
#Funcionos Auxiliares
#________________________________________________________________________________________
#________________________________________________________________________________________

# -----------------------
# Crear/Actualizar visitor, es decir el id del usuario
# -----------------------


def create_or_update_visitor(visitor_id, name, phone, tag_ids=None):
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}, 401

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    payload = {
        "id": visitor_id,
        "name": name,
        "contactnumber": phone,
        "custom_fields": {"canal": "whatsapp"}
    }

    if tag_ids:
        payload["tag_ids"] = tag_ids

    logging.info(f"create_or_update_visitor: POST {url} payload={payload}")

    try:
        r = requests.post(url, headers=headers, json=payload)
        logging.info(f"create_or_update_visitor: status {r.status_code} resp={r.text}")
        return r.json(), r.status_code
    except Exception as e:
        logging.exception("❌ Error en create_or_update_visitor")
        return {"error": str(e)}, 500


#________________________________________________________________________________________
# -----------------------
# Tags: buscar o crear el tag, es decir el motivo de la conversación "soporte-urgente"
# -----------------------
def get_or_create_tag(tag_name, color="#FF5733", module="visitors"):
    """Busca tag por nombre, si no existe la crea y devuelve su ID."""
    access_token = get_access_token()
    if not access_token:
        return None, {"error": "no_access_token"}

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    tags_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/tags"

    try:
        r = requests.get(tags_url, headers=headers, timeout=10)
        tags = r.json().get("data", [])
        for t in tags:
            if t.get("name") == tag_name:
                return t.get("id") or t.get("tag_id"), {"status": "exists", "tag": t}
    except Exception as e:
        logging.error(f"get_or_create_tag: error listando tags -> {e}")

    # Crear si no existe
    payload = {"name": tag_name, "color": color, "module": module}
    try:
        cr = requests.post(tags_url, headers=headers, json=payload, timeout=10)
        data = cr.json().get("data")
        if data:
            tag_obj = data[0] if isinstance(data, list) else data
            return tag_obj.get("id") or tag_obj.get("tag_id"), {"status": "created", "data": tag_obj}
    except Exception as e:
        logging.error(f"get_or_create_tag: error creando tag -> {e}")

    return None, {"error": "tag_create_failed"}
#________________________________________________________________________________________

# -----------------------
# Asociar tags a un visitor (PUT {portal}/visitors/{visitor_id}/tags)
# -----------------------
def associate_tags_to_module(module_name, module_record_id, tag_ids):
    """Asocia tags a un registro de módulo (visitor, etc.)."""
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/{module_name}/{module_record_id}/tags"
    payload = {"ids": tag_ids}

    try:
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        logging.info(f"associate_tags_to_module: {r.status_code} {r.text}")
        return r.json()
    except Exception as e:
        logging.error(f"associate_tags_to_module: exception -> {e}")
        return {"error": str(e)}
#________________________________________________________________________________________

# -----------------------
# Crear conversación (opcional) — usa visitor/v2 endpoint y requiere APP_ID + DEPARTMENT_ID
#corresponde al Departamente que se configura en ZOHO para recibir los mensajes
# -----------------------
def _zoho_headers():
    token = get_access_token()
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json"
    }

# -----------------------
# 0) Obtener o crear visitor en Zoho (usando visitor.id = "whatsapp_{phone}")
# -----------------------
def get_or_create_visitor_from_phone(phone):
    """
    Devuelve visitor_id real usado por Zoho (campo 'id' en la respuesta).
    - Primero intenta GET /api/v2/{portal}/visitors/{visitor_identifier}
    - Si 404/otro, hace POST /api/v2/{portal}/visitors para crear
    Retorna visitor_id (string) o None si falla.
    """
    visitor_identifier = f"whatsapp_{phone}"
    headers = _zoho_headers()

    # 1) Intentar obtener
    get_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors/{visitor_identifier}"
    try:
        r = requests.get(get_url, headers=headers, timeout=10)
        logging.info(f"get_or_create_visitor_from_phone: GET {get_url} -> {r.status_code}")
        if r.status_code == 200:
            # La estructura puede ser {"object":"visitors","data":{...}} o similar
            try:
                j = r.json()
                data = j.get("data") or {}
                # si data es dict con id
                if isinstance(data, dict):
                    vid = data.get("id") or data.get("visitor_id") or visitor_identifier
                    logging.info(f"Visitor exists -> {vid}")
                    return vid
            except Exception:
                logging.warning("get_or_create_visitor_from_phone: parse GET response failed")
        # si 404 o no encontrado, caemos a creación
    except Exception as e:
        logging.warning(f"get_or_create_visitor_from_phone: GET exception -> {e}")

    # 2) Crear visitor (POST). Use visitor id consistente
    post_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    payload = {
        "id": visitor_identifier,
        "name": f"WhatsApp {phone}",
        "contactnumber": phone,
        "custom_fields": {"canal": "whatsapp"}
    }
    try:
        r = requests.post(post_url, headers=headers, json=payload, timeout=10)
        logging.info(f"get_or_create_visitor_from_phone: POST {post_url} -> {r.status_code} {r.text}")
        if r.status_code in (200, 201):
            try:
                j = r.json()
                data = j.get("data")
                # data puede ser dict o lista
                if isinstance(data, list) and data:
                    item = data[0]
                elif isinstance(data, dict):
                    item = data
                else:
                    item = None
                if item:
                    vid = item.get("id") or item.get("visitor_id") or visitor_identifier
                    logging.info(f"Visitor created -> {vid}")
                    return vid
                # fallback: return our identifier if server doesn't return id
                return visitor_identifier
            except Exception:
                logging.warning("get_or_create_visitor_from_phone: parse POST response failed")
                return visitor_identifier
        else:
            logging.error(f"get_or_create_visitor_from_phone: create failed: {r.status_code} {r.text}")
            return None
    except Exception as e:
        logging.exception("get_or_create_visitor_from_phone: exception")
        return None


# -----------------------
# 1) Buscar conversación activa (cliente-side) filtrando por visitor id/user_id
# -----------------------
def get_active_conversation_by_visitor(visitor_identifier, limit=50):
    """
    Lista conversaciones con GET /api/v2/{portal}/conversations (filtrando por app_id)
    y busca la primera conversación cuyo visitor.id == visitor_identifier OR
    visitor.user_id == visitor_identifier, y cuyo estado sea abierto (open/waiting/connected).
    Retorna la conversation dict o None.
    """
    headers = _zoho_headers()
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"
    params = {"app_id": SALESIQ_APP_ID, "limit": limit}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        logging.info(f"get_active_conversation_by_visitor: GET {url} params={params} -> {r.status_code}")
        if r.status_code != 200:
            logging.warning(f"get_active_conversation_by_visitor: non-200 -> {r.status_code} {r.text}")
            return None

        j = r.json()
        conversations = j.get("data", []) or []
        for conv in conversations:
            visitor = conv.get("visitor", {}) or {}
            # visitor may have 'id' or 'user_id' depending on response
            v_id = visitor.get("id") or visitor.get("visitor_id")
            v_user_id = visitor.get("user_id")
            if v_id == visitor_identifier or v_user_id == visitor_identifier:
                state = conv.get("chat_status", {}).get("state_key") or conv.get("status") or ""
                # Accept different possible keys: 'open','waiting','connected','active'
                if state and state.lower() in ("open", "waiting", "connected", "active"):
                    logging.info(f"Found active conversation id={conv.get('id') or conv.get('chat_id')}")
                    return conv
        return None
    except Exception as e:
        logging.exception("get_active_conversation_by_visitor exception")
        return None


# -----------------------
# 2) Crear conversación solo si no hay una activa (usa visitor.id real)
# -----------------------
def create_conversation_if_configured_by_phone(phone, message_text):
    """
    Flujo definitivo:
    - get_or_create_visitor_from_phone(phone) -> visitor_real_id
    - buscar conversación activa para visitor_real_id
    - si existe -> devolverla
    - si no -> POST /api/v2/{portal}/conversations con visitor.id = visitor_real_id
    Devuelve dict con conversation object (normalizado) o None / error dict.
    """
    if not (SALESIQ_APP_ID and SALESIQ_DEPARTMENT_ID):
        logging.info("create_conversation_if_configured_by_phone: APP/DEPT not configured")
        return None

    # 1) obtener o crear visitor en Zoho (nos aseguramos de tener visitor.id real)
    visitor_real_id = get_or_create_visitor_from_phone(phone)
    if not visitor_real_id:
        logging.error("create_conversation_if_configured_by_phone: cannot obtain visitor id")
        return None

    # 2) buscar conversación activa (por visitor real id)
    conv = get_active_conversation_by_visitor(visitor_real_id, limit=50)
    if conv:
        logging.info("create_conversation_if_configured_by_phone: reusing existing conversation")
        return {"status": "existing", "data": conv}

    # 3) crear nueva conversación con visitor.id
    headers = _zoho_headers()
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"
    payload = {
        "visitor": {"id": visitor_real_id},
        "app_id": SALESIQ_APP_ID,
        "department_id": SALESIQ_DEPARTMENT_ID,
        "question": message_text,
        "auto_assign": True
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_conversation_if_configured_by_phone: POST {url} -> {r.status_code} {r.text}")
        if r.status_code in (200, 201):
            # normalizar: respuesta puede tener data list/dict
            try:
                data = r.json().get("data")
                if isinstance(data, list) and data:
                    conv_obj = data[0]
                elif isinstance(data, dict):
                    conv_obj = data
                else:
                    conv_obj = None
                return {"status": "created", "data": conv_obj}
            except Exception:
                logging.warning("create_conversation_if_configured_by_phone: parse created response failed")
                return {"status": "created", "raw": r.text}
        else:
            logging.error("create_conversation_if_configured_by_phone: create failed")
            try:
                return {"error": r.json(), "status_code": r.status_code}
            except Exception:
                return {"error": r.text, "status_code": r.status_code}
    except Exception as e:
        logging.exception("create_conversation_if_configured_by_phone exception")
        return {"error": str(e)}
    
    
def create_conversation_if_configured(visitor_user_id, nombre, telefono, question):
    """
    Reusa conversación existente (por visitor_user_id) si la hay; si no, crea nueva.
    Usa ONLY api/v2 endpoints y no envía tag_ids vacíos.
    Devuelve un dict con info de la conversación (existing o created) o error.
    """
    if not (SALESIQ_APP_ID and SALESIQ_DEPARTMENT_ID):
        logging.info("create_conversation_if_configured: salesiq app/department not configured")
        return None

    # 1) Intentar leer conversación activa (cache local opcional)
    conv = get_active_conversation_by_visitor(visitor_user_id, limit=200)
    if conv:
        logging.info(f"✅ Reusing existing conversation for {visitor_user_id}: {conv.get('id') or conv.get('chat_id')}")
        return {"status":"existing", "data": conv}

    # 2) No existe: crear nueva conversación
    access_token = get_access_token()
    if not access_token:
        return {"error":"no_access_token"}

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type":"application/json"}
    payload = {
        "visitor": {"user_id": visitor_user_id, "name": nombre, "phone": telefono},
        "app_id": SALESIQ_APP_ID,
        "department_id": SALESIQ_DEPARTMENT_ID,
        "question": question,
        "auto_assign": True
    }

    logging.info(f"create_conversation_if_configured: POST {url} payload={payload}")
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_conversation_if_configured: {r.status_code} {r.text}")
        if r.status_code in (200,201):
            # Normalizar respuesta
            try:
                return {"status":"created", "data": r.json().get("data")}
            except Exception:
                return {"status":"created", "raw": r.text}
        else:
            try:
                return {"error": r.json(), "status_code": r.status_code}
            except Exception:
                return {"error": r.text, "status_code": r.status_code}
    except Exception as e:
        logging.exception("create_conversation_if_configured exception")
        return {"error": str(e)}

#________________________________________________________________________________________

def get_active_conversation(visitor_id):
    """
    Verifica si el visitante ya tiene una conversación activa en Zoho SalesIQ.
    Retorna el chat_id si existe, o None si no hay conversación activa.
    """
    access_token = get_access_token()
    if not access_token:
        logging.error("get_active_conversation: no access token available")
        return None

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    url = f"https://salesiq.zoho.com/api/v2/{ZOHO_PORTAL_NAME}/visitors/{visitor_id}/conversations"

    try:
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            logging.warning(f"get_active_conversation: {r.status_code} {r.text}")
            return None

        data = r.json()
        chats = data.get("data", [])
        for chat in chats:
            state = chat.get("chat_status", {}).get("state_key", "")
            if state in ["waiting", "open"]:  # conversación activa
                return chat.get("chat_id")

        return None

    except Exception as e:
        logging.error(f"get_active_conversation exception: {e}")
        return None


#________________________________________________________________________________________
#________________________________________________________________________________________
#Funciones Principales
#________________________________________________________________________________________
#________________________________________________________________________________________
# -----------------------
# Endpoint: App A → App B (WABA -> middleware)
#Se encarga de capturar en JSON la informacion que viene de la App A y lo desglosa para
#extraer los datos importentes que pasaran a ZOHO
# -----------------------
"""@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    data = request.json or {}
    logging.info(f"/api/from-waba — mensaje recibido: {data}")

    user_id = data.get("user_id")
    user_msg = data.get("message")
    tag_name = data.get("tag")
    tag_color = data.get("tag_color") or "#FF5733"

    if not user_id:
        return jsonify({"error": "missing user_id"}), 400

    visitor_id = f"whatsapp_{user_id}"
    nombre = f"WhatsApp {user_id}"
    telefono = user_id

    # 1️ Crear o actualizar visitante
    visitor_resp, status = create_or_update_visitor(visitor_id, nombre, telefono)
    logging.info(f"/api/from-waba — visitor_resp: {visitor_resp}")

    # Extraer visitor_id real de Zoho (si lo genera)
    zoho_visitor_id = None
    if isinstance(visitor_resp, dict):
        zoho_visitor_id = (
            visitor_resp.get("data", [{}])[0].get("id")
            if isinstance(visitor_resp.get("data"), list)
            else visitor_resp.get("data", {}).get("id")
        ) or visitor_id

    # 2️ Si hay tag -> crearla o buscarla y asociar
    tag_result = associate_result = None
    if tag_name:
        tag_id, tag_result = get_or_create_tag(tag_name, color=tag_color, module="visitors")
        if tag_id:
            associate_result = associate_tags_to_module("visitors", zoho_visitor_id, [tag_id])
            logging.info(f"/api/from-waba — tag asociado {tag_id} a {zoho_visitor_id}")

    # 3️ Crear conversación (si hay mensaje)
    conv_resp = None
    if user_msg:
        conv_resp = create_conversation_if_configured(zoho_visitor_id, nombre, telefono, user_msg)

    return jsonify({
        "status": "ok",
        "visitor_resp": visitor_resp,
        "visitor_status_code": status,
        "tag_result": tag_result,
        "associate_result": associate_result,
        "conversation_resp": conv_resp,
        "visitor_id": zoho_visitor_id
    })"""


@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    data = request.json or {}
    logging.info(f"/api/from-waba — mensaje recibido: {data}")

    user_id = data.get("user_id")
    user_msg = data.get("message")
    tag_name = data.get("tag")
    tag_color = data.get("tag_color") or "#FF5733"

    if not user_id:
        return jsonify({"error": "missing user_id"}), 400

    visitor_id = f"whatsapp_{user_id}"
    nombre = f"WhatsApp {user_id}"
    telefono = user_id

    # 1️⃣ Crear o actualizar visitante sin tag_ids vacíos
    visitor_resp, status = create_or_update_visitor(visitor_id, nombre, telefono)
    logging.info(f"/api/from-waba — visitor_resp: {visitor_resp}")

    zoho_visitor_id = None
    if isinstance(visitor_resp, dict):
        data_obj = visitor_resp.get("data")
        if isinstance(data_obj, list) and len(data_obj) > 0:
            zoho_visitor_id = data_obj[0].get("id")
        elif isinstance(data_obj, dict):
            zoho_visitor_id = data_obj.get("id")
    zoho_visitor_id = zoho_visitor_id or visitor_id

    # 2️⃣ Asociar tag si viene definido
    tag_result = associate_result = None
    try:
        if tag_name:
            tag_id, tag_result = get_or_create_tag(tag_name, color=tag_color, module="visitors")
            if tag_id:
                associate_result = associate_tags_to_module("visitors", zoho_visitor_id, [tag_id])
                logging.info(f"/api/from-waba — tag asociado {tag_id} a {zoho_visitor_id}")
    except Exception as e:
        logging.exception("❌ Error al manejar tags")

    # 3️⃣ Unificar conversación (buscar o crear)
    conv_resp = None
    try:
        conv_resp = create_conversation_if_configured(zoho_visitor_id, nombre, telefono, user_msg)
    except Exception as e:
        logging.exception("❌ Error creando conversación")

    return jsonify({
        "status": "ok",
        "visitor_resp": visitor_resp,
        "visitor_status_code": status,
        "tag_result": tag_result,
        "associate_result": associate_result,
        "conversation_resp": conv_resp,
        "visitor_id": zoho_visitor_id
    })

#________________________________________________________________________________________



#________________________________________________________________________________________
#________________________________________________________________________________________
#Funciones Para revisar
#________________________________________________________________________________________
#________________________________________________________________________________________


def enviar_a_salesiq(visitor_id, nombre, telefono, mensaje=None, tag_id=None):
    access_token = get_access_token()
    if not access_token:
        logging.error("❌ No se pudo obtener access_token en enviar_a_salesiq()")
        return "❌ Error al obtener access_token"

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    visitor_id = str(visitor_id or telefono)
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    # 1️⃣ Crear o actualizar visitante
    payload = {
        "id": visitor_id,
        "name": nombre or visitor_id,
        "contactnumber": telefono,
        "custom_fields": {"canal": "whatsapp"}
    }

    logging.info(f"➡️ Enviando visitante a Zoho: {payload}")
    visitor_resp = requests.post(url, headers=headers, json=payload)
    logging.info(f"/api/from-waba visitor_resp: status={visitor_resp.status_code} body={visitor_resp.text}")

    # 2️⃣ Abrir conversación enviando mensaje inicial
    if mensaje:
        msg_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors/{visitor_id}/message"
        msg_payload = {"content": mensaje, "type": "text"}

        if SALESIQ_APP_ID:
            msg_payload["app_id"] = SALESIQ_APP_ID
        if SALESIQ_DEPARTMENT_ID:
            msg_payload["department_id"] = SALESIQ_DEPARTMENT_ID

        logging.info(f"➡️ Enviando mensaje inicial: {msg_payload}")
        msg_resp = requests.post(msg_url, headers=headers, json=msg_payload)
        logging.info(f"⬅️ Respuesta Zoho mensaje: {msg_resp.status_code} {msg_resp.text}")

        # ⬇️ Aquí sí aplicamos el tag al ID de la conversación
        if tag_id and msg_resp.status_code in [200, 201]:
            try:
                conv_id = msg_resp.json()["data"][0]["id"]
                tag_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conv_id}/tags"
                tag_payload = {"ids": [tag_id]}
                logging.info(f"➡️ Etiquetando conversación {conv_id} con {tag_id}")
                tag_resp = requests.put(tag_url, headers=headers, json=tag_payload)
                logging.info(f"⬅️ Respuesta Zoho tags: {tag_resp.status_code} {tag_resp.text}")
            except Exception as e:
                logging.error(f"⚠️ No se pudo asignar tag a la conversación: {e}")


    return "✅ Visitante y conversación enviados a Zoho"

# -----------------------
# Endpoint: Zoho -> App B (webhook)
# -----------------------
@app.route('/api/from-zoho', methods=['POST'])
def from_zoho():
    data = request.json or {}
    logging.info(f"/api/from-zoho payload: {data}")

    # Intenta identificar un mensaje de agente o evento relevante
    event = data.get("event")
    # estructura que a veces viene: {"message": {"text": "..."}}
    # o según integración: data['message']['text'] y visitor id en data['visitor']['id']
    try:
        if event == "agent_message":
            agent_msg = data.get("message", {}).get("text") or data.get("message")
            visitor_id = data.get("visitor", {}).get("id")
            logging.info(f"from_zoho: agent_message for visitor {visitor_id}: {agent_msg}")
            if visitor_id and visitor_id.startswith("whatsapp_") and APP_A_URL:
                user_id = visitor_id.replace("whatsapp_", "")
                try:
                    r = requests.post(f"{APP_A_URL}/send", json={"to": user_id, "msg": agent_msg}, timeout=10)
                    logging.info(f"from_zoho: forwarded to App A -> status {r.status_code} resp={r.text}")
                    return jsonify({"status": "sent_to_app_a", "app_a_status": r.status_code}), 200
                except Exception as e:
                    logging.error(f"from_zoho: error forwarding to App A -> {e}")
                    return jsonify({"status": "error_forwarding", "error": str(e)}), 500

        # visitor_message event logging (optional)
        if event == "visitor_message":
            logging.info(f"from_zoho: visitor_message -> {data.get('message')}")

    except Exception as e:
        logging.error(f"from_zoho: unexpected error -> {e}")

    return jsonify({"status": "received"}), 200
#________________________________________________________________________________________
#________________________________________________________________________________________


#________________________________________________________________________________________
# -----------------------
# GET verification endpoint for Zoho webhook subscription
# -----------------------
@app.route("/webhook", methods=["GET"])
def webhook_verify():
    token = request.args.get("verify_token")
    if token == VERIFY_TOKEN:
        return request.args.get("challenge", "ok")
    return "Error: token inválido", 403

# -----------------------
# Debug token (opcional)
# -----------------------
@app.route('/debug-token', methods=['GET'])
def debug_token():
    t = get_access_token()
    return jsonify({"access_token_preview": (t[:20] + "..." if t else None)}), 200

# -----------------------
# Verify endpoint for app health
# -----------------------
@app.route("/verify", methods=["GET"])
def verify():
    token = request.args.get("token")
    if token == VERIFY_TOKEN:
        return jsonify({"status": "verified"}), 200
    return jsonify({"status": "forbidden"}), 403

# -----------------------
# Run (Render espera puerto dinamico via $PORT)
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
