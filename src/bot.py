"""
Bot de Telegram que usa una BdC y un LLM configurados en .env
Pensado para ejecutarse de fondo continuamente.

Construye el prompt: pregunta + fragmentos de la BdC + instrucción de responder solo a esa información
Y devuelve la respuesta.

Comandos disponibles:
  /start      -> mensaje de bienvenida
  /help       -> panel con todos los comandos disponibles
  /actualizar -> fuerza un git pull de la BdC inmediatamente
  /ficheros   -> lista todos los archivos .md cargados en la BdC
  /contenido  -> muestra el contenido completo de un archivo de la BdC
  /debug      -> muestra la última pregunta y el contexto enviado al LLM
  /error      -> muestra el detalle técnico del último error producido
"""

import asyncio     # Para ejecutar el LLM en un hilo y no bloquear el bot, y para el aviso de "escribiendo..."
import logging     # Para registrar eventos y errores del bot
import os         # Leer variables de entorno y rutas del sistema
import threading  # Crear hilos para ejecutar en paralelo
import time       # Para funciones de pausa (sleep) entre sincronizaciones
import traceback  # Para capturar el detalle completo de las excepciones (comando /error)
from datetime import datetime  # Para poner fecha/hora a los errores registrados

from dotenv import load_dotenv  # Carga variables desde el archivo .env al entorno.

# Cargamos antes, sino puede que tenga valores antiguos
load_dotenv(override=True)  # leer el archivo .env y cargarlo como variables de entorno

