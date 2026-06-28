"""
Tests de llm_client.py

Se mockea requests.post para no hacer llamadas reales a ninguna API.
También se mockean las variables de entorno para controlar el proveedor.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# --------------------------------------------------------------
#  respuestas HTTP falsas
# --------------------------------------------------------------
def _mock_openai_response(texto="Respuesta de prueba."):
    """Simula la respuesta JSON que devuelve la API de OpenAI."""
    mock = MagicMock()
    mock.json.return_value = {
        "choices": [{"message": {"content": texto}}]
    }
    mock.raise_for_status = MagicMock()  # no lanza excepción
    return mock

def _mock_anthropic_response(texto="Respuesta de prueba."):
    """Simula la respuesta JSON que devuelve la API de Anthropic."""
    mock = MagicMock()
    mock.json.return_value = {
        "content": [{"type": "text", "text": texto}]
    }
    mock.raise_for_status = MagicMock()
    return mock


def _mock_http_error(status_code=401):
    """Simula un error HTTP de la API."""
    from requests.exceptions import HTTPError
    mock = MagicMock()
    mock.raise_for_status.side_effect = HTTPError(f"{status_code} Error")
    return mock

# --------------------------------------------------------------
#  generate()
# --------------------------------------------------------------
class TestGenerate:

    # Verifica que un proveedor no implementado lance ValueError con "desconocido"
    def test_proveedor_desconocido_lanza_error(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "proveedor_inventado"}):
            import importlib
            import llm_client
            importlib.reload(llm_client)
            with pytest.raises(ValueError, match="desconocido"):
                llm_client.generate("hola")

    # Verifica que con proveedor "openai", se llame a _generate_openai_compatible
    def test_openai_llama_a_funcion_correcta(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}):
            import importlib
            import llm_client
            importlib.reload(llm_client)
            with patch("llm_client._generate_openai_compatible", return_value="ok") as mock_fn:
                llm_client.generate("prompt", "system")
                mock_fn.assert_called_once_with("prompt", "system")

    # Verifica que con proveedor "anthropic", se llame a _generate_anthropic
    def test_anthropic_llama_a_funcion_correcta(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "anthropic"}):
            import importlib
            import llm_client
            importlib.reload(llm_client)
            with patch("llm_client._generate_anthropic", return_value="ok") as mock_fn:
                llm_client.generate("prompt", "system")
                mock_fn.assert_called_once_with("prompt", "system")

    # Verifica que con proveedor "openai_compatible", se llame a _generate_openai_compatible
    def test_openai_compatible_llama_a_funcion_correcta(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai_compatible"}):
            import importlib
            import llm_client
            importlib.reload(llm_client)
            with patch("llm_client._generate_openai_compatible", return_value="ok") as mock_fn:
                llm_client.generate("prompt")
                mock_fn.assert_called_once()

# --------------------------------------------------------------
#  _generate_openai_compatible
# --------------------------------------------------------------
class TestGenerateOpenAICompatible:

    # Recarga el módulo antes de cada test para que los env vars estén nuevos
    def setup_method(self):
        import importlib, llm_client
        importlib.reload(llm_client)
        self.llm = llm_client

    # Verifica que el texto de la respuesta de OpenAI se extrae y devuelve correctamente
    def test_devuelve_texto_de_respuesta(self):
        with patch("requests.post", return_value=_mock_openai_response("Una EDO de primer orden relaciona una función con su derivada.")):
            resultado = self.llm._generate_openai_compatible("¿Qué es una EDO de primer orden?", "")
        assert resultado == "Una EDO de primer orden relaciona una función con su derivada."

    # Verifica que el .strip() del código elimina espacios sobrantes al inicio y al final
    def test_strips_espacios_sobrantes(self):
        with patch("requests.post", return_value=_mock_openai_response("  El método de Euler avanza en pasos h.  ")):
            resultado = self.llm._generate_openai_compatible("¿Qué es el método de Euler?", "")
        assert resultado == "El método de Euler avanza en pasos h."

    # Verifica que el system prompt se incluye en los mensajes con rol "system" cuando no está vacío
    def test_incluye_system_prompt_si_existe(self):
        with patch("requests.post", return_value=_mock_openai_response()) as mock_post:
            self.llm._generate_openai_compatible("¿Qué es una EDO lineal?", "Eres un asistente de ecuaciones diferenciales.")
            payload = mock_post.call_args.kwargs["json"]
            roles = [m["role"] for m in payload["messages"]]
            assert "system" in roles

    # Verifica que no se añade mensaje de rol "system" cuando el system prompt está vacío
    def test_no_incluye_system_prompt_si_vacio(self):
        with patch("requests.post", return_value=_mock_openai_response()) as mock_post:
            self.llm._generate_openai_compatible("¿Qué es una EDO homogénea?", "")
            payload = mock_post.call_args.kwargs["json"]
            roles = [m["role"] for m in payload["messages"]]
            assert "system" not in roles

    # Verifica que el .rstrip('/') evita dobles barras en la URL aunque el .env las tenga
    def test_url_no_tiene_doble_barra(self):
        env = {"LLM_BASE_URL": "https://api.openai.com/v1/"}  # con / al final
        with patch.dict("os.environ", env):
            import importlib, llm_client
            importlib.reload(llm_client)
            with patch("requests.post", return_value=_mock_openai_response()) as mock_post:
                llm_client._generate_openai_compatible("¿Qué es el wronskiano?", "")
                url_usada = mock_post.call_args.args[0]
            assert "//" not in url_usada.replace("https://", "")

    # Verifica que un error HTTP de la API (ej. 401) se propaga como excepción
    def test_error_http_lanza_excepcion(self):
        from requests.exceptions import HTTPError
        with patch("requests.post", return_value=_mock_http_error(401)):
            with pytest.raises(HTTPError):
                self.llm._generate_openai_compatible("¿Qué es una solución particular?", "")

    # Verifica que temperature=0 para que el LLM dé respuestas deterministas
    def test_temperatura_es_cero(self):
        with patch("requests.post", return_value=_mock_openai_response()) as mock_post:
            self.llm._generate_openai_compatible("¿Qué es una EDO separable?", "")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["temperature"] == 0


class TestGenerateAnthropic:

    # Recarga el módulo antes de cada test para que los env vars sean nuevos
    def setup_method(self):
        import importlib, llm_client
        importlib.reload(llm_client)
        self.llm = llm_client

    # Verifica que el texto de la respuesta de Anthropic se extrae y devuelve correctamente
    def test_devuelve_texto_de_respuesta(self):
        with patch("requests.post", return_value=_mock_anthropic_response("Una EDO lineal homogénea tiene g(x) = 0.")):
            resultado = self.llm._generate_anthropic("¿Qué es una EDO homogénea?", "")
        assert resultado == "Una EDO lineal homogénea tiene g(x) = 0."

    # Verifica que varios bloques de texto en la respuesta se unen en un solo string
    def test_une_multiples_bloques_de_texto(self):
        mock = MagicMock()
        mock.json.return_value = {
            "content": [
                {"type": "text", "text": "La solución general es combinación lineal. "},
                {"type": "text", "text": "Las soluciones particulares forman la base."},
            ]
        }
        mock.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock):
            resultado = self.llm._generate_anthropic("¿Qué es la solución general?", "")
        assert "La solución general es combinación lineal." in resultado
        assert "Las soluciones particulares forman la base." in resultado

    # Verifica que los bloques que no son texto (ej. imágenes) se ignoran
    def test_ignora_bloques_que_no_son_texto(self):
        mock = MagicMock()
        mock.json.return_value = {
            "content": [
                {"type": "image", "data": "base64..."},
                {"type": "text", "text": "El método de Euler es un método numérico."},
            ]
        }
        mock.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock):
            resultado = self.llm._generate_anthropic("¿Qué es Euler?", "")
        assert resultado == "El método de Euler es un método numérico."

    # Verifica que Anthropic usa "x-api-key" en la cabecera en vez de "Authorization: Bearer"
    def test_usa_header_x_api_key(self):
        with patch("requests.post", return_value=_mock_anthropic_response()) as mock_post:
            self.llm._generate_anthropic("¿Qué es el wronskiano?", "")
            headers = mock_post.call_args.kwargs["headers"]
            assert "x-api-key" in headers

    # Verifica que el system prompt va en el campo "system" del payload, no dentro de messages
    def test_system_prompt_va_en_campo_propio(self):
        with patch("requests.post", return_value=_mock_anthropic_response()) as mock_post:
            self.llm._generate_anthropic("¿Qué es una EDO separable?", "Eres un asistente de ecuaciones diferenciales.")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["system"] == "Eres un asistente de ecuaciones diferenciales."
            for msg in payload["messages"]:
                assert msg["role"] != "system"

    # Verifica que un error HTTP de la API (ej. 403) se propaga como excepción
    def test_error_http_lanza_excepcion(self):
        from requests.exceptions import HTTPError
        with patch("requests.post", return_value=_mock_http_error(403)):
            with pytest.raises(HTTPError):
                self.llm._generate_anthropic("¿Qué es una solución homogénea?", "")



