from flask import Flask, render_template, request, jsonify, json
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


Versión: 1.2

- Se establece generación de token provicional para abrir conversaciones
- Mensaje de apertura de chat
- Identificacion de conversación, se crea funcion  -- busca_conversacion(phone)
- Continuacion de chat partiendo del id de la conversación , se modifica funcion from_waba()
- Se crea funcion que envia mensajes si ya existe una conversacion, --envio_mesaje_a_conversacion(conversation_id,user_msg)


"""
#________________________________________________________________________________________
# Integración WABA (App A)--- Zoho SalesIQ (App B, middleware)

load_dotenv()
app = Flask(__name__)
#________________________________________________________________________________________
#variables entorno y configuración

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN")
ZOHO_PORTAL_NAME = os.getenv("ZOHO_PORTAL_NAME")            # ej: "ticallmedia"
ZOHO_SALESIQ_BASE = os.getenv("ZOHO_SALESIQ_BASE", "https://salesiq.zoho.com/api/v2")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")                    # para /webhook GET verification
APP_A_URL = os.getenv("APP_A_URL")                          # URL de App A para reenviar respuestas
SALESIQ_APP_ID = os.getenv("SALESIQ_APP_ID")                # opcional (para crear conversación)
SALESIQ_DEPARTMENT_ID = os.getenv("SALESIQ_DEPARTMENT_ID")  # opcional

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

#Generación de Token provisional    
def get_access_token():
    #Refresca o usa el token de zoho
    if ZOHO_ACCESS_TOKEN:
        return ZOHO_ACCESS_TOKEN
    
    if not (ZOHO_ACCESS_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        logging.error(f"get_access_token: Credenciales Faltantes")
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
#Funciones Auxiliares
#________________________________________________________________________________________
#________________________________________________________________________________________
def create_or_update_visitor(visitor_id, nombre, telefono, custom_fields=None, tag_ids=None):
    #Crea o actualiza visitante, devuelve respuesta de zoho, importante envia el tags

    access_token = get_access_token()
    if not access_token:
        logging.error("create_or_update_visitor: no se obtuvo un access_token valido...")
        return {"error":"no_access_token"},401
    
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    payload = {
        "id": str(visitor_id),
        "name": nombre,
        "contactnumber": telefono,
        "custom_fields": custom_fields or {"canal": "whatsapp"},
        "tag_ids": "" #[] #se incluye porque es obligatorio asi este vacio
        
    }

    #incluir tags si existen
    if tag_ids:
        payload["tag_ids"] = tag_ids
    logging.info(f"create_or_update_visitor: POST {url} payload={payload}")

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f" : status {r.status_code} resp={r.text}")

        try:
            return r.json(), r.status_code
        except Exception as e:
            logging.info(f"create_or_update_visitor: invalid response: {e}")
            return {"error": "invalid_response", "details": str(e)}, r.status_code

    except Exception as e:
        logging.error(f"create_or_update_visitor: exception -> {e}")
        return {"error": str(e)}, 500



def create_conversation_if_configured(visitor_user_id, nombre, telefono,question):
    #Crea conversaciones en SalesIQ
    
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
        logging.error(f"create_conversation_if_configured: excepcion -> {e}")
        return {"error": str(e)}


def busca_conversacion(phone):
    try:
        access_token = get_access_token()

        if not access_token:
            logging.error(f"busca_conversacion: No se puedo encontra el access_token")
            return None
        
        headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

        url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"
        params = {
            "phone": phone,
            "status": "open"
        }

        #GET para obtener el codigo
        response = requests.get(url, headers=headers, params=params)

        #revision si hay un error de HTTP
        response.raise_for_status()
        #Conversion de la respuesta en json
        response_data = response.json()

        if 'data' in response_data and response_data.get('data'):
            primera_conversacion = response_data['data'][0]
            conversation_id = primera_conversacion.get('id')

            if conversation_id:
                logging.info(f"busca_conversacion: número de conversacion {conversation_id}")
                return conversation_id
            else:
                logging.error("busca_conversacion: No se encontraron conversaciones Abiertas... -> ")        
                return None
        else:
            logging.info(f"busca_conversacion: No se encontraron conversaciones abiertas para el telefono {phone}")
            return None
    except Exception as e:
        logging.error("busca_conversacion: Ocurrió un error inesperado... -> {e}")    
        return None
    

def envio_mesaje_a_conversacion(conversation_id,user_msg):
    """Envía el mensaj a una conversacion de zoho sales IQ existente"""

    access_token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conversation_id}/messages"
    
    payload = {
        "text": user_msg
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        #revision si hay un error de HTTP
        response.raise_for_status()
        logging.info(f"envio_mesaje_a_conversacion: Mensaje enviado exitosamente a la conversación: {conversation_id}")
        return response.json()
    except Exception as e:
        logging.error(f"envio_mesaje_a_conversacion: Error inesperado al enviar mensaje: -->{e}")
        return {"error": str(e)}


#________________________________________________________________________________________
#________________________________________________________________________________________
#Funciones Principales
#________________________________________________________________________________________
#________________________________________________________________________________________
@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    data = request.json or {}
    logging.info(f"/api/from-waba - mensaje recibido: {data}")

    user_id = data.get("user_id")
    user_msg = data.get("message")
    tag_name = data.get("tag")
    tag_color = data.get("tag_color") or "#FF5733"

    if not user_id:
            return jsonify({"error":"missing user_id" }), 400
    
    #1. Busca si existe una conversaicon abierta
    conversation_id = busca_conversacion(user_id)

    if conversation_id:

        envio_mensaje = envio_mesaje_a_conversacion(conversation_id,user_msg)

        # Si se encontró, devuelve el ID
        return jsonify({
            "status": "Mensaje enviado",
            "message": "Mensaje añadido a la conversaión existente...",
            "conversation_id": conversation_id,
            "send_response": envio_mensaje
        }), 200
    else:
        logging.info(f"No se encontro conversación para el {user_id}. Creando nuevo visitante y conversación...")

        #datos del visitante
        visitor_resp = None
        conv_resp = None
        final_status_code = 201 #201 creado

        #Crear o actualizar al visitante en zoho
        visitor_id_local = f"whatsapp_{user_id}"
        nombre = f"whatsapp {user_id}"
        telefono = user_id

        #Crear o actualizar visitante (importante captura el tag)
        visitor_resp, status = create_or_update_visitor(visitor_id_local, nombre, telefono, "whatsapp", tag_name)
        
        # Extraer visitor_id real de Zoho (si lo genera)
        zoho_visitor_id = None
        if status == 200 and  isinstance(visitor_resp.get("data"), dict):
            zoho_visitor_id = visitor_resp["data"].get("id")
        
        if not zoho_visitor_id:
            logging.error(f"No se puedo crear o encontrar el visitante en zoho. Avbortando...")

            return jsonify({
                "status": "error",
                "message": "Faloo al crear el visitante en zoho",
                "details": visitor_resp
                }),500

        #2.Crear conversacion con el primer mensaje

        if user_msg:
            conv_resp = create_conversation_if_configured(zoho_visitor_id, nombre, telefono, user_msg)
        
        return jsonify({
            "status": "ok",
            "visitor_resp": visitor_resp,
            "visitor_status_code": status,
            "conversation_resp": conv_resp,
            "visitor_id": zoho_visitor_id
        }), final_status_code
   
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
#________________________________________________________________________________________

if __name__=="__main__":
    #port = int(os.environ.get("PORT",5000))
    app.run(host='0.0.0.0', port=5000, debug=False)
#________________________________________________________________________________________