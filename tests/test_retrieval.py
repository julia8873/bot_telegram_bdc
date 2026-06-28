"""
Tests de retrieval.py

Se prueban las funciones puras directamente y las que usan el sistema de archivos con una BdC temporal creada por pytest (tmp_path).
"""

import pytest
from pathlib import Path
from unittest.mock import patch

# buscamos el directorio de src para importar las funciones del fichero retrieval
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from retrieval import (
    _tokenize,
    _is_excluded,
    _strip_frontmatter,
    _load_bdc_files,
    get_relevant_context,
)


# --------------------------------------------------------------
#  Tests de la función _tokenize
# --------------------------------------------------------------
class TestTokenize:

    def test_devuelve_set(self):
        assert isinstance(_tokenize("hola mundo"), set)

    def test_convierte_a_minusculas(self):
        assert "rag" in _tokenize("RAG es útil")

    def test_elimina_puntuacion(self):
        tokens = _tokenize("hola, ¿cómo estás?")
        assert "," not in tokens
        assert "?" not in tokens

    def test_elimina_duplicados(self):
        # set -> cada palabra aparece una sola vez
        tokens = _tokenize("hola hola hola")
        assert tokens == {"hola"}

    def test_texto_vacio(self):
        assert _tokenize("") == set()

    def test_solo_puntuacion(self):
        assert _tokenize(".,;:!?") == set()

    def test_palabras_normales(self):
        assert _tokenize("ecuaciones diferenciales") == {"ecuaciones", "diferenciales"}


# --------------------------------------------------------------
#  Tests de la función _is_excluded
# --------------------------------------------------------------
class TestIsExcluded:

    def test_agents_excluido(self):
        assert _is_excluded(Path("AGENTS.md")) is True

    def test_index_excluido(self):
        assert _is_excluded(Path("index.md")) is True

    def test_log_excluido(self):
        assert _is_excluded(Path("log.md")) is True

    def test_carpeta_okf_excluida(self):
        assert _is_excluded(Path("okf/cualquier-cosa.md")) is True

    def test_archivo_normal_no_excluido(self):
        assert _is_excluded(Path("ecuaciones.md")) is False

    def test_subcarpeta_normal_no_excluida(self):
        assert _is_excluded(Path("ecuaciones/T1.md")) is False

    def test_mayusculas_no_importan(self):
        # AGENTS.md y agents.md deben tratarse igual
        assert _is_excluded(Path("AGENTS.MD")) is True


# --------------------------------------------------------------
#  Tests de la función _strip_frontmatter
# --------------------------------------------------------------
class TestStripFrontmatter:

    def test_elimina_frontmatter(self):
        contenido = "---\ntitle: Test\ntags: [a]\n---\nContenido real."
        resultado = _strip_frontmatter(contenido)
        assert "title" not in resultado
        assert "Contenido real." in resultado

    def test_sin_frontmatter_no_cambia(self):
        contenido = "Contenido sin frontmatter."
        assert _strip_frontmatter(contenido) == contenido

    def test_no_elimina_guiones_en_contenido(self):
        # Los --- en mitad del texto no deben eliminarse
        contenido = "Intro.\n\n---\n\nSeparador visual."
        resultado = _strip_frontmatter(contenido)
        assert "Separador visual." in resultado

    def test_solo_elimina_primer_bloque(self):
        # Si hubiera dos bloques --- por algún motivo, solo borra el primero
        contenido = "---\ntitle: A\n---\nTexto.\n---\notra cosa\n---\n"
        resultado = _strip_frontmatter(contenido)
        assert "Texto." in resultado

