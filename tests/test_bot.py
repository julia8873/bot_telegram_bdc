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

    def test_sin_url_no_hace_nada(self):
        with patch.dict("os.environ", {"BDC_REPO_URL": ""}):
            import importlib, bot
            importlib.reload(bot)
            # No debe lanzar ningún error aunque no haya git
            bot.sync_bdc_once()

    def test_clona_si_no_existe(self, tmp_path):
        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
               "BDC_PATH": str(tmp_path / "bdc")}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            mock_repo = MagicMock()
            with patch("git.Repo.clone_from") as mock_clone:
                bot.sync_bdc_once()
                mock_clone.assert_called_once()

    def test_hace_pull_si_ya_existe(self, tmp_path):
        bdc = tmp_path / "bdc"
        bdc.mkdir()
        (bdc / ".git").mkdir()  # simula que ya hay un repo clonado

        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
               "BDC_PATH": str(bdc)}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            mock_repo = MagicMock()
            with patch("git.Repo", return_value=mock_repo):
                bot.sync_bdc_once()
                mock_repo.remotes.origin.pull.assert_called_once()

    def test_error_git_no_detiene_el_bot(self, tmp_path):
        env = {"BDC_REPO_URL": "https://github.com/ejemplo/repo.git",
               "BDC_PATH": str(tmp_path / "bdc")}
        with patch.dict("os.environ", env):
            import importlib, bot
            importlib.reload(bot)
            with patch("git.Repo.clone_from", side_effect=Exception("sin red")):
                # No debe propagar la excepción
                bot.sync_bdc_once()


# --------------------------------------------------------------
#  handle_message
# --------------------------------------------------------------
def _make_update(texto: str) -> MagicMock:
    """Crea un objeto Update falso con el texto dado."""
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

    async def test_responde_con_bienvenida(self):
        import importlib, bot
        importlib.reload(bot)

        update = _make_update("/start")
        await bot.start_command(update, MagicMock())

        update.message.reply_text.assert_called_once()
        respuesta = update.message.reply_text.call_args.args[0]
        assert len(respuesta) > 0  # responde algo