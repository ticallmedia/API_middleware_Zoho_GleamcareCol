# API_ app de Middleware se zoho y waba

#Descripción: 

#Es una App de puente entre, la App de WABA y Zoho SalesIQ, orientado la comunición hacia el agente humano y que permite utilizar las caracteristicas de Sales IQ como Chat Center.

#Versión: 1.0 Actualiza 25/02/2026:

#Caracteristicas: 

#- Cargar variables de entorno desde .env
#- no cuenta con bd
#- Captura mensaja a mensaje de la App A hacia App b y finalmente a Zoho SalesIQ
#- Se agrega creacion de tabla de visitantes zoho, para capturar el visitor_id y evitar crea
#un chat por cada mensaje del usuario
#- Se establece generación de token provicional para abrir conversaciones
#- Mensaje de apertura de chat
#- Identificacion de conversación, se crea funcion  -- busca_conversacion(phone)
#- Continuacion de chat partiendo del id de la conversación , se modifica funcion from_waba()
#- Se crea funcion que envia mensajes si ya existe una conversacion, --envio_mesaje_a_conversacion(conversation_id,user_msg)
#- Se agrega variables globales CACHED_ACCESS_TOKEN, TOKEN_EXPIRATION_TIME para consultar access_token y solo crear cuando sea necesario
#- Se agrega JSONDecodeError, debido a que habia respuestas que llegaban a zoho, y devolvian a la 
#api un valor vacio que la Api persivia como un error, se agrega para hacer una excepcion y que continue el flujo 
#- Se configura Flujo de Trabajo en Zoho Sales IQ, para configurar el webhook desde Zoho
#- Se crea funcion from_zoho(): que realiza la captura del webhook y se envia a la App A
#- Se buscar Visitante si no existe crea visisitante y con el fin de poder visitor_id, y 
#con este ultimo buscar una nueva conversación, si no existe crearla.
