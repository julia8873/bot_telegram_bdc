"""
Capa de abstracción del LLM

Actúa como capa intermedia entre el bot y el LLM
El bot solo llama a generate(), no sabe qué LLM hay

Para añadir un proveedor nuevo: Añadir un bloque en 'generate()'
Si tiene API del tipo OpenAO, probablemente sirva "openai_compatible" sin tener que escribir más código

"""


import os        # Para leer las variables de entorno (.env).
import requests  # Para hacer peticiones HTTP a las APIs de los LLMs.


LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()          # Proveedor
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1") # URL base de la API. Se puede sobreescribir para apuntar a un LLM propio.
LLM_API_KEY  = os.getenv("LLM_API_KEY", "")                           # Clave de autenticación de la API. Vacía por defecto (obligatoria en producción).
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")                  # Modelo concreto a usar. (por defecto: gpt-4o-mini)

# Devolver la respuesta del LLM para dicho prompt
def generate(prompt: str, system_prompt: str = "") -> str:
    """Devuelve la respuesta del LLM configurado para un prompt dado."""

    if LLM_PROVIDER in ("openai", "ugr", "openai_compatible"):
        # los tres proveedores comparten el mismo formato de API, se aplica la misma función
        # prompt - la entrada del usuario
        # system_promp - las instrucciones que tiene que seguir el LLM (eg: eres un asistente que...)
        return _generate_openai_compatible(prompt, system_prompt)
    
    elif LLM_PROVIDER == "anthropic":
        # Anthropic tiene su propia API con formato distinto
        return _generate_anthropic(prompt, system_prompt)

    else:
        # Si el proveedor configurado no está implementado
        raise ValueError(f"LLM_PROVIDER desconocido: {LLM_PROVIDER}")
    

# función privada
# Sirve para OpenAI y cualquier LLM que tenga el formato de API (/v1/chat/completions).

def _generate_openai_compatible(prompt: str, system_prompt: str) -> str:
    """
    Sirve para OpenAI y para la mayoría de LLMs auto-hospedados/UGR,
    que suelen exponer un endpoint compatible con /v1/chat/completions.
    """

    # completions -> dado texto de entrada, genera la continuación

    url = f"{LLM_BASE_URL}/chat/completions" # contruir URL del endpoint con la ruta estándar de openAI

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}", # Autenticación: Token en cabecera HTTP
        "Content-Type": "application/json",  #indicar que el body de la petición es JSON
    }

    messages = [] # Lista de mensajes de la conversación con el LLM

    if system_prompt: # si se ha introducido un system prompt
        messages.append({"role": "system", "content": system_prompt}) # instrucciones al LLM

    messages.append({"role": "user", "content": prompt}) # Pregunta del usuario

    payload = {
        "model": LLM_MODEL,
        "messages": messages,  # historial de la conversación
        "temperature": 0       # temperatura 0 -> respuestas deterministas y precisas. 
                                # solo se usa el conocimiento que le hemos pasado
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60) # enviar petición POST al LLM
    resp.raise_for_status() # Si la API devuelve error HTTP, lanza excepción

    data = resp.json()  # parsear respuesta JSON del LLM a un diccionario Python

    return data["choices"][0]["message"]["content"].strip() # extraer el texto de respuesta



# función privada de anthropic
# adaptada para el formato de la API de Anthropic
def _generate_anthropic(prompt: str, system_prompt: str) -> str:

    url = f"{LLM_BASE_URL}/messages"  # Endpoint de Anthropic: /messages en lugar de /chat/completions.

    headers = {
        "x-api-key": LLM_API_KEY,           # Anthropic usa "x-api-key" en lugar de "Authorization: Bearer".
        "anthropic-version": "2023-06-01",   # Versión de la API requerida por Anthropic en cada petición.
        "Content-Type": "application/json",  # El cuerpo de la petición es JSON.
    }

    payload = {
        "model": LLM_MODEL,
        "max_tokens": 1000,   # Límite de tokens en la respuesta
        "system": system_prompt or "",                         
        "messages": [{"role": "user", "content": prompt}], 
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)  # Petición POST a la API de Anthropic
    resp.raise_for_status()  # Lanza excepción si la API devuelve error HTTP.

    data = resp.json()  # parsea la respuesta JSON a diccionario Python.

    return "".join(
        block["text"]             # Extrae el texto de cada bloque.
        for block in data["content"]  # Itera sobre los bloques de contenido de la respuesta.
        if block["type"] == "text"    # Filtra solo los bloques de tipo texto (podría haber otros tipos, como imágenes).
    ).strip()



