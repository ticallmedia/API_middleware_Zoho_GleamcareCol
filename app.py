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

    # =========================================
    # PASO 1: Intentar ACTUALIZAR (PATCH)
    # =========================================
    update_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors/{visitor_id}"
    
    logging.info(f"Intentando actualizar visitante: PATCH {update_url}")
    logging.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        r_update = requests.patch(update_url, headers=headers, json=payload, timeout=10)
        logging.info(f"PATCH respuesta: status={r_update.status_code}, body={r_update.text[:300]}")
        
        if r_update.status_code in [200, 201, 204]:
            logging.info(f"✅ Visitante {visitor_id} ACTUALIZADO exitosamente")
            return r_update.json() if r_update.text else {"success": True, "updated": True}, r_update.status_code
        
        elif r_update.status_code == 404:
            # Visitante no existe, intentar crear
            logging.info(f"Visitante {visitor_id} no existe. Intentando crear...")
            
        else:
            # Otro error
            logging.error(f"Error en PATCH: {r_update.status_code} - {r_update.text}")
            return {"error": "update_failed", "details": r_update.text}, r_update.status_code
    
    except requests.exceptions.RequestException as e:
        logging.error(f"Excepción en PATCH: {e}")
    
    # =========================================
    # PASO 2: CREAR nuevo visitante (POST)
    # =========================================
    create_url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"
    
    # Ahora SÍ incluir 'id' en el payload
    payload["id"] = str(visitor_id)
    
    logging.info(f"Creando nuevo visitante: POST {create_url}")
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




def update_visitor_via_contacts_api(visitor_id, nombre, apellido, email, telefono):
    """
    Alternativa: Usar la API de Contacts de Zoho para actualizar datos
    
    Esta API es más flexible para actualizaciones
    """
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}, 401
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    # Endpoint de Contacts (diferente de Visitors)
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/contacts"
    
    payload = {
        "visitor_id": str(visitor_id),
        "name": f"{nombre} {apellido}".strip(),
        "email": email,
        "phone": telefono
    }
    
    if nombre:
        payload["first_name"] = nombre
    if apellido:
        payload["last_name"] = apellido
    
    logging.info(f"Actualizando contacto: POST {url}")
    logging.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        logging.info(f"Contacts API respuesta: {r.status_code} - {r.text[:300]}")
        
        if r.status_code in [200, 201]:
            return r.json(), r.status_code
        else:
            return {"error": "contacts_update_failed", "details": r.text}, r.status_code
    
    except Exception as e:
        logging.error(f"Error en Contacts API: {e}")
        return {"error": str(e)}, 500


def update_conversation_with_visitor_data(conversation_id, nombre, apellido, email):
    """
    Actualiza los datos del visitante a través de la conversación
    """
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}, 401
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conversation_id}"
    
    payload = {
        "visitor": {
            "name": f"{nombre} {apellido}".strip(),
            "email": email,
            "first_name": nombre,
            "last_name": apellido
        }
    }
    
    try:
        r = requests.patch(url, headers=headers, json=payload, timeout=10)
        logging.info(f"Conversación actualizada: {r.status_code} - {r.text[:200]}")
        
        if r.status_code in [200, 201, 204]:
            return r.json() if r.text else {"success": True}, r.status_code
        else:
            return {"error": "conversation_update_failed", "details": r.text}, r.status_code
    
    except Exception as e:
        return {"error": str(e)}, 500
    




@app.route('/api/update-visitor-data', methods=['POST'])
def update_visitor_data():
    """
    Versión simplificada: Solo actualiza custom_fields
    (No requiere acceso a CRM)
    """
    data = request.json or {}
    
    user_id = data.get("user_id")
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    email = data.get("email", "")
    servicio = data.get("servicio", "")
    descripcion = data.get("descripcion", "")
    
    if not user_id:
        return jsonify({"error": "missing user_id"}), 400
    
    visitor_id = f"whatsapp_{user_id}"
    nombre_completo = f"{first_name} {last_name}".strip()
    
    # Custom fields (ESTO SÍ FUNCIONA en SalesIQ)
    custom_fields = {
        "canal": "whatsapp",
        "nombre_real": first_name,
        "apellido_real": last_name,
        "email_real": email,
        "servicio_interes": servicio,
        "descripcion_negocio": descripcion[:500],  # Limitar a 500 chars
        "datos_completos": "SI"
    }
    
    visitor_resp, status = create_or_update_visitor(
        visitor_id=visitor_id,
        nombre_completo=nombre_completo,
        telefono=user_id,
        nombre=first_name,
        apellido=last_name,
        email=email,
        custom_fields=custom_fields
    )
    
    # Agregar nota visible en conversación
    conv_id = busca_conversacion(user_id)
    if conv_id:
        nota = (
            f"📋 **INFORMACIÓN DEL LEAD ACTUALIZADA**\n\n"
            f"👤 Nombre: {first_name} {last_name}\n"
            f"📧 Email: {email}\n"
            f"🎯 Servicio: {servicio}\n"
            f"📝 Descripción: {descripcion[:200]}..."
        )
        envio_mesaje_a_conversacion(conv_id, nota)
    
    return jsonify({
        "status": "success",
        "visitor_updated": status in [200, 201],
        "custom_fields": custom_fields,
        "note_added_to_conversation": conv_id is not None
    }), 200