# ------------------------------------------------------------------------------
#  Tests de la función _load_bdc_files (usamos sistema de archivos temporal)
# ------------------------------------------------------------------------------
class TestLoadBdcFiles:

    def test_carga_archivos_normales(self, tmp_path):
        (tmp_path / "nota.md").write_text("Contenido de nota.", encoding="utf-8")
        with patch("retrieval.BDC_PATH", tmp_path):
            files = _load_bdc_files()
        assert len(files) == 1
        assert "nota.md" in files[0][0]

    def test_excluye_agents(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Metadata interna.", encoding="utf-8")
        (tmp_path / "nota.md").write_text("Contenido real.", encoding="utf-8")
        with patch("retrieval.BDC_PATH", tmp_path):
            files = _load_bdc_files()
        nombres = [f[0] for f in files]
        assert not any("AGENTS" in n.upper() for n in nombres)

    def test_excluye_carpeta_okf(self, tmp_path):
        okf = tmp_path / "okf"
        okf.mkdir()
        (okf / "index.md").write_text("Índice interno.", encoding="utf-8")
        (tmp_path / "nota.md").write_text("Contenido real.", encoding="utf-8")
        with patch("retrieval.BDC_PATH", tmp_path):
            files = _load_bdc_files()
        nombres = [f[0] for f in files]
        assert not any("okf" in n for n in nombres)

    def test_quita_frontmatter_del_contenido(self, tmp_path):
        (tmp_path / "nota.md").write_text(
            "---\ntitle: Test\n---\nContenido útil.", encoding="utf-8"
        )
        with patch("retrieval.BDC_PATH", tmp_path):
            files = _load_bdc_files()
        assert "title" not in files[0][1]
        assert "Contenido útil." in files[0][1]

    def test_bdc_inexistente_devuelve_lista_vacia(self, tmp_path):
        ruta_que_no_existe = tmp_path / "no-existe"
        with patch("retrieval.BDC_PATH", ruta_que_no_existe):
            files = _load_bdc_files()
        assert files == []

    def test_carga_subcarpetas(self, tmp_path):
        sub = tmp_path / "servicios"
        sub.mkdir()
        (sub / "talleres.md").write_text("Info talleres.", encoding="utf-8")
        with patch("retrieval.BDC_PATH", tmp_path):
            files = _load_bdc_files()
        assert any("talleres.md" in f[0] for f in files)

# --------------------------------------------------------------
#  Tests de la función get_relevant_context
# --------------------------------------------------------------
class TestGetRelevantContext:

    def _crear_bdc(self, tmp_path):
        """Crea una BdC de prueba con tres notas de Ecuaciones Diferenciales."""
        (tmp_path / "edo-primer-orden.md").write_text(
            "Una ecuación diferencial ordinaria de primer orden relaciona "
            "una función con su derivada. La forma general es dy/dx = f(x, y).",
            encoding="utf-8",
        )
        (tmp_path / "metodo-euler.md").write_text(
            "El método de Euler es un método numérico para resolver EDOs. "
            "Aproxima la solución avanzando en pasos pequeños h a lo largo del eje x.",
            encoding="utf-8",
        )
        (tmp_path / "ecuaciones-lineales.md").write_text(
            "Una EDO lineal de orden n tiene la forma a_n(x)y^(n) + ... + a_0(x)y = g(x). "
            "Si g(x) = 0 se llama homogénea.",
            encoding="utf-8",
        )

    def test_devuelve_contexto_relevante(self, tmp_path):
        self._crear_bdc(tmp_path)
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("¿Qué es una ecuación diferencial de primer orden?")
        assert "ecuación" in resultado.lower()

    def test_fuente_incluida_en_resultado(self, tmp_path):
        self._crear_bdc(tmp_path)
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("método numérico Euler pasos")
        assert "### Fuente:" in resultado

    def test_pregunta_sin_coincidencias_devuelve_vacio(self, tmp_path):
        self._crear_bdc(tmp_path)
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("fotosíntesis respiración celular")
        assert resultado == ""

    def test_pregunta_vacia_devuelve_vacio(self, tmp_path):
        self._crear_bdc(tmp_path)
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("")
        assert resultado == ""

    def test_top_k_limita_resultados(self, tmp_path):
        # Creamos 3 notas que todas contienen "solución"
        for i in range(3):
            (tmp_path / f"tema{i}.md").write_text(
                f"Tema {i}: la solución general de una EDO lineal homogénea "
                f"es combinación lineal de soluciones particulares.",
                encoding="utf-8",
            )
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("solución general EDO lineal homogénea", top_k=2)
        # Con top_k=2 solo deben aparecer 2 bloques "### Fuente:"
        assert resultado.count("### Fuente:") == 2

    def test_archivo_mas_relevante_aparece_primero(self, tmp_path):
        # nota_a tiene MÁS palabras en común con la pregunta que nota_b
        (tmp_path / "nota_a.md").write_text(
            "El método de Euler aproxima soluciones de EDOs mediante pasos "
            "numéricos iterativos con tamaño h.",
            encoding="utf-8",
        )
        (tmp_path / "nota_b.md").write_text(
            "El método de Euler es numérico.",  # solo comparte "método" y "Euler"
            encoding="utf-8",
        )
        with patch("retrieval.BDC_PATH", tmp_path):
            resultado = get_relevant_context("método Euler aproxima soluciones pasos numéricos iterativos")
        # ambas aparecen (las dos tienen overlap > 0), pero nota_a va primero
        assert resultado.index("nota_a") < resultado.index("nota_b")

