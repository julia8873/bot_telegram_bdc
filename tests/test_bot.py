"""
Tests de bot.py

Se mockea todo lo externo: Telegram, llm_client, retrieval y git.
Así los tests no necesitan token, API key ni conexión a internet.
"""

import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --------------------------------------------------------------
#  sync_bdc_once
# --------------------------------------------------------------
class TestSyncBdcOnce:

    # Verifica que sin BDC_REPO_URL configurada la función termina sin errores ni llamadas a git
    def test_sin_url_no_hace_nada(self):
        with patch.dict("os.environ", {"BDC_REPO_URL": ""}):
            import importlib, bot
            importlib.reload(bot)
            bot.sync_bdc_once()

    # Verifica que si la carpeta bdc no existe se llama a git clone para crearla
    def test_clona_si_no_existe(self, tmp_path):
        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
            "BDC_PATH": str(tmp_path / "bdc")}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            with patch("os.path.exists", return_value=False), \
                patch("git.Repo") as mock_git:
                bot.sync_bdc_once()
                mock_git.clone_from.assert_called_once()

    # Verifica que si la carpeta bdc ya existe se llama a git pull en vez de clone
    def test_hace_pull_si_ya_existe(self, tmp_path):
        bdc = tmp_path / "bdc"
        bdc.mkdir()
        (bdc / ".git").mkdir()

        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
               "BDC_PATH": str(bdc)}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            mock_repo = MagicMock()
            with patch("git.Repo", return_value=mock_repo):
                bot.sync_bdc_once()
                mock_repo.remotes.origin.pull.assert_called_once()

    # Verifica que un fallo de git no detiene el bot sino que se captura y se registra como warning
    def test_error_git_no_detiene_el_bot(self, tmp_path):
        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
               "BDC_PATH": str(tmp_path / "bdc")}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            with patch("git.Repo.clone_from", side_effect=Exception("sin red")):
                bot.sync_bdc_once()


# --------------------------------------------------------------
#  handle_message
# --------------------------------------------------------------