from telegram import Update, LinkPreviewOptions  # Update: actualización recibida. LinkPreviewOptions: para desactivar las "tarjetas" de vista previa de enlaces.
from telegram.constants import ChatAction  # Para mostrar el aviso de "escribiendo..." en Telegram
from telegram.request import HTTPXRequest  # Para poder ajustar los timeouts de conexión a Telegram
from telegram.ext import (
    Application,          # Clase principal que gestiona el ciclo de vida del bot.
    CommandHandler,       # Para comandos tipo /start, /help, etc.
    MessageHandler,       # Para mensajes normales de texto.
    ContextTypes,         # Contexto de cada handler.
    Defaults,              # Para fijar opciones por defecto (aquí: desactivar vista previa de enlaces) en todos los envíos
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

# Almacena el último error producido al llamar al LLM, para el comando /error
_ultimo_error: dict = {"momento": None, "pregunta": None, "detalle": None}


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


async def _aviso_escribiendo_en_bucle(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Repite el aviso de 'escribiendo...' de Telegram cada 4 segundos.
    Telegram solo muestra esta animación ~5s, así que hay que renovarla
    mientras dure la espera al LLM. Se cancela en cuanto el LLM responde.
    """
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass  # esperado: se cancela en cuanto llega la respuesta del LLM


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

    mensaje_espera = None  # puede quedarse en None si Telegram no responde al enviarlo
    aviso_task = None

    try:
        # Mensaje editable de "cargando" + aviso nativo de "escribiendo..." en bucle,
        # para que el usuario no se quede esperando sin saber si el bot sigue vivo
        mensaje_espera = await update.message.reply_text("⏳ Generando respuesta...")
        aviso_task = asyncio.create_task(
            _aviso_escribiendo_en_bucle(update.effective_chat.id, context)
        )

        # to_thread: la llamada al LLM es bloqueante (usa requests), así que la
        # lanzamos en un hilo aparte para no congelar el bot mientras se espera
        answer = await asyncio.to_thread(llm_client.generate, prompt, SYSTEM_PROMPT)

    except Exception:
        logger.exception("Error al llamar al LLM")
        # Guardamos el detalle completo para que /error pueda mostrarlo
        _ultimo_error["momento"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        _ultimo_error["pregunta"] = question
        _ultimo_error["detalle"] = traceback.format_exc()
        answer = (
            "⚠️ Se ha producido un error al generar la respuesta. "
            "Usa /error para ver el detalle técnico."
        )
    finally:
        if aviso_task is not None:
            aviso_task.cancel()  # ya tenemos respuesta (o error): paramos el aviso

    # Enviar la respuesta final. Separado en su propio try/except: si Telegram
    # falló al crear "mensaje_espera" (p. ej. timeout de red), no editamos nada,
    # mandamos un mensaje nuevo. Si esto también falla, no hay más que podamos
    # hacer desde aquí (es un problema de conectividad con Telegram, no del bot).
    MAX_LEN = 4096
    try:
        if mensaje_espera is not None:
            await mensaje_espera.edit_text(answer[:MAX_LEN])
            for i in range(MAX_LEN, len(answer), MAX_LEN):  # el resto (si lo hay), en mensajes nuevos
                await update.message.reply_text(answer[i:i + MAX_LEN])
        else:
            for i in range(0, len(answer), MAX_LEN):
                await update.message.reply_text(answer[i:i + MAX_LEN])
    except Exception:
        logger.exception("No se pudo enviar la respuesta al chat de Telegram")
        _ultimo_error["momento"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        _ultimo_error["pregunta"] = question
        _ultimo_error["detalle"] = traceback.format_exc()


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Red de seguridad: captura cualquier excepción no controlada en cualquier
    handler (no solo handle_message), para que quede registrada en /error
    en vez de perderse silenciosamente en los logs.
    """
    logger.error("Excepción no controlada", exc_info=context.error)
    _ultimo_error["momento"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _ultimo_error["pregunta"] = "(error fuera de una pregunta normal, ver /error)"
    _ultimo_error["detalle"] = "".join(
        traceback.format_exception(type(context.error), context.error, context.error.__traceback__)
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para el comando /start"""
    await update.message.reply_text(
        "Hola, soy tu asistente de la BdC. Pregúntame lo que necesites.\n\n"
        "Si necesitas ver el panel con todos los comandos disponibles, escribe /help."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para el comando /help: panel con todos los comandos disponibles"""
    await update.message.reply_text(
        "Comandos disponibles:\n\n"
        "/actualizar -> actualiza el contenido de la BdC desde el repositorio\n"
        "/ficheros   -> muestra los archivos cargados en la BdC\n"
        "/contenido <archivo> -> muestra el contenido de un archivo de la BdC\n"
        "/debug      -> muestra la última pregunta y el contexto enviado al LLM\n"
        "/error      -> muestra el detalle técnico del último error producido"
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

    lista = "\n".join(f"• <code>{ruta}</code>" for ruta, _ in sorted(files))
    
    respuesta = (
        f"Archivos cargados en la BdC ({len(files)}):\n\n"
        f"{lista}\n\n"
        f"Usa /contenido &lt;nombre&gt; para ver el contenido de uno."
    )
    
    # Se especifica parse_mode="HTML" al enviar el mensaje
    await update.message.reply_text(respuesta, parse_mode="HTML")

async def contenido_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el contenido completo de un archivo de la BdC, buscándolo por nombre (o parte del nombre)"""
    if not context.args:  # context.args = palabras después del comando, ej. /contenido T1.md -> ["T1.md"]
        await update.message.reply_text(
            "Uso: /contenido <nombre_archivo>\n"
            "Ejemplo: /contenido T1.md\n\n"
            "Usa /ficheros para ver los nombres exactos disponibles."
        )
        return

    busqueda = " ".join(context.args).lower()  # admite nombres con espacios
    files = retrieval._load_bdc_files()  # [(ruta_relativa, contenido_sin_frontmatter), ...]

    # Coincidencia por nombre de archivo o por ruta completa (no sensible a mayúsculas)
    encontrados = [(ruta, contenido) for ruta, contenido in files if busqueda in ruta.lower()]

    if not encontrados:
        await update.message.reply_text(
            f"No se ha encontrado ningún archivo que coincida con '{busqueda}'. "
            f"Usa /ficheros para ver la lista exacta."
        )
        return

    if len(encontrados) > 1:
        # demasiadas coincidencias -> pedimos que sea más concreto, mostrando cuáles chocan
        lista = "\n".join(f"• {ruta}" for ruta, _ in encontrados)
        await update.message.reply_text(
            f"Hay varios archivos que coinciden con '{busqueda}', sé más concreto:\n{lista}"
        )
        return

    ruta, contenido = encontrados[0]
    texto = f"📄 {ruta}\n\n{contenido.strip()}"

    # Telegram tiene límite de 4096 caracteres por mensaje: partimos en trozos si hace falta
    MAX_LEN = 4096
    for i in range(0, len(texto), MAX_LEN):
        await update.message.reply_text(texto[i:i + MAX_LEN])


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


async def error_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el detalle técnico (traceback) del último error producido, si lo hay"""
    if _ultimo_error["detalle"] is None:  # no se ha producido ningún error todavía
        await update.message.reply_text("No se ha registrado ningún error todavía. ✅")
        return

    cabecera = (
        f"*Último error* ({_ultimo_error['momento']})\n"
        f"*Pregunta:* {_ultimo_error['pregunta']}\n\n"
    )
    # Markdown con ``` para que el traceback se vea con formato de código monoespaciado
    cuerpo = f"```\n{_ultimo_error['detalle']}\n```"

    # Telegram tiene límite de 4096 caracteres; recortamos el traceback si hace falta
    # (nos quedamos con el final, que suele tener la línea del error real)
    MAX_LEN = 4096
    espacio_disponible = MAX_LEN - len(cabecera) - len("```\n\n```")
    if len(_ultimo_error["detalle"]) > espacio_disponible:
        cuerpo = f"```\n...(recortado)...\n{_ultimo_error['detalle'][-espacio_disponible:]}\n```"

    await update.message.reply_text(cabecera + cuerpo, parse_mode="Markdown")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta el TELEGRAM_BOT_TOKEN en el fichero .env")
    
    # 1. Sincronizar la BdC
    sync_bdc_once()

    # 2. lanzar hilo daemon para mantener la BdC constantemente actualizada
    if BDC_REPO_URL:
        threading.Thread(target=pull_bdc_loop, daemon=True).start()

    # Timeouts por defecto de la librería son cortos (~5s); si la conexión hacia
    # Telegram es lenta (p. ej. por pasar a través de la VPN de la UGR) pero no
    # está bloqueada, esto evita falsos "TimedOut" por pura lentitud de red.
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )
    # Desactiva la "tarjeta" de vista previa que Telegram añade automáticamente
    # cuando un mensaje contiene una URL (puede mostrar contenido de una web
    # totalmente ajena a la respuesta, ej. enlaces dentro de los .md de la BdC)
    defaults = Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .defaults(defaults)
        .build()
    )  # Crear la aplicación del bot con el token, los timeouts ajustados y la vista previa desactivada

    # Registrar handlers de comandos
    application.add_handler(CommandHandler("start",      start_command))
    application.add_handler(CommandHandler("help",       help_command))
    application.add_handler(CommandHandler("actualizar", actualizar_command))
    application.add_handler(CommandHandler("ficheros",   ficheros_command))
    application.add_handler(CommandHandler("contenido",  contenido_command))
    application.add_handler(CommandHandler("debug",      debug_command))
    application.add_handler(CommandHandler("error",      error_command))

    # Para las preguntas de usuario — excluimos comandos y mensajes no textuales
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Red de seguridad: cualquier excepción no controlada en cualquier handler
    # queda registrada para /error en vez de perderse en los logs
    application.add_error_handler(global_error_handler)

    logger.info("Bot iniciado. Esperando mensajes (polling)...")
    application.run_polling()  # consultar Telegram periódicamente para nuevos mensajes

if __name__ == "__main__":
    main()  # ejecutar main solo si el script se lanza (no si se importa)