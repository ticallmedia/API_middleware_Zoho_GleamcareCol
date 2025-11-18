# API_ app de Middleware se zoho y waba

#Descripción: 

#Es una App de puente entre, la App de WABA y Zoho SalesIQ, orientado la comunición hacia el agente humano y que permite utilizar las caracteristicas de Sales IQ como Chat Center.

#Versión: 1.0

#Caracteristicas: 
#- Cargar variables de entorno desde .env
#- no cuenta con bd

#Versión: 1.1

#Caracteristicas: 
#- Se agrega creacion de tabla de visitantes zoho, para capturar el visitor_id y evitar crea
#un chat por cada mensaje del usuario

#Versión: 1.2

#- Se establece generación de token provicional para abrir conversaciones
#- Mensaje de apertura de chat
#- Identificacion de conversación, se crea funcion  -- busca_conversacion(phone)
#- Continuacion de chat partiendo del id de la conversación , se modifica funcion from_waba()
#- Se crea funcion que envia mensajes si ya existe una conversacion, --envio_mesaje_a_conversacion(conversation_id,user_msg)
#- Se agrega variables globales CACHED_ACCESS_TOKEN, TOKEN_EXPIRATION_TIME para consultar access_token y solo crear cuando sea necesario