# Crea un objeto Update falso con el texto dado para no depender de Telegram
def _make_update(texto: str) -> MagicMock:
    update = MagicMock()
    update.message.text = texto
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.asyncio
class TestHandleMessage:

    # Verifica que el bot envía a Telegram la respuesta generada por el LLM cuando hay contexto
    async def test_responde_con_contexto_encontrado(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Qué es una EDO de primer orden?")
        contexto = "### Fuente: edo-primer-orden.md\nUna EDO de primer orden relaciona una función con su derivada."

        with patch("retrieval.get_relevant_context", return_value=contexto), \
             patch("llm_client.generate", return_value="Una EDO de primer orden relaciona una función con su derivada dy/dx = f(x,y)."):
            await bot.handle_message(update, MagicMock())

        update.message.reply_text.assert_called_once_with(
            "Una EDO de primer orden relaciona una función con su derivada dy/dx = f(x,y)."
        )

    # Verifica que el bot responde aunque no haya contexto relevante en la BdC
    async def test_responde_sin_contexto(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Cuántos créditos tiene la asignatura?")

        with patch("retrieval.get_relevant_context", return_value=""), \
             patch("llm_client.generate", return_value="No tengo información suficiente."):
            await bot.handle_message(update, MagicMock())

        update.message.reply_text.assert_called_once()

    # Verifica que el contexto recuperado de la BdC se inyecta dentro del prompt que recibe el LLM
    async def test_prompt_incluye_contexto_cuando_existe(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Qué es el método de Euler?")
        contexto = "El método de Euler aproxima soluciones de EDOs avanzando en pasos h."

        with patch("retrieval.get_relevant_context", return_value=contexto), \
             patch("llm_client.generate", return_value="Euler avanza en pasos h.") as mock_gen:
            await bot.handle_message(update, MagicMock())
            prompt_usado = mock_gen.call_args.args[0]

        assert contexto in prompt_usado

    # Verifica que cuando no hay contexto el prompt avisa al LLM explícitamente de ello
    async def test_prompt_sin_contexto_avisa_al_llm(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Cuál es la nota media de la asignatura?")

        with patch("retrieval.get_relevant_context", return_value=""), \
             patch("llm_client.generate", return_value="Sin info.") as mock_gen:
            await bot.handle_message(update, MagicMock())
            prompt_usado = mock_gen.call_args.args[0]

        assert "no se ha encontrado" in prompt_usado.lower()

    # Verifica que si el LLM falla el bot responde con mensaje de error en vez de lanzar excepción
    async def test_error_en_llm_devuelve_mensaje_de_error(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Qué es el wronskiano?")

        with patch("retrieval.get_relevant_context", return_value="El wronskiano determina independencia lineal."), \
             patch("llm_client.generate", side_effect=Exception("timeout")):
            await bot.handle_message(update, MagicMock())

        update.message.reply_text.assert_called_once()
        respuesta = update.message.reply_text.call_args.args[0]
        assert "error" in respuesta.lower()

    # Verifica que el SYSTEM_PROMPT definido en bot.py llega como argumento a llm_client.generate
    async def test_system_prompt_se_pasa_al_llm(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("¿Qué es una EDO separable?")

        with patch("retrieval.get_relevant_context", return_value="Una EDO separable se puede escribir como g(y)dy = f(x)dx."), \
             patch("llm_client.generate", return_value="ok") as mock_gen:
            await bot.handle_message(update, MagicMock())
            kwargs = mock_gen.call_args.kwargs

        assert "system_prompt" in kwargs
        assert len(kwargs["system_prompt"]) > 0


# --------------------------------------------------------------
#  start_command
# --------------------------------------------------------------
@pytest.mark.asyncio
class TestStartCommand:

    # Verifica que el comando /start devuelve un mensaje de bienvenida no vacío
    async def test_responde_con_bienvenida(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("/start")
        await bot.start_command(update, MagicMock())

        update.message.reply_text.assert_called_once()
        respuesta = update.message.reply_text.call_args.args[0]
        assert len(respuesta) > 0


# --------------------------------------------------------------
#  actualizar_command
# --------------------------------------------------------------
@pytest.mark.asyncio
class TestActualizarCommand:
 
    # Verifica que sin BDC_REPO_URL configurada el comando avisa de que no hay repo
    async def test_sin_url_avisa_al_usuario(self):
        import importlib, bot
        importlib.reload(bot)
        bot.BDC_REPO_URL = ""
 
        update = _make_update("/actualizar")
        await bot.actualizar_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "no hay" in respuesta.lower() or "repositorio" in respuesta.lower()
 
    # Verifica que con repo configurado llama a sync_bdc_once y confirma éxito al usuario
    async def test_con_url_llama_a_sync_y_confirma(self):
        import importlib, bot
        importlib.reload(bot)
        bot.BDC_REPO_URL = "https://github.com/ejemplo/repo.git"
 
        update = _make_update("/actualizar")
        with patch("bot.sync_bdc_once") as mock_sync:
            await bot.actualizar_command(update, MagicMock())
            mock_sync.assert_called_once()
 
        # El último mensaje enviado debe confirmar que se actualizó
        ultimo_mensaje = update.message.reply_text.call_args_list[-1].args[0]
        assert "actualizada" in ultimo_mensaje.lower()
 
    # Verifica que si sync_bdc_once falla el comando responde con mensaje de error
    async def test_error_en_sync_responde_con_error(self):
        import importlib, bot
        importlib.reload(bot)
        bot.BDC_REPO_URL = "https://github.com/ejemplo/repo.git"
 
        update = _make_update("/actualizar")
        with patch("bot.sync_bdc_once", side_effect=Exception("sin red")):
            await bot.actualizar_command(update, MagicMock())
 
        ultimo_mensaje = update.message.reply_text.call_args_list[-1].args[0]
        assert "error" in ultimo_mensaje.lower()
 
 
# --------------------------------------------------------------
#  ficheros_command
# --------------------------------------------------------------
@pytest.mark.asyncio
class TestFicherosCommand:
 
    # Verifica que si la BdC está vacía el comando avisa de que no hay archivos
    async def test_sin_ficheros_avisa_al_usuario(self):
        import importlib, bot
        importlib.reload(bot)
 
        update = _make_update("/ficheros")
        with patch("retrieval._load_bdc_files", return_value=[]):
            await bot.ficheros_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "no hay" in respuesta.lower()
 
    # Verifica que los nombres de los archivos cargados aparecen en la respuesta
    async def test_muestra_nombres_de_archivos(self):
        import importlib, bot
        importlib.reload(bot)
 
        files = [
            ("edo-primer-orden.md", "contenido 1"),
            ("metodo-euler.md",     "contenido 2"),
            ("ecuaciones-lineales.md", "contenido 3"),
        ]
        update = _make_update("/ficheros")
        with patch("retrieval._load_bdc_files", return_value=files):
            await bot.ficheros_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "edo-primer-orden.md" in respuesta
        assert "metodo-euler.md" in respuesta
        assert "ecuaciones-lineales.md" in respuesta
 
    # Verifica que el número de archivos cargados aparece en la respuesta
    async def test_muestra_numero_de_archivos(self):
        import importlib, bot
        importlib.reload(bot)
 
        files = [("nota1.md", "x"), ("nota2.md", "y")]
        update = _make_update("/ficheros")
        with patch("retrieval._load_bdc_files", return_value=files):
            await bot.ficheros_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "2" in respuesta
 
 
# --------------------------------------------------------------
#  debug_command
# --------------------------------------------------------------
@pytest.mark.asyncio
class TestDebugCommand:
 
    # Verifica que si no se ha procesado ninguna pregunta aún el comando lo indica
    async def test_sin_preguntas_previas_avisa(self):
        import importlib, bot
        importlib.reload(bot)
        bot._ultimo_debug = {"pregunta": None, "contexto": None}
 
        update = _make_update("/debug")
        await bot.debug_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "ninguna pregunta" in respuesta.lower() or "aún" in respuesta.lower()
 
    # Verifica que la última pregunta procesada aparece en la respuesta del comando
    async def test_muestra_ultima_pregunta(self):
        import importlib, bot
        importlib.reload(bot)
        bot._ultimo_debug = {
            "pregunta": "¿Qué es el wronskiano?",
            "contexto": "El wronskiano determina independencia lineal de soluciones."
        }
 
        update = _make_update("/debug")
        await bot.debug_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "wronskiano" in respuesta.lower()
 
    # Verifica que el contexto enviado al LLM aparece en la respuesta del comando
    async def test_muestra_contexto_enviado_al_llm(self):
        import importlib, bot
        importlib.reload(bot)
        bot._ultimo_debug = {
            "pregunta": "¿Qué es el método de Euler?",
            "contexto": "El método de Euler aproxima soluciones avanzando en pasos h."
        }
 
        update = _make_update("/debug")
        await bot.debug_command(update, MagicMock())
 
        respuesta = update.message.reply_text.call_args.args[0]
        assert "euler" in respuesta.lower()
 
    # Verifica que _ultimo_debug se actualiza correctamente al procesar un mensaje
    async def test_handle_message_actualiza_debug(self):
        import importlib, bot
        importlib.reload(bot)
        bot._ultimo_debug = {"pregunta": None, "contexto": None}
 
        update = _make_update("¿Qué es una EDO separable?")
        contexto = "Una EDO separable se escribe como g(y)dy = f(x)dx."
 
        with patch("retrieval.get_relevant_context", return_value=contexto), \
             patch("llm_client.generate", return_value="ok"):
            await bot.handle_message(update, MagicMock())
 
        assert bot._ultimo_debug["pregunta"] == "¿Qué es una EDO separable?"
        assert bot._ultimo_debug["contexto"] == contexto