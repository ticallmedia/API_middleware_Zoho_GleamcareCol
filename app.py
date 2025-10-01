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
load_dotenv()
app = Flask(__name__)

#Log de eventos ajustado para utilizarlo en render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

#________________________________________________________________________________________
#Varibles de entorno
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_PORTAL_NAME = os.getenv("ZOHO_PORTAL_NAME") 
#ZOHO_PORTAL_NAME = os.getenv("ZOHO_PORTAL_NAME", "ticallmedia")
ZOHO_SALESIQ_BASE = os.getenv("ZOHO_SALESIQ_BASE")
#ZOHO_SALESIQ_BASE = os.getenv("ZOHO_SALESIQ_BASE", "https://salesiq.zoho.com/api/v2")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
#VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_de_verificacion")

APP_A_URL = os.getenv("APP_A_URL")
#APP_A_URL = os.getenv("APP_A_URL", "https://beta-ticallmedia-w.onrender.com")
ZOHO_ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN")
#________________________________________________________________________________________

#Obtiene un nuevo access_token usando el refresh_token
def get_access_token():
    url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    response = requests.post(url, params=params)
    data = response.json()
    logging.info(f"Access token:, {data}")

    """
    if "access_token" in data:
        return data["access_token"]
    else:
        return None
    """
    if "access_token" not in data:
        raise Exception(f"Error al refrescar token: {data}")
    return data["access_token"]
    
#________________________________________________________________________________________

#1. Mensaje entrante desde App A (usuario WhatsApp → SalesIQ)
def enviar_a_salesiq(visitor_id, nombre, telefono, mensaje=None, tag_id=None):
    access_token = get_access_token()
    if not access_token:
        return "❌ Error al obtener access_token"

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    # si no viene visitor_id, usamos telefono como fallback
    visitor_id = str(visitor_id or telefono)

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    payload = {
        "id": visitor_id,
        "name": nombre or visitor_id,
        "contactnumber": telefono,
        "custom_fields": {"canal": "whatsapp"}
    }

    # 👇 asignar tag si existe
    if tag_id:
        payload["tag_ids"] = [tag_id]

    response = requests.post(url, headers=headers, json=payload)

    # enviar mensaje inicial si existe
    if mensaje:
        msg_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors/{visitor_id}/message"
        msg_payload = {"content": mensaje, "type": "text"}
        requests.post(msg_url, headers=headers, json=msg_payload)

    try:
        data = response.json()
    except:
        data = {"error": "Respuesta no válida de Zoho", "raw": response.text}

    if response.status_code in [200, 201]:
        logging.info("✅ Lead enviado correctamente")
        return data   # ✅ ahora devolvemos la respuesta JSON completa
    else:
        logging.info(f"❌ Error al enviar Lead: {data}")
        return f"❌ Error al enviar Lead: {data}", 500



#________________________________________________________________________________________
#Rutas Flask

#webhook desde App A (mensajes entrantes de whatsapp)

@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    data = request.json
    user_msg = data.get("message")
    user_id = data.get("user_id")
    tag_name = data.get("tag")   # 👈 nombre del tag

    visitor_id = f"whatsapp_{user_id}"

    # 1️⃣ resolver el tag_id si se mandó un tag
    tag_id = None
    tag_result = None
    if tag_name:
        tag_id, tag_result = get_or_create_tag(tag_name)

    # 2️⃣ enviar visitante/mensaje a Zoho con tag opcional
    response = enviar_a_salesiq(
        visitor_id,
        nombre=f"WhatsApp {user_id}",
        telefono=user_id,
        mensaje=user_msg,
        tag_id=tag_id
    )

    return jsonify({
        "status": "sent_to_zoho",
        "zoho_response": response,
        "tag_response": tag_result
    })


def get_or_create_tag(tag_name, color="#FF5733", module="visitors"):
    """
    Verifica si un tag ya existe en Zoho.
    Si no existe, lo crea.
    Retorna (tag_id, respuesta_completa).
    """
    access_token = get_access_token()
    if not access_token:
        return None, {"error": "❌ No access_token"}

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    base_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/tags"

    # 1️⃣ Buscar si ya existe
    list_resp = requests.get(base_url, headers=headers)
    try:
        tags = list_resp.json().get("data", [])
    except:
        tags = []

    for t in tags:
        if t.get("name") == tag_name:
            return t.get("id"), {"status": "exists", "tag": t}

    # 2️⃣ Si no existe, crearlo
    payload = {"name": tag_name, "color": color, "module": module}
    create_resp = requests.post(base_url, headers=headers, json=payload)

    try:
        create_data = create_resp.json()
    except:
        create_data = {"error": "Respuesta no válida", "raw": create_resp.text}

    if create_resp.status_code in [200, 201]:
        new_tag = create_data.get("data", [])[0]
        return new_tag.get("id"), create_data

    return None, create_data






@app.route("/webhook", methods=["GET"])
def webhook_verify():
    token = request.args.get("verify_token")
    if token == VERIFY_TOKEN:
        # Zoho envía un "challenge" que debes devolver
        return request.args.get("challenge", "ok")
    return "Error: token inválido", 403



#webhook desde zoho (respuesta de agentes)
@app.route('/api/from-zoho', methods=['POST'])
def from_zoho():
    data = request.json
    event = data.get("event")

    """
    agent_msg = data.get("message")
    visitor_id = data.get("visitor_id")

    
    if visitor_id and visitor_id.startswith("whatsapp_"):
        user_id = visitor_id.replace("whatsapp_","")
        #logging.info(f"Enviar a whatsapp ({user_id}): {agent_msg}")
        
        # Reenviar a App A (endpoint /send)
        response = requests.post(f"{APP_A_URL}/send",json={"to": user_id, "msg": agent_msg})

        return jsonify({"status": "sent_to_app_a","app_a_response": response.json()})

    return jsonify({"status":"ignored"})
    """
    if event == "agent_message":
        agent_msg = data["message"]["text"]
        visitor_id = data["visitor"]["id"]

        if visitor_id.startswith("whatsapp_"):
            user_id = visitor_id.replace("whatsapp_", "")
            requests.post(f"{APP_A_URL}/send", json={"to": user_id, "msg": agent_msg})
            return jsonify({"status": "sent_to_whatsapp"})

    elif event == "visitor_message":
        # 👀 Esto puede usarse si quieres almacenar logs en B
        print(f"Mensaje de visitante: {data['message']['text']}")

    return jsonify({"status": "ok"})

#________________________________________________________________________________________
#endpoint, esto permite recibir el refresh_token, que se genera en zoho manualmente
@app.route('/oauth2callback')
def oauth_callback():
    code = request.args.get('code')
    if code:
        return f"Código recibido correctamente: {code}"
    return "No se recibió ningún código."

"""
#________________________________________________________________________________________
@app.route('/debug-token')
def debug_token():
    token = get_access_token()
    return f"Access token (si existe): {token}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)
"""
#________________________________________________________________________________________

#Endpoint opcional para verificación
@app.route("/verify", methods=["GET"])
def verify():
    token = request.args.get("token")
    if token == VERIFY_TOKEN:
        return jsonify({"status": "verified"}), 200
    return jsonify({"status": "forbidden"}), 403
#________________________________________________________________________________________

#if __name__ == "__main__":
#    app.run(debug=True, port=5000)

#if __name__ == "__main__":
#    port = int(os.environ.get("PORT", 5000))  # Render asigna un puerto dinámico
#    app.run(host="0.0.0.0", port=port)

# --- Ejecución del Programa ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)

#________________________________________________________________________________________
