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

Versión: 1.4

Actualiza 08/01/2026:
- Se buscar Visitante si no existe crea visisitante y con el fin de poder visitor_id, y 
con este ultimo buscar una nueva conversación, si no existe crearla.

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

    REDIRECT_URI = "https://api-middleware-zoho-gleamcarecol.onrender.com/oauth2callback"

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
    
    try:
        logging.info(f"busca_conversacion:Buscando conversación abierta para el teléfono: {phone}")
        response = requests.get(url, headers=headers, timeout=10)
        
        response.raise_for_status()  # Verificar si hubo errores HTTP
        response_data = response.json()

        if 'data' in response_data and response_data.get('data'):

        #if response_data.get('data'):
            #logging.info(f"busca_conversacion: control................1")
            #lista_conversaciones = response_data['data']
            lista_conversaciones = response_data.get('data')
            #logging.info(f"busca_conversacion: json generado................ {lista_conversaciones}")

            for conv in lista_conversaciones:
                conversation_id = conv.get('id')
                visitor = conv.get('visitor',{})
                #logging.info(f"busca_conversacion: control id................{conv}")
                #logging.info(f"busca_conversacion: control id................{conversation_id}")

                if visitor:
                    #logging.info(f"busca_conversacion: control................{visitor}")
                    # 1. Teléfono debe coincidir
                    # 2. Estado debe ser "open"
                    # 3. state debe ser 1 (waiting) o 2 (connected) - NO 3 (ended)
                    # 4. No debe tener un agente humano activo (attender)
                    
                    visitor_name = visitor.get('name')
                    visitor_phone = visitor.get('phone')
                    chat_status = conv.get('chat_sttus',{})#es un diccionario
                    status_key = chat_status.get('status_key')
                    state = chat_status.get('state')
                    attender = conv.get('attender')
                    #revisa si esta asignado a un agente humano
                    is_bot_conversation = not attender or attender.get('is_bot', False)
                    logging.info(f"busca_conversacion: phone................{phone}")
                    logging.info(f"busca_conversacion: visitor_phone................{visitor_phone}")
                    logging.info(f"busca_conversacion: status_key................{status_key}")
                    logging.info(f"busca_conversacion: state................{state}")
                    logging.info(f"busca_conversacion: is_bot_conversation................{is_bot_conversation}")

                    if (visitor_phone == phone and
                        status_key == "open" and
                        state in (1,2) and
                        is_bot_conversation):

                        logging.info(
                            f"busca_conversacion: El telefono buscado coincide - "
                            f"Conversation:{conversation_id},telefono: {visitor_phone}, visitor: {visitor_name},"
                            f"status_key: {status_key}, state: {state}"
                            )
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

def limpiar_telefono(telefono):
    # Implementación arriba

    """
    Limpia y estandariza el formato del teléfono
    """
    if not telefono:
        return ""
    
    telefono_limpio = str(telefono).strip().replace(' ','').replace('-','').replace('(','').replace(')','')

    if telefono_limpio.startswith('+'):
        telefono_limpio = '+' + telefono_limpio
    
    return telefono_limpio

# FUNCIONES DE VISITANTES
def obtener_o_crear_visitante(telefono):
    # Implementación arriba
    """
    Buscar un visitante existente, si no existe lo crea 
    retorna el visitor_id
    """

    logging.info(f"obtener_o_crear_visitante: buscando visitante con telefono: {telefono}")

    #1. buscar el visitante existente
    visitor_id = buscar_visitante_por_telefono(telefono)
    
    #2. si no existe , crear nuevo
    if not visitor_id:
        logging.info(f"obtener_o_crear_visitante: visitante no encontrado, crenado nuevo...")
        visitor_id = crear_visitante(telefono)
    else:
        logging.info(f"obtener_o_crear_visitante: Visitante existente encontrado: {visitor_id}")

    return visitor_id

