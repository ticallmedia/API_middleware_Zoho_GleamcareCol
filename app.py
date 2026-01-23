from flask import Flask, render_template, request, jsonify
from json import JSONDecodeError
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
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
- Se agrega variables globales CACHED_ACCESS_TOKEN, TOKEN_EXPIRATION_TIME para consultar access_token y solo crear cuando sea necesario
- Se agrega JSONDecodeError, debido a que habia respuestas que llegaban a zoho, y devolvian a la 
api un valor vacio que la Api persivia como un error, se agrega para hacer una excepcion y que continue el flujo 

Versión: 1.3

- Se configura Flujo de Trabajo en Zoho Sales IQ, para configurar el webhook desde Zoho
- Se crea funcion from_zoho(): que realiza la captura del webhook y se envia a la App A

"""
#________________________________________________________________________________________
# Integración WABA (App A)--- Zoho SalesIQ (App B, middleware)

load_dotenv()
app = Flask(__name__)

# Configura el logger (Log de eventos para ajustado para utilizarlo en render)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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

#variables para gestionar el estado del token
CACHED_ACCESS_TOKEN = None
TOKEN_EXPIRATION_TIME = None 
#________________________________________________________________________________________
"""
Función para redirigir al usuario a la URL de autorización de Zoho, 
Necesaria para establecer comunicación
"""

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
    """
    Obtiene un nuevo access_token de Zoho utilizando el refresh_token.
    cada vez que se establece una comunicación, es necesario refrescarlo.
    """
    global CACHED_ACCESS_TOKEN, TOKEN_EXPIRATION_TIME

    if CACHED_ACCESS_TOKEN and TOKEN_EXPIRATION_TIME and datetime.now() < TOKEN_EXPIRATION_TIME - timedelta(seconds=30):
        logging.info(f"get_access_token: access_token, sigue siendo valido...")
        return CACHED_ACCESS_TOKEN
    
    logging.info(f"get_access_token: El access_token no es valido o a expirado. Solicitando uno nuevo a zoho...")

    if not (ZOHO_REFRESH_TOKEN and ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET):
        logging.error("get_access_token: Faltan credenciales críticas (REFRESH_TOKEN, CLIENT_ID, o CLIENT_SECRET).")
        return None
    
    url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    try:
        logging.info(f"get_access_token: Solicitando un nuevo access_token a Zoho...")
        response = requests.post(url, params=params, timeout=10)
        response.raise_for_status()  # Verificar si hubo errores HTTP
        
        data = response.json()
        new_access_token = data.get("access_token")

        if new_access_token:
            #calculando la expiracion del token
            expiracion_en_segundos = data.get("expires_in",3600)

            CACHED_ACCESS_TOKEN = new_access_token
            TOKEN_EXPIRATION_TIME = datetime.now() + timedelta(seconds=expiracion_en_segundos)
            
            logging.info(f"get_access_token: Nuevo access_token obtenido exitosamente.")
            return CACHED_ACCESS_TOKEN
        else:
            logging.error(f"get_access_token: La respuesta de Zoho no incluyó un access_token. Respuesta: {data}")
            return None
            
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"get_access_token: Error HTTP al refrescar token. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return None
    except Exception as e:
        logging.error(f"get_access_token: Ocurrió una excepción inesperada -> {e}")
        return None
    
#________________________________________________________________________________________
#________________________________________________________________________________________
#Funciones Auxiliares
#________________________________________________________________________________________
#________________________________________________________________________________________
def create_or_update_visitor(visitor_id, nombre_completo, nombre, apellido,telefono, custom_fields=None, tag_ids=None):
    """
    Crea o actualiza visitante, devuelve respuesta de zoho, importante envia el tags
    """

    access_token = get_access_token()
    if not access_token:
        logging.error("create_or_update_visitor: no se obtuvo un access_token valido...")
        return {"error":"no_access_token"},401
    
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", 
               "Content-Type": "application/json"}

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    payload = {
        "id": str(visitor_id),
        "name": nombre_completo,
        "first_name": nombre,
        "last_name": apellido,
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



def create_conversation_if_configured(visitor_user_id, nombre_completo, nombre, apellido, telefono,question):
    """
    Crea conversaciones en SalesIQ
    """
    
    url = f"https://salesiq.zoho.com/visitor/v2/{ZOHO_PORTAL_NAME}/conversations"
    payload = {
        "visitor": {"user_id": visitor_user_id, "name": nombre_completo, "first_name": nombre, "last_name": apellido, "phone": telefono},
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
    """
    Busca una conversación abierta en Zoho SalesIQ para un número de teléfono.
    """
    access_token = get_access_token()
    if not access_token:
        logging.error("busca_conversacion: No se pudo obtener un access_token válido. Abortando búsqueda.")
        return None

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    params = {
        "phone": phone,
        "status": "open"
    }
    
    try:
        logging.info(f"Buscando conversación abierta para el teléfono: {phone}")
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        response.raise_for_status()  # Verificar si hubo errores HTTP
        response_data = response.json()

        logging.info(f"busca_conversacion: Respuesta de la API: {response_data}")

        if 'data' in response_data and response_data.get('data'):
            primera_conversacion = response_data['data'][0]
            conversation_id = primera_conversacion.get('id')

            if conversation_id:
                logging.info(f"Se encontró una conversación abierta con ID: {conversation_id}")
                return conversation_id
        
        logging.info(f"No se encontraron conversaciones abiertas para el teléfono {phone}")
        return None
    
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"busca_conversacion: Error HTTP de la API de Zoho. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logging.error(f"busca_conversacion: Error de conexión (Timeout, DNS, etc): {req_err}")
        return None
    except Exception as e:
        logging.error(f"busca_conversacion: Ocurrió un error inesperado -> {e}")    
        return None
    
def envio_mesaje_a_conversacion(conversation_id,mensaje):
    """
    Envía el mensaj a una conversacion de zoho sales IQ existente
    """
    if "btn_si1" in mensaje:
        mensaje = "[👤 Usuario]: Si"
    elif "btn_no1" in mensaje:
        mensaje = "[👤 Usuario]: No"
    elif "btn_1" in mensaje:
        mensaje = "[👤 Usuario]: DDA & Mobile Campaigns 📱"
    elif "btn_2" in mensaje:
        mensaje = "[👤 Usuario]: Websites 🌐"
    elif "btn_3" in mensaje:
        mensaje = "[👤 Usuario]: Advertising Photography 📸"
    elif "btn_4" in mensaje:
        mensaje = "[👤 Usuario]: Content Marketing ✍️"
    elif "btn_5" in mensaje:
        mensaje = "[👤 Usuario]: Media Strategy 📈"
    elif "btn_6" in mensaje:
        mensaje = "[👤 Usuario]: Digital Marketing 💻"
    elif "btn_7" in mensaje:
        mensaje = "[👤 Usuario]: Paid Social Media 📊"
    elif "btn_8" in mensaje:
        mensaje = "[👤 Usuario]: E-commerce Strategy 🛒"
    elif "btn_9" in mensaje:
        mensaje = "[👤 Usuario]: Display Media 📺"
    elif "btn_0" in mensaje:
        mensaje = "[👤 Usuario]: Hablar con un agente 🗣️"
    else:
        mensaje

    access_token = get_access_token()

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conversation_id}/messages"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", 
               "Content-Type": "application/json"}

    payload = {
        "text": mensaje
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        #revision si hay un error de HTTP
        response.raise_for_status()  # Verificar si hubo errores HTTP
        logging.info(f"envio_mesaje_a_conversacion: Enviando mensaje a la conversación: {conversation_id}")
        
        try:
            response_data =  response.json()
            logging.info(f"envio_mesaje_a_co: respuesta de API: {response_data}")
            return True
        except JSONDecodeError:
            logging.info(f"envio_mesaje_a_conversacion: Mensajes enviado con exito, la API devolvio una respuesta vacia (200 OK) lo cual es normal...")
    
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"envio_mesaje_a_conversacion: Error HTTP de la API de Zoho. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return False
    except requests.exceptions.RequestException as req_err:
        logging.error(f"envio_mesaje_a_conversacion: Error de conexión: {req_err}")
        return False
    except Exception as e:
        logging.error(f"envio_mesaje_a_conversacion: Error inesperado al enviar mensaje: -->{e}")
        return {"error": str(e)}


#________________________________________________________________________________________
#________________________________________________________________________________________
#Funciones Principales 
#________________________________________________________________________________________
#________________________________________________________________________________________

#Recepcion de mensajes de Whatsapp - Zoho

@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    """
    Función Principal de envio de mensajes a Zoho
    """
    data = request.json or {}
    logging.info(f"/api/from-waba - mensaje recibido: {data}")

    user_id = data.get("user_id")
    #user_name = data.get("name")#nuevo
    user_first_name = data.get("first_name")#nuevo
    user_last_name = data.get("last_name")#nuevo
    user_msg = data.get("message")
    tag_name = data.get("tag", "soporte_urgente")
    tag_color = data.get("tag_color") or "#FF5733"

    #Se crea mensaje para agregar el cambio de etiqueta
    mensaje_formateado = ""
    
    
    #No muestra redundancia en el chat que esta en zoho
    if mensaje_formateado.strip().startswith("[🤖 Bot]:") or mensaje_formateado.strip().startswith("[👤 Usuario]:"):
        logging.info(f"from-waba:Mensaje ya formateado detectado, ignorando para evitar bucle.")
        return {"status": "bucle prevenido"}, 200
    elif tag_name == "respuesta_bot":
        mensaje_formateado = f"[🤖 Bot]: {user_msg}"
    else:
        mensaje_formateado = f"[👤 Usuario]: {user_msg}"

    

    if not user_id:
        return jsonify({"error":"missing user_id" }), 400
    
    #1. Busca si existe una conversaicon abierta
    conversation_id = busca_conversacion(user_id)

    if conversation_id:

        envio_mensaje = envio_mesaje_a_conversacion(conversation_id,mensaje_formateado)

        # Si se encontró, devuelve el ID
        return jsonify({
            "status": "Mensaje enviado",
            "message": "Mensaje agrego a la conversacion existente...",
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
        #nombre = user_name #f"whatsapp {user_id}"
        nombre = user_first_name #nuevo
        apellido = user_last_name #nuevo
        nombre_completo = nombre + ' ' + apellido
        telefono = user_id

        #Crear o actualizar visitante (importante captura el tag)
        visitor_resp, status = create_or_update_visitor(visitor_id_local, nombre_completo, nombre, apellido, telefono, "whatsapp", tag_name)
        
        # Extraer visitor_id real de Zoho (si lo genera)
        zoho_visitor_id = None


        #No fue posible cambiar esta estructura, se debe evaluar si se puede hacer mas secilla
        if isinstance(visitor_resp, dict):
            zoho_visitor_id = (
                visitor_resp.get("data", [{}])[0].get("id")
                if isinstance(visitor_resp.get("data"), list)
                else visitor_resp.get("data", {}).get("id")
            ) or visitor_id_local
        
        if not zoho_visitor_id:
            logging.error(f"No se puedo crear o encontrar el visitante en zoho. Abortando...")

            return jsonify({
                "status": "error",
                "message": "Fallo al crear el visitante en zoho",
                "details": visitor_resp
                }),500

        #Crear conversacion con el primer mensaje

        if mensaje_formateado:
            conv_resp = create_conversation_if_configured(
                zoho_visitor_id, 
                nombre_completo,
                nombre,
                apellido, 
                telefono, 
                mensaje_formateado
                )
        
        return jsonify({
            "status": "ok",
            "visitor_resp": visitor_resp,
            "visitor_status_code": status,
            "conversation_resp": conv_resp,
            "visitor_id": zoho_visitor_id
        }), final_status_code
#________________________________________________________________________________________

#Envío de Mensajes desde Zoho - Whatsapp

@app.route('/api/from-zoho', methods=['POST'])
def from_zoho():
    """
    Este endpoint, recibo las respuestas enviadas al webhooks de zoho, cuando un agente responde
    """
    try:
        zoho_data = request.json
        logging.info(f"from-zoho: Webhook recibida de Zoho: {zoho_data}")

        event_type = zoho_data.get('event')
        if event_type != "conversation.operator.replied":
            logging.warning(f"Evento ignorado porque no es una respuesta de operador: '{event_type}'")
            return {"status": "evento ignorado"}, 200
        

        """
        # En zoho no existe en el diccionario "data" si no "entity"
        
        main_entity = zoho_data.get("entity", {})
        
        message_text = main_entity.get("message",{}).get("text")
        visitor_info = main_entity.get("visitor", {})

        visitor_phone = visitor_info.get("phone")

        if not message_text or not visitor_phone:
            logging.error(f"Faltan datos en la webhook tras procesar 'entity': Mensaje='{message_text}', Telefono='{visitor_phone}'")
            return {"status": "datos incompletos"}, 400
        
        #No muestra redundancia en el chat que esta en el whatsapp
        if message_text.strip().startswith("[🤖 Bot]:") or message_text.strip().startswith("[👤 Usuario]:"):
            logging.info(f"Eco de mensaje de bot detectado. Se ignora para evitar bucle...")
            return {"status":"eco de bot ignorado"}, 200
        """
        main_entity = zoho_data.get("entity", {})
        message_info = main_entity.get("message", {}) # Obtenemos el diccionario 'message' completo

        # Extraemos los datos del diccionario 'message_info'
        message_text = message_info.get("text")
        sender_name = message_info.get("sender", {}).get("name")

        # Inicio lógica anti-bucle
        # Se añade 'message_text and' para evitar errores si el mensaje está vacío
        if sender_name == "TicAll-Bot" and message_text and message_text.strip().startswith("[🤖 Bot]:"):
            logging.info("Eco de mensaje de bot detectado. Ignorando para evitar segundo envío.")
            return {"status": "eco de bot ignorado"}, 200
        
        #No muestra redundancia en el chat que esta en el whatsapp
        if message_text.strip().startswith("[🤖 Bot]:") or message_text.strip().startswith("[👤 Usuario]:"):
            logging.info(f"Eco de mensaje de bot detectado. Se ignora para evitar bucle...")
            return {"status":"eco de bot ignorado"}, 200

        
        visitor_info = main_entity.get("visitor", {})
        visitor_phone = visitor_info.get("phone")

        if not message_text or not visitor_phone:
            logging.error(f"Faltan datos en la webhook tras procesar 'entity': Mensaje='{message_text}', Telefono='{visitor_phone}'")
            return {"status": "datos incompletos"}, 400



            
        payload_for_app_a = {
            "phone_number": visitor_phone,
            "message": message_text,
            "sender_role": "human_agent"
        }

        logging.info(f"Payload que App B va a enviar a App A: {payload_for_app_a}")
        url = f"{APP_A_URL}/api/envio_whatsapp"
        
        response = requests.post(url, json=payload_for_app_a, timeout=20)
        
        logging.info(f"Respuesta recibida de App A: Status={response.status_code}, Body='{response.text}'")
        response.raise_for_status()
        
        return {"status": "enviado a App A"}, 200

    except requests.exceptions.RequestException as e:
        logging.error(f"Error de CONEXIÓN al llamar a App A: {e}")
        return {"status": "error de conexión"}, 500
    except Exception as e:
        logging.error(f"Error inesperado en from_zoho: {e}")
        return {"status":"error interno"}, 500
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