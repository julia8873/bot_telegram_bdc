"""
Capa de abstracción del LLM

Actúa como capa intermedia entre el bot y el LLM
El bot solo llama a generate(), no sabe qué LLM hay

Para añadir un proveedor nuevo: Añadir un bloque en 'generate()'
Si tiene API del tipo OpenAI, probablemente sirva "openai_compatible" sin tener que escribir más código
"""

import logging  # Para registrar los reintentos y errores
import os       # Para leer las variables de entorno (.env).
import time     # Para esperar entre reintentos
import requests # Para hacer peticiones HTTP a las APIs de los LLMs.

logger = logging.getLogger("llm-client")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()           # Proveedor
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1") # URL base de la API.
LLM_API_KEY  = os.getenv("LLM_API_KEY", "")                           # Clave de autenticación.
LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-4o-mini")                  # Modelo concreto a usar.

# Configuración de reintentos para errores transitorios del servidor (503, 429...)
MAX_REINTENTOS  = 3  # número máximo de intentos antes de rendirse
ESPERA_INICIAL  = 2  # segundos de espera antes del primer reintento


def generate(prompt: str, system_prompt: str = "") -> str:
    """Devuelve la respuesta del LLM configurado para un prompt dado."""

    if LLM_PROVIDER in ("openai", "ugr", "openai_compatible"):
        # los tres proveedores comparten el mismo formato de API
        return _generate_openai_compatible(prompt, system_prompt)

    elif LLM_PROVIDER == "anthropic":
        # Anthropic tiene su propia API con formato distinto
        return _generate_anthropic(prompt, system_prompt)

    else:
        # Si el proveedor configurado no está implementado
        raise ValueError(f"LLM_PROVIDER desconocido: {LLM_PROVIDER}")


def _generate_openai_compatible(prompt: str, system_prompt: str) -> str:
    """
    Sirve para OpenAI y para la mayoría de LLMs auto-hospedados/UGR,
    que suelen exponer un endpoint compatible con /v1/chat/completions.
    Incluye reintentos automáticos para errores transitorios (503, 429).
    """

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"  # rstrip evita doble barra si la URL termina en /

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",  # autenticación estándar de OpenAI
        "Content-Type": "application/json",          # el body de la petición es JSON
    }

    messages = []  # lista de mensajes de la conversación con el LLM

    if system_prompt:  # solo añadir si no está vacío
        messages.append({"role": "system", "content": system_prompt})  # instrucciones al LLM

    messages.append({"role": "user", "content": prompt})  # pregunta del usuario

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0,  # 0 -> respuestas deterministas, sin creatividad
    }

    espera = ESPERA_INICIAL  # segundos de espera, se duplica en cada reintento

    for intento in range(MAX_REINTENTOS):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)  # petición POST al LLM

        if resp.status_code in (503, 429) and intento < MAX_REINTENTOS - 1:
            # 503 -> servidor no disponible (transitorio), 429 -> demasiadas peticiones (rate limit)
            # si aún quedan reintentos, esperamos y volvemos a intentarlo
            logger.warning(
                f"Error {resp.status_code} de la API. "
                f"Reintentando en {espera}s... (intento {intento + 1}/{MAX_REINTENTOS})"
            )
            time.sleep(espera)  # esperar antes del siguiente intento
            espera *= 2         # esperamos: 2s -> 4s -> 8s
            continue            # volver al inicio del bucle

        resp.raise_for_status()  # si la API devuelve otro error HTTP, lanza excepción
        data = resp.json()       # parsear respuesta JSON a diccionario Python
        return data["choices"][0]["message"]["content"].strip()  # extraer el texto de respuesta


def _generate_anthropic(prompt: str, system_prompt: str) -> str:
    """
    Adaptada para el formato de la API de Anthropic.
    Incluye reintentos automáticos para errores transitorios (503, 429).
    """

    url = f"{LLM_BASE_URL.rstrip('/')}/messages"  # Anthropic usa /messages en vez de /chat/completions

    headers = {
        "x-api-key": LLM_API_KEY,           # Anthropic usa x-api-key en vez de Authorization: Bearer
        "anthropic-version": "2023-06-01",   # versión de la API requerida por Anthropic
        "Content-Type": "application/json",
    }

    payload = {
        "model": LLM_MODEL,
        "max_tokens": 1000,            # límite de tokens en la respuesta (obligatorio en Anthropic)
        "system": system_prompt or "", # system prompt en campo propio, no dentro de messages
        "messages": [{"role": "user", "content": prompt}],
    }

    espera = ESPERA_INICIAL  # segundos de espera, se duplica en cada reintento

    for intento in range(MAX_REINTENTOS):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)  # petición POST a Anthropic

        if resp.status_code in (503, 429) and intento < MAX_REINTENTOS - 1:
            # mecanismo de reintento
            logger.warning(
                f"Error {resp.status_code} de la API. "
                f"Reintentando en {espera}s... (intento {intento + 1}/{MAX_REINTENTOS})"
            )
            time.sleep(espera)
            espera *= 2
            continue

        resp.raise_for_status()  # lanza excepción si la API devuelve error HTTP
        data = resp.json()       # parsear respuesta JSON a diccionario Python

        return "".join(
            block["text"]                    # extraer texto de cada bloque
            for block in data["content"]     # Anthropic puede devolver varios bloques
            if block["type"] == "text"       # ignorar bloques que no sean texto (ej. imágenes)
        ).strip()