def buscar_visitante_por_telefono(telefono):
    # Implementación arriba
    """
    Buscar un visitante existente por número de telefono
    retorna el visitor_id si existe, None si no existe
    """

    access_token = get_access_token()
    if not access_token:
        logging.error("buscar_visitante_por_telefono: No se pudo obtener un access_token válido. Abortando búsqueda.")
        return {"error": "no_access_token"}, 401
    
    #limpiar teléfono
    telefono_limpio = limpiar_telefono(telefono)

    # URL para listar visitante
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        logging.info(f"buscar_visitante_por_telefono: URL: {url}")
        logging.info(f"buscar_visitante_por_telefono: response... {response.status_code}")
        logging.info(f"buscar_visitante_por_telefono: response text... {response.text}")
        
        
        logging.info(f"buscar_visitante_por_telefono: CONTROL...response... {response}")

        if response.status_code == 200:            
            data = response.json()
            visitantes = data.get('data',[])

            logging.info(f"buscar_visitante_por_telefono: CONTROL...visitantes... {visitantes}")

            #Buscar visitante que coincida con el teléfono
            for visitante in visitantes:
                phone_visitante = visitante.get('phone','')
                phone_limpio = limpiar_telefono(phone_visitante)
                logging.info(f"buscar_visitante_por_telefono: CONTROL...phone_visitante...:{phone_visitante}")
                logging.info(f"buscar_visitante_por_telefono: CONTROL...phone_limpio...:{phone_limpio}")

                if phone_limpio == telefono_limpio:
                    visitor_id = visitante.id('id')
                    logging.info(f"buscar_visitante_por_telefono: Visitante encontrado_ ID= {visitor_id}, telefono= {phone_visitante}")
                    return visitor_id
            return None        
        else:
            logging.error(f"buscar_visitante_por_telefono: Error al buscar visitante: {response.status_code}")
            return None

    except Exception as e:
        logging.error(f"buscar_visitante_por_telefono: Excepción al buscar convarsación: {str(e)}")    
        return None
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"buscar_visitante_por_telefono: Error HTTP de la API de Zoho. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logging.error(f"buscar_visitante_por_telefono: Error de conexión (Timeout, DNS, etc): {req_err}")
        return None

def crear_visitante(telefono):
    # Implementación arriba
    """
    Crear un nuevo visitante con el teléfono de whatsapp
    retonra el visitor_id del visiatante creado
    """
    access_token = get_access_token()

    if not access_token:
        return {"error": "no_access_token"}, 401
    
    #limpiar teléfono
    telefono_limpio = limpiar_telefono(telefono)

    # URL para listar visitante
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/visitors"

    payload = {
        "name": f"Visitante {telefono_limpio}",
        "phone": telefono_limpio,
        "user_id": f"whatsapp_{telefono_limpio.replace('+','')}",
        "info": json.dumps({
            "source":"Whatsapp",
            "whatsapp_number":telefono_limpio
        })
    }

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, json=payload)
        
        if response.status_code in [200, 201]:
            data = response.json()
            visitor_id = data.get('data',{}.get('id'))
            logging.info(f"crear_visitante: Visitante creado existosamente {visitor_id}")
            return visitor_id  
        else:
            logging.error(f"crear_visitante: Error creando visitante: {response.status_code}")
            return None

    except Exception as e:
        logging.error(f"crear_visitante: Exception creando visitante: {str(e)}")
        return None   

