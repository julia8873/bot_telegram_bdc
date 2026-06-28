"""
Bot de Telegram que usa una BdC y un LLM configurados en .env
Pensado para ejecutarse de fondo continuamente.

Construye el prompt: pregunta + fragmentos de la BdC + instrucción de responder solo a esa información
Y devuelve la respuesta.

"""

import logging     # Para registrar eventos y errores del bot
import os         # Leer variables de entorno y rutas del sistema
import threading  # Crear hilos para ejecutar en paralelo
import time       # Para funciones de pausa (sleep) entre sincronizaciones

from dotenv import load_dotenv  # Carga variables desde el archivo .env al entorno.
from telegram import Update     # Representa una actualización (mensaje, etc.) recibida de Telegram.
from telegram.ext import (
    Application,          # Clase principal que gestiona el ciclo de vida del bot.
    CommandHandler,       # Para comandos tipo /start, /help, etc.
    MessageHandler,       # Para mensajes normales de texto.
    ContextTypes,         # Contexto de cada handler.
    filters,              # Filtros para seleccionar qué mensajes procesa cada handler.
)

import llm_client  # Encapsular las llamadas al LLM
import retrieval   # Buscar en la BDC

load_dotenv()  # leer el archivo .env y cargarlo como variables de entorno

# configurar el formato y los mensajes de log
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", # Formato: fecha, nombre, nivel, mensaje
    level=logging.INFO, # Solo mostrar mensajes de mivel INFO o superior
)
logger = logging.getLogger("bot-telegram-bdc")  # crear logger con ese nombre


# Variables leídas del .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BDC_REPO_URL = os.getenv("BDC_REPO_URL","").strip() # url al repo git de la BDC. Si está vacío, no se define
BDC_PULL_INTERVAL_SECONDS = int(os.getenv("BDC_PULL_INTERVAL_SECONDS", "300")) # intervalo de sincronización con la BDC (por defecto: cada 5 mins)


# Prompt para el comportamiento del LLM
SYSTEM_PROMPT = (
    "Eres un asistente que responde preguntas únicamente a partir del "
    "contexto proporcionado, extraído de la Base de Conocimiento. "   # Restringe las respuestas al contexto dado.
    "Si la respuesta no está en el contexto, dilo explícitamente en "  # Evita que el LLM invente información.
    "lugar de inventar información."
)


# Sincronizar la BDC
def sync_bdc_once():
    """Clona la BdC si no existe, o actualiza con git pull si ya existe"""
    if not BDC_REPO_URL:
        return
    
    import git # importamos GitPython que solo se usará en esta función

    bdc_path = os.getenv("BDC_PATH", "./bdc")  # ruta local donde se guardará la bdc. (por defecto: "./bdc")
    try:
        if os.path.exists(os.path.join(bdc_path, ".git")): # comprobar si ya estaba clonado el git de antes
            git.Repo(bdc_path).remotes.origin.pull()  # hacemos un git pull
            logger.info("BdC actualizada correctamente mediante git pull.")
        else:
            git.Repo.clone_from(BDC_REPO_URL, bdc_path) # sino, clonar desde 0
            logger.info("BdC clonada por primera vez en %s.", bdc_path)
    except Exception as e:
        logger.warning(f"No se ha podido sincronizar la BdC: {e}")


# Sincronizar BdC cada N segundos
def pull_bdc_loop():
    """Hilo en background: repite sync_bdc_once() cada N segundos"""

    if not BDC_REPO_URL:
        return
    
    while True:
        time.sleep(BDC_PULL_INTERVAL_SECONDS)
        sync_bdc_once()


# Handler para procesar cada mensaje recibido por el bot
# async para poder usar await para que no se bloquee el bot mientras se espera para la confirmación HTTP de Telegram
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para procesar cada mensaje recibido por el bot"""
    question = update.message.text # texto enviado por el usuario a telegram
    logger.info(f"Pregunta recibida: {question!r}")  # !r para ver caracteres especiales

    context_text = retrieval.get_relevant_context(question) # buscar fragmentos relevantes en la BdC

    # Crear prompt para pasárselo al LLM

    if context_text: # ha encontrado texto relevante
        prompt = (
            f"Contexto de la Base de Conocimiento:\n{context_text}\n\n"  # Inyecta el contexto recuperado.
            f"Pregunta del usuario: {question}\n\n"                       # Añade la pregunta original.
            f"Responde solo con información del contexto anterior."        # Instrucción explícita al LLM.
        )
    else:
        prompt = (
            f"Pregunta del usuario: {question}\n\n"
            f"No se ha encontrado contexto relevante en la Base de Conocimiento. "  # Informa al LLM de la ausencia.
            f"Indica que no tienes información suficiente para responder con certeza."  # Que indique que no se ha encontrado contexto relevante
        )

    try:
        answer = llm_client.generate(prompt, system_prompt=SYSTEM_PROMPT)
    except Exception as e:
        logger.exception("Error al llamar al LLM")
        answer = "Se ha producido un error al generar la respuesta. Inténtalo de nuevo."
    
    # await: cede el control mientras Telegram confirma el envío (HTTP)
    await update.message.reply_text(answer)

# Handler para el comando /start.
# Lo que se muestra cuando usuario hace /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para el comando /start"""
    await update.message.reply_text(
        "Hola, soy tu asistente de la BdC. Pregúntame lo que necesites"
    )

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta el TELEGRAM_BOT_TOKEN en el fichero .env")
    
    # 1. Sincronizar la BdC
    sync_bdc_once()

    # 2. lanzar hilo daemon para mantener la BdC constantemente actualizada
    if BDC_REPO_URL:
        threading.Thread(target=pull_bdc_loop, daemon=True).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()  # Crear la aplicación del bot con el token
    application.add_handler(CommandHandler("start", start_command)) # Registrar el handler de /start
    # Para las preguntas de usuario, las mandamos a la función handle_message
    # Filtramos lo que no sea texto (eg quitamos [Audio], [Video]) y tampoco consideramos los comandos como "/start"
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)) # Registra el handler para cualquier mensaje de texto que no sea un comando

    logger.info("Bot iniciado. Esperando mensajes (polling)...")
    application.run_polling()  # consultar Telegram periódicamente para nuevos mensajes

if __name__ == "__main__":
    main()  # ejecutar main solo si el script se lanza (no si se importa)