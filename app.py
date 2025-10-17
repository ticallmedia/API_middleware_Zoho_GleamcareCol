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
def create_or_update_visitor(visitor_id, nombre, telefono, custom_fields=None, tag_ids=None):
    """Crea o actualiza visitante y devuelve respuesta de Zoho."""
    access_token = get_access_token()
    if not access_token:
        logging.error("create_or_update_visitor: no access token available")
        return {"error": "no_access_token"}, 401

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    payload = {
        "id": str(visitor_id),
        "name": nombre,
        "contactnumber": telefono,
        "custom_fields": custom_fields or {"canal": "whatsapp"},
        "tag_ids": "" #[] #se incluye porque es obligatorio asi este vacio
    }

    # Incluir tags si existen
    if tag_ids:
        payload["tag_ids"] = tag_ids

    logging.info(f"create_or_update_visitor: POST {url} payload={payload}")

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_or_update_visitor: status {r.status_code} resp={r.text}")

        try:
            return r.json(), r.status_code
        except Exception as e:
            logging.error(f"create_or_update_visitor: invalid response: {e}")
            return {"error":"invalid_response","details": str(e)},r.status_code

    except Exception as e:
        logging.error(f"create_or_update_visitor: exception -> {e}")
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
def create_conversation_if_configured(visitor_user_id, nombre, telefono, question):
    """Crea conversación en SalesIQ solo si están configuradas APP_ID y DEPARTMENT_ID."""
    if not (SALESIQ_APP_ID and SALESIQ_DEPARTMENT_ID):
        return None

    url = f"https://salesiq.zoho.com/visitor/v2/{ZOHO_PORTAL_NAME}/conversations"
    payload = {
        "visitor": {"user_id": visitor_user_id, "name": nombre, "phone": telefono},
        "app_id": SALESIQ_APP_ID,
        "department_id": SALESIQ_DEPARTMENT_ID,
        "question": question
    }

    access_token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_conversation_if_configured: {r.status_code} {r.text}")
        return r.json()
    except Exception as e:
        logging.error(f"create_conversation_if_configured: exception -> {e}")
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