# FUNCIONES DE CONVERSACIONES
#def buscar_conversacion_abierta_por_visitor(visitor_id):
def buscar_conversacion_abierta_por_visitor(telefono):
    # Implementación arriba
    """
    Busca conversaciones abiertas para un visitor_id especifico
    Retona la conversación si existe, None si no
    """
    access_token = get_access_token()

    logging.info(f"buscar_conversacion_abierta_por_visitor: Buscando conversación abierta para visitor_id: {telefono}")

    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations"

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    params = {
        "phone": telefono,
        "status": "open"
    }

    try:
        #response = requests.get(url, headers=headers)
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code == 200:
            response.raise_for_status()  # Verificar si hubo errores HTTP
            data = response.json()
            conversaciones = data.get('data',[])

            #filtrando conversaciones del visitante que estén abiertas
            for conv in conversaciones:
                conv_visitor_id = conv.get('visitor',{}).get('id','')
                conv_phone = conv.get('visitor',{}).get('phone','')
                conv_id = conv.get('id', '') 
                #chat_status = conv.get('chat_status',{})
                #status_key = chat_status.get('status_key','')

                #verificar que sea del mismo visitante y esté abierta
                #if conv_visitor_id == visitor_id and status_key =='open':
                logging.info(f"buscar_conversacion_abierta_por_visitor: Numero de telefono del visitante: {conv_phone}")
                if conv_phone == telefono:
                    #chat_id = conv.get('chat_id')
                    logging.info(f"buscar_conversacion_abierta_por_visitor: Conversación abierta encontrada: {conv_id}, para el visitor: {conv_visitor_id}")
                    return conv_id
            
            #logging.info(f"buscar_conversacion_abierta_por_visitor: No hay conversaciones abierta para el visitor_id {visitor_id}")
            logging.info(f"buscar_conversacion_abierta_por_visitor: No hay conversaciones abierta para el visitor_id {conv_visitor_id}")
            return None
        
        else:
            logging.error(f"buscar_conversacion_abierta_por_visitor: Error listando conversaciones: {response.status_code}")
            return None

    except requests.exceptions.HTTPError as http_err:
        logging.error(f"buscar_conversacion_abierta_por_visitor: Error HTTP de la API de Zoho. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logging.error(f"buscar_conversacion_abierta_por_visitor: Error de conexión (Timeout, DNS, etc): {req_err}")
        return None
    except Exception as e:
        logging.error(f"buscar_conversacion_abierta_por_visitor: Excepción al buscar convarsación: {str(e)}")    
        return None

def crear_conversacion_con_visitante(visitor_id, telefono, mensaje_inicial):
    # Implementación arriba
    """
    Crea una conversación asociada a un visitante
    """
    access_token = get_access_token()
    logging.info(f"crear_conversacion_con_visitante: Creando conversación para el visitor_id: {visitor_id}")

    url = f"https://salesiq.zoho.com/visitor/v2/{ZOHO_PORTAL_NAME}/conversations"

    payload = {
        "visitor": {"user_id": visitor_id, "phone": telefono},
        "app_id": SALESIQ_APP_ID,
        "department_id": SALESIQ_DEPARTMENT_ID,
        "question": mensaje_inicial #,"auto_assign": True
        }

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)

        logging.info(f"crear_conversacion_con_visitante: Respuesta crear conversación: {response.status_code}")

        if response.status_code in [200, 201]:
            data = response.json()
            conversacion = data.get('data',[])

            conversation_id = data.get('id')
            visitor = data.get('visitor',{})
            #chat_id = conversacion.get('chat_id')

            #logging.info(f"crear_conversacion_con_visitante: Conversación creada: {chat_id}")
            logging.info(f"crear_conversacion_con_visitante: Conversación creada: {visitor}")

            """
            return {
                'chat_id': chat_id,
                'visitor_id': visitor_id,
                'conversacion': conversacion
            }            
            """
            return {
                'conversacion_id': conversation_id,
                'visitor_id': visitor,
                'conversacion': conversacion
            }

        else:
            logging.error(f"crear_conversacion_con_visitante: Error creando conversación: {response.text}")
            return None
    except Exception as e:
        logging.error(f"crear_conversacion_con_visitante: Excepción al buscar convarsación: {str(e)}")    
        return {"error": str(e)}