#conexion con api_crm
"""
@app.route('/api/update-visitor-data', methods=['POST'])
def update_visitor_data():
    #    Actualiza datos del visitante usando el método que SÍ funciona


    data = request.json or {}
    logging.info(f"/api/update-visitor-data - Datos recibidos: {data}")
    
    user_id = data.get("user_id")
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    email = data.get("email", "").strip()
    servicio = data.get("servicio", "")
    descripcion = data.get("descripcion", "")
    
    if not user_id:
        return jsonify({"error": "missing user_id"}), 400
    
    visitor_id = f"whatsapp_{user_id}"
    nombre_completo = f"{first_name} {last_name}".strip()
    
    # MÉTODO 1: Actualizar custom_fields en SalesIQ (siempre funciona)
    custom_fields = {
        "canal": "whatsapp",
        "nombre_real": first_name,
        "apellido_real": last_name,
        "email_real": email,
        "servicio_interes": servicio,
        "descripcion_negocio": descripcion,
        "datos_completos": "true",
        "ultima_actualizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    visitor_resp, status = create_or_update_visitor(
        visitor_id=visitor_id,
        nombre_completo=nombre_completo,
        telefono=user_id,
        nombre=first_name,
        apellido=last_name,
        email=email,
        custom_fields=custom_fields
    )
    
    # MÉTODO 2: Sincronizar con Zoho CRM (recomendado)
    crm_resp, crm_status = sync_visitor_to_zoho_crm(
        visitor_id=visitor_id,
        nombre=first_name,
        apellido=last_name,
        email=email,
        telefono=user_id,
        servicio=servicio,
        descripcion=descripcion
    )
    
    # Resultado
    result = {
        "status": "success",
        "salesiq_update": {
            "status_code": status,
            "custom_fields_updated": True if status in [200, 201] else False,
            "details": visitor_resp
        },
        "crm_sync": {
            "status_code": crm_status,
            "synced": True if crm_status in [200, 201] else False,
            "details": crm_resp
        }
    }
    
    logging.info(f"✅ Resultado final: {json.dumps(result, indent=2)}")
    
    return jsonify(result), 200



def sync_visitor_to_zoho_crm(visitor_id, nombre, apellido, email, telefono, servicio, descripcion):
    #    Sincroniza los datos del visitante directamente a Zoho CRM    Esta es la forma CORRECTA de actualizar información de contactos
    
    access_token = get_access_token()
    if not access_token:
        return {"error": "no_access_token"}, 401
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    # Endpoint de Zoho CRM (diferente de SalesIQ)
    crm_url = "https://www.zohoapis.com/crm/v2/Contacts/upsert"
    
    payload = {
        "data": [{
            "Phone": telefono,
            "First_Name": nombre,
            "Last_Name": apellido,
            "Email": email,
            "Lead_Source": "WhatsApp Bot",
            "Description": f"Servicio de interés: {servicio}\n\n{descripcion}",
            "Visitor_ID": visitor_id  # Campo personalizado para vincular
        }],
        "duplicate_check_fields": ["Phone"],  # Actualiza si ya existe
        "trigger": ["approval", "workflow", "blueprint"]
    }
    
    logging.info(f"Sincronizando con Zoho CRM: {crm_url}")
    logging.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        r = requests.post(crm_url, headers=headers, json=payload, timeout=10)
        logging.info(f"CRM respuesta: {r.status_code} - {r.text[:500]}")
        
        if r.status_code in [200, 201]:
            logging.info(f"✅ Contacto sincronizado en Zoho CRM")
            return r.json(), r.status_code
        else:
            return {"error": "crm_sync_failed", "details": r.text}, r.status_code
    
    except Exception as e:
        logging.error(f"Error sync CRM: {e}")
        return {"error": str(e)}, 500

"""


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

    nombre = user_first_name or f"whatsapp {user_id}"
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