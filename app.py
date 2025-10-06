from flask import Flask, render_template, request, jsonify
from datetime import datetime
from dotenv import load_dotenv
import requests
import os
import logging

#________________________________________________________________________________________
"""
App middleware Zoho

Varsión: 1

Descripción: 

Es una App de puente entre, la App de WABA y Zoho SalesIQ, orientado la comunición hacia el 
agente humano y que permite utilizar las caracteristicas de Sales IQ como Chat Center.

Caracteristicas: 
- Cargar variables de entorno desde .env
- no cuenta con bd


"""
#________________________________________________________________________________________
# app.py — Integración WABA ↔ Zoho SalesIQ (App B, middleware)

load_dotenv()
app = Flask(__name__)

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------
# Environment / Config
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

# -----------------------
# Helper: get_access_token()
# - If ZOHO_ACCESS_TOKEN is set, use it.
# - Else if ZOHO_REFRESH_TOKEN + client creds exist, request a new access token.
# -----------------------
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

# -----------------------
# Tags: buscar o crear
# -----------------------
def get_or_create_tag(tag_name, color="#FF5733", module="visitors"):
    """
    Busca tag por nombre; si existe devuelve (tag_id, info).
    Si no existe, lo crea y devuelve (tag_id, info_create).
    """
    access_token = get_access_token()
    if not access_token:
        return None, {"error": "no_access_token"}

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    tags_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/tags"

    # 1) listar tags
    try:
        r = requests.get(tags_url, headers=headers, timeout=10)
        logging.info(f"get_or_create_tag: GET {tags_url} -> {r.status_code}")
        list_data = r.json()
    except Exception as e:
        logging.error(f"get_or_create_tag: error listing tags -> {e}")
        list_data = {}

    for t in list_data.get("data", []):
        # soporte para diferentes esquemas ('id' o 'tag_id')
        if t.get("name") == tag_name:
            tag_id = t.get("id") or t.get("tag_id")
            logging.info(f"get_or_create_tag: found tag {tag_name} -> id {tag_id}")
            return tag_id, {"status": "exists", "tag": t}

    # 2) crear tag si no existe
    payload = {"name": tag_name, "color": color, "module": module}
    logging.info(f"get_or_create_tag: creating tag -> {payload}")
    try:
        cr = requests.post(tags_url, headers=headers, json=payload, timeout=10)
        logging.info(f"get_or_create_tag: create response {cr.status_code} {cr.text}")
        create_data = cr.json()
    except Exception as e:
        logging.error(f"get_or_create_tag: create exception -> {e}")
        return None, {"error": "create_exception", "raw": str(e)}

    # parse create_data safely
    # Zoho puede devolver {"data": {...}} ó {"data": [{...}]}
    if cr.status_code in (200, 201) and "data" in create_data:
        data_obj = create_data["data"]
        if isinstance(data_obj, list) and len(data_obj) > 0:
            new = data_obj[0]
        elif isinstance(data_obj, dict):
            new = data_obj
        else:
            new = None
        if new:
            tag_id = new.get("id") or new.get("tag_id")
            logging.info(f"get_or_create_tag: created tag id -> {tag_id}")
            return tag_id, {"status": "created", "data": new}
    logging.error(f"get_or_create_tag: failed to create tag -> {create_data}")
    return None, create_data

# -----------------------
# Asociar tags a un visitor (PUT {portal}/visitors/{visitor_id}/tags)
# -----------------------
def associate_tags_to_module(module_name, module_record_id, tag_ids):
    """
    PUT /api/v2/{portal}/{module}/{module_record_id}/tags
    body: {"ids": ["tagid1", ...]}
    """
    access_token = get_access_token()
    if not access_token:
        logging.error("associate_tags_to_module: no access token")
        return {"error": "no_access_token"}

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/{module_name}/{module_record_id}/tags"
    payload = {"ids": tag_ids}
    logging.info(f"associate_tags_to_module: PUT {url} payload={payload}")
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        logging.info(f"associate_tags_to_module: status {r.status_code} resp={r.text}")
        try:
            return r.json()
        except:
            return {"status_code": r.status_code, "raw": r.text}
    except Exception as e:
        logging.error(f"associate_tags_to_module: exception -> {e}")
        return {"error": str(e)}

# -----------------------
# Crear/Actualizar visitor
# -----------------------
def create_or_update_visitor(visitor_id, nombre, telefono, custom_fields=None):
    access_token = get_access_token()
    if not access_token:
        logging.error("create_or_update_visitor: no access token")
        return {"error": "no_access_token"}, 401

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    payload = {
        "id": str(visitor_id),
        "name": nombre,
        "contactnumber": telefono,
        "custom_fields": custom_fields or {"canal": "whatsapp"}
    }
    logging.info(f"create_or_update_visitor: POST {url} payload={payload}")
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_or_update_visitor: status {r.status_code} resp={r.text}")
        try:
            return r.json(), r.status_code
        except:
            return {"error": "invalid_response", "raw": r.text}, r.status_code
    except Exception as e:
        logging.error(f"create_or_update_visitor: exception -> {e}")
        return {"error": str(e)}, 500

