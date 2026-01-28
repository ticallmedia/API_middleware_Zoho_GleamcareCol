from flask import Flask, render_template, request, jsonify, json
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

Actualiza 08/01/2026:
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

def create_or_update_visitor(visitor_id, nombre_completo, telefono, nombre=None, apellido=None, email=None, custom_fields=None):
    """
    Crea o actualiza visitante en Zoho SalesIQ v2
    
    ESTRATEGIA:
    1. Intentar actualizar con PATCH (asume que existe)
    2. Si falla con 404, crear con POST
    """
    access_token = get_access_token()
    if not access_token:
        logging.error("create_or_update_visitor: no se obtuvo access_token valido")
        return {"error": "no_access_token"}, 401
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}", 
        "Content-Type": "application/json"
    }

    # Construir payload (sin 'id' porque va en la URL)
    payload = {
        "name": nombre_completo,
        "contactnumber": str(telefono)
    }
    
    if nombre:
        payload["first_name"] = nombre
    if apellido:    
        payload["last_name"] = apellido
    if email:
        payload["email"] = email
    if custom_fields:
        payload["custom_fields"] = custom_fields


    create_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    
    # Ahora SÍ incluir 'id' en el payload
    payload["id"] = str(visitor_id)
    
    logging.info(f"create_or_update_visitor: Creando nuevo visitante POST {create_url}")
    logging.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        r_create = requests.post(create_url, headers=headers, json=payload, timeout=10)
        logging.info(f"POST respuesta: status={r_create.status_code}, body={r_create.text[:300]}")
        
        if r_create.status_code in [200, 201]:
            logging.info(f"✅ Visitante {visitor_id} CREADO exitosamente")
            return r_create.json(), r_create.status_code
        else:
            logging.error(f"Error en POST: {r_create.status_code} - {r_create.text}")
            return {"error": "create_failed", "details": r_create.text}, r_create.status_code
    
    except requests.exceptions.RequestException as e:
        logging.error(f"Excepción en POST: {e}")
        return {"error": str(e)}, 500


def create_conversation_if_configured(visitor_user_id, nombre_completo, nombre, apellido, email, telefono,question):
    """
    Crea conversaciones en SalesIQ
    """
    
    url = f"https://salesiq.zoho.com/visitor/v2/{ZOHO_PORTAL_NAME}/conversations"
    payload = {
        "visitor": {"user_id": visitor_user_id, "name": nombre_completo, "first_name": nombre, "last_name": apellido, "email": email, "phone": telefono},
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
        logging.info(f"busca_conversacion:Buscando conversación abierta para el teléfono: {phone}")
        #response = requests.get(url, headers=headers, timeout=10)
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        response.raise_for_status()  # Verificar si hubo errores HTTP
        response_data = response.json()

        #if 'data' in response_data and response_data.get('data',[]):
        if response_data.get('data'):
            lista_conversaciones = response_data.get('data')

            for conv in lista_conversaciones:
                conversation_id = conv.get('id')
                visitor = conv.get('visitor',{})

                if visitor:
                    visitor_name = visitor.get('name')
                    visitor_phone = visitor.get('phone')
                    chat_status = conv.get('chat_sttus',{})#es un diccionario
                    status_key = chat_status.get('status_key')
                    state = chat_status.get('state')

                    # 1. Teléfono debe coincidir
                    # 2. Estado debe ser "open"
                    # 3. state debe ser 1 (waiting) o 2 (connected) - NO 3 (ended)
                    # 4. No debe tener un agente humano activo (attender)

                    attender = conv.get('attender')
                    #revisa si esta asignado a un agente humano
                    is_bot_conversation = not attender or attender.get('is_bot', False)

                    if (phone == visitor_phone and
                        status_key == "open" and
                        state in (1,2) and
                        is_bot_conversation):

                        logging.info(
                            f"busca_conversacion:El telefono buscado coincide - "
                            f"Conversation:{conversation_id},telefono: {visitor_phone}, visitor: {visitor_name},"
                            f"status_key: {status_key}, state: {state}"
                            )
                        #logging.info(f"busca_conversacion:Se encontró una conversación abierta con ID: {conversation_id} para el telefono: {phone}")
                        return conversation_id

        logging.info(f"busca_conversacion: No se encontraron conversaciones abiertas para el teléfono {phone}")
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
        mensaje = "[👤 Usuario]: 📱DDA & Mobile Campaigns"
    elif "btn_2" in mensaje:
        mensaje = "[👤 Usuario]: 📊Display Media Planning"
    elif "btn_3" in mensaje:
        mensaje = "[👤 Usuario]: 🛒Ecommerce Strategy"
    elif "btn_4" in mensaje:
        mensaje = "[👤 Usuario]: 📣Paid Social Media"
    elif "btn_5" in mensaje:
        mensaje = "[👤 Usuario]: 🎯Audience Studies"
    elif "btn_6" in mensaje:
        mensaje = "[👤 Usuario]: 🚀Digital Marketing"
    elif "btn_7" in mensaje:
        mensaje = "[👤 Usuario]: 📰Media Strategy"
    elif "btn_8" in mensaje:
        mensaje = "[👤 Usuario]: 🤖Custom Bot Development"
    elif "btn_9" in mensaje:
        mensaje = "[👤 Usuario]: 🌐WebSites"
    elif "btn_0" in mensaje:
        mensaje = "[👤 Usuario]: 🗣️Talk to an Agent"
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
def asignar_tag_a_conversacion(conversation_id, tag_id):
    """
    Asigna un tag a una conversación existente en Zoho
    """
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}, 401
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conversation_id}/tags"
    
    payload = {
        "tag_ids": [tag_id] if isinstance(tag_id, str) else tag_id
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"Tag asignado: {r.status_code} - {r.text}")
        return r.json() if r.text else {"success": True}, r.status_code
    except Exception as e:
        logging.error(f"Error asignando tag: {e}")
        return {"error": str(e)}, 500
