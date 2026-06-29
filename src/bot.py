"""
Bot de Telegram que usa una BdC y un LLM configurados en .env
Pensado para ejecutarse de fondo continuamente.

Construye el prompt: pregunta + fragmentos de la BdC + instrucción de responder solo a esa información
Y devuelve la respuesta.

Comandos disponibles:
  /start      -> mensaje de bienvenida
  /actualizar -> fuerza un git pull de la BdC inmediatamente
  /ficheros   -> lista todos los archivos .md cargados en la BdC
  /debug      -> muestra la última pregunta y el contexto enviado al LLM
"""

import logging     # Para registrar eventos y errores del bot
import os         # Leer variables de entorno y rutas del sistema
import threading  # Crear hilos para ejecutar en paralelo
import time       # Para funciones de pausa (sleep) entre sincronizaciones

from dotenv import load_dotenv  # Carga variables desde el archivo .env al entorno.

# Cargamos antes, sino puede que tenga valores antiguos
load_dotenv(override=True)  # leer el archivo .env y cargarlo como variables de entorno

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


# configurar el formato y los mensajes de log
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", # Formato: fecha, nombre, nivel, mensaje
    level=logging.INFO, # Solo mostrar mensajes de nivel INFO o superior
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
    "lugar de inventar información en una frase."
)

# Almacena la última pregunta y contexto para el comando /debug
# Se actualiza en cada handle_message
_ultimo_debug: dict = {"pregunta": None, "contexto": None}


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

    # Guardar pregunta y contexto para /debug
    _ultimo_debug["pregunta"] = question
    _ultimo_debug["contexto"] = context_text if context_text else "(sin contexto relevante)"

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
    
    # Telegram tiene un límite de 4096 caracteres por mensaje
    MAX_LEN = 4096
    if len(answer) <= MAX_LEN:
        await update.message.reply_text(answer)  # await: cede el control mientras Telegram confirma el envío (HTTP)
    else:
        for i in range(0, len(answer), MAX_LEN):  # partir la respuesta en trozos de 4096 y enviar uno a uno
            await update.message.reply_text(answer[i:i + MAX_LEN])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para el comando /start"""
    await update.message.reply_text(
        "Hola, soy tu asistente de la BdC. Pregúntame lo que necesites.\n\n"
        "Comandos disponibles:\n"
        "/actualizar -> actualiza el contenido de la BdC desde el repositorio\n"
        "/ficheros   -> muestra los archivos cargados en la BdC\n"
        "/debug      -> muestra la última pregunta y el contexto enviado al LLM"
    )

# -------------------------------------------------------------------------------------------------------
#    Comandos
# -------------------------------------------------------------------------------------------------------

async def actualizar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fuerza un git pull de la BdC inmediatamente sin esperar al intervalo automático"""
    if not BDC_REPO_URL:
        await update.message.reply_text("No hay repositorio configurado en BDC_REPO_URL.")
        return

    await update.message.reply_text("Actualizando la BdC...")  # avisa antes de empezar, puede tardar
    try:
        sync_bdc_once()
        await update.message.reply_text("BdC actualizada correctamente.")
    except Exception as e:
        logger.exception("Error al actualizar la BdC desde /actualizar")
        await update.message.reply_text(f"Error al actualizar la BdC: {e}")


async def ficheros_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos los archivos .md actualmente cargados en la BdC"""
    files = retrieval._load_bdc_files()  # devuelve [(ruta_relativa, contenido), ...]

    if not files:
        await update.message.reply_text("No hay archivos cargados en la BdC.")
        return

    # Construir la lista con solo las rutas, sin el contenido
    lista = "\n".join(f"• {ruta}" for ruta, _ in sorted(files))
    respuesta = f"Archivos cargados en la BdC ({len(files)}):\n\n{lista}"
    await update.message.reply_text(respuesta)


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la última pregunta recibida y el contexto que se envió al LLM"""
    if _ultimo_debug["pregunta"] is None:  # todavía no se ha procesado ningún mensaje
        await update.message.reply_text("Aún no se ha procesado ninguna pregunta.")
        return

    respuesta = (
        f"*Última pregunta:*\n{_ultimo_debug['pregunta']}\n\n"
        f"*Contexto enviado al LLM:*\n{_ultimo_debug['contexto']}"
    )

    # Telegram tiene límite de 4096 caracteres
    MAX_LEN = 4096
    await update.message.reply_text(respuesta[:MAX_LEN], parse_mode="Markdown")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta el TELEGRAM_BOT_TOKEN en el fichero .env")
    
    # 1. Sincronizar la BdC
    sync_bdc_once()

    # 2. lanzar hilo daemon para mantener la BdC constantemente actualizada
    if BDC_REPO_URL:
        threading.Thread(target=pull_bdc_loop, daemon=True).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()  # Crear la aplicación del bot con el token

    # Registrar handlers de comandos
    application.add_handler(CommandHandler("start",      start_command))
    application.add_handler(CommandHandler("actualizar", actualizar_command))
    application.add_handler(CommandHandler("ficheros",   ficheros_command))
    application.add_handler(CommandHandler("debug",      debug_command))

    # Para las preguntas de usuario — excluimos comandos y mensajes no textuales
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot iniciado. Esperando mensajes (polling)...")
    application.run_polling()  # consultar Telegram periódicamente para nuevos mensajes

if __name__ == "__main__":
    main()  # ejecutar main solo si el script se lanza (no si se importa)