# -----------------------
# Crear conversación (opcional) — usa visitor/v2 endpoint y requiere APP_ID + DEPARTMENT_ID
# -----------------------
def create_conversation_if_configured(visitor_user_id, nombre, telefono, question):
    if not (SALESIQ_APP_ID and SALESIQ_DEPARTMENT_ID):
        logging.info("create_conversation_if_configured: APP_ID or DEPARTMENT_ID not configured — skipping conversation creation")
        return None

    url = f"https://salesiq.zoho.com/visitor/v2/{ZOHO_PORTAL_NAME}/conversations"
    payload = {
        "visitor": {
            "user_id": visitor_user_id,
            "name": nombre,
            "phone": telefono
        },
        "app_id": SALESIQ_APP_ID,
        "department_id": SALESIQ_DEPARTMENT_ID,
        "question": question
    }
    access_token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}
    logging.info(f"create_conversation_if_configured: POST {url} payload={payload}")
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"create_conversation_if_configured: status {r.status_code} resp={r.text}")
        try:
            return r.json()
        except:
            return {"status_code": r.status_code, "raw": r.text}
    except Exception as e:
        logging.error(f"create_conversation_if_configured: exception -> {e}")
        return {"error": str(e)}

# -----------------------
# Endpoint: App A → App B (WABA -> middleware)
# -----------------------
@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    data = request.json or {}
    logging.info(f"/api/from-waba payload: {data}")

    user_msg = data.get("message")
    user_id = data.get("user_id")
    tag_name = data.get("tag")   # opcional
    tag_color = data.get("tag_color")  # opcional

    if not user_id:
        logging.error("/api/from-waba: missing user_id")
        return jsonify({"error": "missing user_id"}), 400

    visitor_id = f"whatsapp_{user_id}"

    # 1) crear/actualizar visitor
    visitor_resp, status = create_or_update_visitor(visitor_id, nombre=f"WhatsApp {user_id}", telefono=user_id)
    logging.info(f"/api/from-waba visitor_resp: status={status} body={visitor_resp}")

    associate_result = None
    tag_result = None
    tag_id = None
    # 2) si hay tag_name -> resolver id y asociar
    if tag_name:
        tag_id, tag_result = get_or_create_tag(tag_name, color=tag_color or "#FF5733", module="visitors")
        logging.info(f"/api/from-waba tag resolved: {tag_id} result={tag_result}")
        if tag_id:
            associate_result = associate_tags_to_module("visitors", visitor_id, [tag_id])
            logging.info(f"/api/from-waba associate_result: {associate_result}")

    # 3) crear conversation si está configurado (envía mensaje como question)
    conv_resp = None
    if user_msg:
        conv_resp = create_conversation_if_configured(visitor_id, nombre=f"WhatsApp {user_id}", telefono=user_id, question=user_msg)

    return jsonify({
        "status": "ok",
        "visitor_resp": visitor_resp,
        "visitor_status_code": status,
        "tag_result": tag_result,
        "associate_result": associate_result,
        "conversation_resp": conv_resp
    })



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

        # opcional: asignar a app o departamento
        if SALESIQ_APP_ID:
            msg_payload["app_id"] = SALESIQ_APP_ID
        if SALESIQ_DEPARTMENT_ID:
            msg_payload["department_id"] = SALESIQ_DEPARTMENT_ID

        logging.info(f"➡️ Enviando mensaje inicial: {msg_payload}")
        msg_resp = requests.post(msg_url, headers=headers, json=msg_payload)
        logging.info(f"⬅️ Respuesta Zoho mensaje: {msg_resp.status_code} {msg_resp.text}")

        # Si quieres etiquetar la conversación
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
# OAuth callback endpoint: intercambia code -> refresh_token (único uso manual)
# -----------------------
@app.route('/oauth2callback', methods=['GET'])
def oauth_callback():
    code = request.args.get('code')
    if not code:
        return "No se recibió 'code' en la URL.", 400

    if not (ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        return "Faltan ZOHO_CLIENT_ID o ZOHO_CLIENT_SECRET en variables de entorno.", 500

    # Intercambia el authorization code por tokens
    token_url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "code": code,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "redirect_uri": request.base_url,  # debe ser exactamente lo registrado
        "grant_type": "authorization_code"
    }
    try:
        r = requests.post(token_url, params=params, timeout=10)
        data = r.json()
        logging.info(f"oauth2callback: token exchange -> {data}")
        # mostrar refresh_token para que lo copies a Render ENV (seguridad: solo use una vez)
        refresh_token = data.get("refresh_token")
        access_token = data.get("access_token")
        return jsonify({"token_response": data, "note": "Copia refresh_token a Render env var ZOHO_REFRESH_TOKEN. No lo publiques."})
    except Exception as e:
        logging.error(f"oauth2callback: exception -> {e}")
        return jsonify({"error": str(e)}), 500

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