#________________________________________________________________________________________

#Recepcion de mensajes de Whatsapp - Zoho

@app.route('/api/from-waba', methods=['POST'])
def from_waba():
    """
    Función Principal
    """
    data = request.json or {}
    logging.info(f"/api/from-waba - mensaje recibido: {data}")

    user_id = data.get("user_id")
    user_msg = data.get("message")
    tag_name = data.get("tag", "soporte_urgente")  # Guardar para usar después
    tag_color = data.get("tag_color") or "#FF5733"

    user_first_name = data.get("first_name")
    user_last_name = data.get("last_name")
    user_email = data.get("email")
        
    if not user_id:
        return jsonify({"error": "missing user_id"}), 400

    visitor_id_local = f"whatsapp_{user_id}"

    nombre = user_first_name or f"Visitante {user_id}"
    apellido = user_last_name or ""
    email = user_email or f"{user_id}@email.com"
    nombre_completo = f"{nombre} {apellido}".strip()

    visitor_resp, status = create_or_update_visitor(
        visitor_id=visitor_id_local, 
        nombre_completo=nombre_completo, 
        telefono=user_id, 
        nombre=user_first_name, 
        apellido=user_last_name, 
        email=user_email, 
        custom_fields={"canal": "whatsapp"}
        # tag_ids=tag_name  ← ELIMINAR ESTO
    )
        
    if status >= 400:
        logging.warning(f"No se pudo crear/actualizar el visitante. Detalle: {visitor_resp}")

    zoho_visitor_id = None
    if isinstance(visitor_resp, dict):
        zoho_visitor_id = (
            visitor_resp.get("data", [{}])[0].get("id")
            if isinstance(visitor_resp.get("data"), list)
            else visitor_resp.get("data", {}).get("id")
        ) or visitor_id_local
    
    if not zoho_visitor_id:
        logging.error(f"No se pudo crear o encontrar el visitante en Zoho")
        return jsonify({
            "status": "error",
            "message": "Fallo al crear el visitante en Zoho",
            "details": visitor_resp
        }), 500

    # Si no hay mensaje, solo actualización de datos
    if not user_msg:
        logging.info(f"✅ Datos del visitante {user_id} actualizados. No hay mensajes.")
        return jsonify({
            "status": "datos_actualizados",
            "visitor_id": zoho_visitor_id
        }), 200
    
    mensaje_formateado = f"[👤 Usuario]: {user_msg}"
    if tag_name == "respuesta_bot":
        mensaje_formateado = f"[🤖 Bot]: {user_msg}"

    conversation_id = busca_conversacion(user_id)

    if conversation_id:
        #Asignar tag a conversación existente
        if tag_name:
            asignar_tag_a_conversacion(conversation_id, tag_name)
        
        envio_mensaje = envio_mesaje_a_conversacion(conversation_id, mensaje_formateado)
        return jsonify({
            "status": "mensaje_enviado",
            "conversation_id": conversation_id
        }), 200
    else:
        # Crear nueva conversación
        conv_resp = create_conversation_if_configured(
            zoho_visitor_id, nombre_completo, nombre, apellido, email, user_id, mensaje_formateado
        )
        
        # Asignar tag a conversación recién creada
        if conv_resp and isinstance(conv_resp, dict):
            new_conv_id = conv_resp.get("data", {}).get("id")
            if new_conv_id and tag_name:
                asignar_tag_a_conversacion(new_conv_id, tag_name)
        
        return jsonify({
            "status": "conversacion_creada",
            "conversation_resp": conv_resp
        }), 201
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