#def enviar_mensaje_a_conversacion(chat_id, mensaje):
def enviar_mensaje_a_conversacion(conversacion_abierta, mensaje):

    # Implementación arriba
    """
    Envía un mensaje a una conversación existente
    """

    if "btn_si1" in mensaje:
        mensaje = "[👤 Usuario]: Si"
    elif "btn_no1" in mensaje:
        mensaje = "[👤 Usuario]: No"
    elif "btn_1" in mensaje:
        mensaje = "[👤 Usuario]: 🔄TicAll Flow®️Ecosys"
    elif "btn_2" in mensaje:
        mensaje = "[👤 Usuario]: 🤖Custom AI Agents"
    elif "btn_3" in mensaje:
        mensaje = "[👤 Usuario]: 🛒Ecommerce Arch"
    elif "btn_4" in mensaje:
        mensaje = "[👤 Usuario]: ⚡Performance Arch"
    elif "btn_5" in mensaje:
        mensaje = "[👤 Usuario]: 📈Demand Generation"
    elif "btn_6" in mensaje:
        mensaje = "[👤 Usuario]: 🌐High-Performance Webs"
    elif "btn_0" in mensaje:
        mensaje = "[👤 Usuario]: 🗣️Talk to an Agent"
    else:
        mensaje
            

    access_token = get_access_token()
    """
    if not access_token:
        logging.error(f"enviar_mensaje_a_conversacion: No se pudo obtener un access_token válido. Abortando búsqueda.")
        return None
    """
    
    #logging.info(f"enviar_mensaje_a_conversacion: Enviando mensaje a conversación: {chat_id}")
    logging.info(f"enviar_mensaje_a_conversacion: Enviando mensaje a conversación: {conversacion_abierta}")

    #url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{chat_id}/message"
    url = f"{ZOHO_SALESIQ_BASE}/{ZOHO_PORTAL_NAME}/conversations/{conversacion_abierta}/messages"
        
    headers = {
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'Content-Type': 'application/json'
    }

    payload = {
        "text": mensaje
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Verificar si hubo errores HTTP
        
        """
        response_data =  response.json()
        logging.info(f"enviar_mensaje_a_conversacion: respuesta de API: {response_data}")
        return True
        """
        
        if response.status_code in [200, 201]:
            logging.info(f"enviar_mensaje_a_conversacion: Mensaje enviado exitosamente, a la conversación: {conversacion_abierta}")
            return True
        else:
            logging.error(f"enviar_mensaje_a_conversacion: Error enviando mensaje: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"enviar_mensaje_a_conversacion: Error HTTP de la API de Zoho. Status: {http_err.response.status_code}, Body: {http_err.response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        logging.error(f"enviar_mensaje_a_conversacion: Error de conexión (Timeout, DNS, etc): {req_err}")
        return None
    except Exception as e:
        logging.error(f"enviar_mensaje_a_conversacion: Excepción al buscar convarsación: {str(e)}")    
        return None
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
    # 1. Recibe datos de WhatsApp desde App A
    # 2. Extrae teléfono y mensaje
    # 3. NUEVO: Obtener/crear visitante por teléfono → visitor_id
    # 4. Buscar conversación abierta (CON visitor_id)
    # 5a. Si existe: enviar mensaje a conversación
    # 5b. Si NO existe: crear conversación (CON visitor_id)
    # 6. Enviar mensaje
    """
    try:
        #========================================================
        # Paso 1: Recibir y Validar Datos
        #========================================================
        data = request.json or {}
        
        #if not data:
            #logging.error(f"from-waba: No se recibieron datos en el request")
            #return jsonify({"error":"No data received"}), 400
        logging.info(f"from-waba: mensaje recibido: {data}")
        
        #extraer información del mensaje de whatsapp
        telefono = data.get('user_id') or data.get('phone') #or data.get('from') or data.get('telefono')
        mensaje = data.get('message') or data.get('text')# or data.get('body')
        tag_name = data.get("tag", "soporte_urgente")

        mensaje_formateado = f"[👤 Usuario]: {mensaje}"
        if tag_name == "respuesta_bot":
            mensaje_formateado = f"[🤖 Bot]: {mensaje}"

        #validar que se cuenta con los datos minimos
        if not telefono or not mensaje:
            logging.error(f"from-waba: Datos incompletos: - telefono: {telefono}, mensaje: {mensaje_formateado}")
            return jsonify({
                "error": "Missing phone or message"
                }), 400

        logging.info(f"\n{'='*70}")
        logging.info(f"Mensaje de Whatsapp recibido:")
        logging.info(f"Telefono: {telefono}")
        logging.info(f"Mensaje: {mensaje_formateado[:100]}...")
        logging.info(f"\n{'='*70}\n")

        #========================================================
        # Paso 2: Obtener o crear visitante
        #========================================================
        """
        logging.info(f"PASO 1: Obteniendo o creando visitante... ")
        visitor_id = obtener_o_crear_visitante(telefono)

        if not visitor_id:
            logging.error(f"from-waba: No se pudo obtener o crear visitante")
            return jsonify({
                "error": "Failed to create/get visitor",
                "phone": telefono   
                }), 500

        logging.info(f"from-waba: Visitor ID obtenido:{visitor_id}")
        """
        visitor_id = f"whatsapp_{telefono}" #dato provisional

        #========================================================
        # Paso 3: Buscar conversaciones abiertas
        #========================================================
        logging.info(f"PASO 2: buscando conversación abierta... ")
        #conversacion_abierta = buscar_conversacion_abierta_por_visitor(visitor_id)
        conversacion_abierta = buscar_conversacion_abierta_por_visitor(telefono)

        chat_id = None
        #========================================================
        # Paso 4: Enviar mensaje a conversación existente o crear nueva
        #========================================================
        if conversacion_abierta:
            #caso A: Ya existe una conversación abierta
            #chat_id = conversacion_abierta.get('chat_id')
            #logging.info(f"PASO 3: Conversación abierta encontrada: {chat_id}")
            logging.info(f"PASO 3: Conversación abierta encontrada: {conversacion_abierta}")
            logging.info(f"PASO 3: Enviando mensaje a conversación existente... ")

            #resultado_envio = enviar_mensaje_a_conversacion(chat_id, mensaje)
            resultado_envio = enviar_mensaje_a_conversacion(conversacion_abierta, mensaje_formateado)

            if not resultado_envio:
                logging.error(f"PASO 3: Error al enviar mensaje a conversación: {chat_id}")
                return jsonify({
                    "error": "Failed to send message",
                    "chat_id": chat_id
                }),500
            
            logging.info(f"PASO 3: Mensaje Enviando exitosamente a: {conversacion_abierta} ")
        else:
            #Caso B: No existe conversación, crear nueva
            logging.info(f"PASO 3: No hay conversación abierta ")
            logging.info(f"PASO 3: Creando Nueva Conversación...")

            resultado = crear_conversacion_con_visitante(visitor_id, telefono, mensaje_formateado)
            
            if not resultado:
                logging.error(f"PASO 3: Error al crear conversación...")

                return jsonify({
                    "error": "Failed to create conversation",
                    "visitor_id": visitor_id
                }),500
            
            #chat_id = resultado['chat_id']
            #logging.error(f"PASO 3: Nueva Conversación creada: {chat_id}")

        #========================================================
        # Paso 5: Respuesta exitosa
        #========================================================
        logging.info(f"\n{'='*70}")
        logging.info(f"PASO 4: PROCESO COMPLETADO EXITOSAMENTE")
        logging.info(f"Visitor ID: {visitor_id}")
        #logging.info(f"Chat ID: {chat_id}")
        logging.info(f"\n{'='*70}\n")
        
        return jsonify({
            "success": True,
            "visitor_id": visitor_id,
            #"chat_id": chat_id,
            "phone": telefono,
            "action": "conversation_exists" if conversacion_abierta else "conversation_created"
        }), 200

    except Exception as e:
        logging.error(f"from-waba: Error Critico en form-waba: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())

        return jsonify({
            "error":"Internal server error",
            "details":str(e)
        }),500

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