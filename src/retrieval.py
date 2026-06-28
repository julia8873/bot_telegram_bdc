"""
Recuperar contexto de la BdC

Versión simple: coincidencia de palabras clave sobre los ficheros .md de la BdC. Es determinista, rápida y no añade 
dependencias pesadas. 

Hacer versión con embeddings + búsqueda vectorial

Nota: la BdC se mantiene con el plugin de Obsidian "LLM Wiki Assistant"
siguiendo el estándar OKF, que genera archivos (AGENTS.md, okf/index.md, okf/log.md).
Estos archivos ccontienen información que el bot no necesita y no tiene que buscar en ellos.

"""


import os                    # Para leer variables de entorno.
import re                    # Para expresiones regulares (tokenizar texto y quitar frontmatter).
from pathlib import Path     # Para manejar rutas de archivos de forma más cómoda que con strings.

BDC_PATH = Path(os.getenv("BDC_PATH", "./bdc"))            # Ruta local de la BdC. (por defecto: ./bdc)
TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "4"))             # Número máximo de fragmentos a devolver. (por defecto: 4)

# Excluir archivos y carpetas generados por el plugin OKF que no nos interesan como fuente de respuesta
# (los genera el plugin OKF y no es contenido del que tengamos que extraer información)

EXCLUDED_FILENAMES = {"agents.md", "index.md", "log.md"}
EXCLUDED_DIRS = {"okf"}

# Expresión regular que detecta el bloque YAML al inicio de un archivo .md
# es metadata como título, fecha, tags, que no es contenido útil
# Ejemplo de frontmatter:
#    title: RAG
#    date: 2024-01-15
#    status: draft
#    tags: [rag, llm]
# Esto es para obsidian y organizar las entidades. El LLM no necesita saberlo
# El contenido importante ya estará escrito en la nota o en el nombre del archivo
FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


# convertir texto en conjunto de palabras únicas en minúsuclas
def _tokenize(text: str) -> set:
    return set(re.findall(r"\w+", text.lower()))


# Devuelve True si el archivo se debe ignorar
def _is_excluded(rel_path: Path) -> bool:
    if rel_path.name.lower() in EXCLUDED_FILENAMES:
        return True
    
    if any(part.lower() in EXCLUDED_DIRS for part in rel_path.parts):
        # rel_path.parts descompone la ruta "okf/log.md" en ("okf", "log.md") -> detectaría "okf"
        return True

    return False


def _strip_frontmatter(content: str) -> str:
    """Quita el bloque YAML (--- ... ---) del principio, si existe."""
    return FRONTMATTER_RE.sub("", content, count=1) # count=1 solo elimina el primero ---

def _load_bdc_files() -> list[tuple[str,str]]:
    """Devuelve [(ruta_relativa, contenido_sin_frontmatter), ...] de las
    notas reales de /bdc, excluyendo los archivos que no nos interesan de OKF."""

    files = [] # lista con archivos válidos

    if not BDC_PATH.exists():  # si la carpeta de la BdC no existe
        return files  # devolver vacío
    
    for path in BDC_PATH.rglob("*.md"):
        rel_path = path.relative_to(BDC_PATH) # obtener rita relativa (eg "tema1/nota.md")

        if _is_excluded(rel_path): # si el archivo no es de nuestro interés (los q ha generado OKF que hemos filtrado)
            continue

        try:
            content = _strip_frontmatter(path.read_text(encoding="utf-8"))
            # lee archivo como texto UTF-8 y quita frontmatter YAML
            files.append((str(rel_path), content)) # Guarda ruta y contenido limpio
        except Exception:
            continue
    
    return files


def get_relevant_context(question: str, top_k: int = TOP_K) -> str:
    """
    Devuelve un bloque de texto con los `top_k` fragmentos de /bdc más
    relevantes para la pregunta, para meter en el prompt del LLM.
    """

    question_tokens = _tokenize(question)  # Convierte la pregunta en conjunto de palabras.

    if not question_tokens:  # Si la pregunta está vacía...
        return ""          

    scored = []  # Lista donde se guardan los archivos con su puntuación de relevancia.

    for rel_path, content in _load_bdc_files():  # Itera sobre todos los archivos válidos de la BdC.
        content_tokens = _tokenize(content)       # Tokeniza el contenido del archivo.
        overlap = len(question_tokens & content_tokens)
        # & es intersección de conjuntos: cuenta cuántas palabras de la pregunta
        # aparecen también en el archivo. Esa cantidad es la puntuación de relevancia.
        # Ej: pregunta={"qué","es","rag"}, archivo={"rag","es","vectorial"} -> overlap=2

        if overlap > 0:                                   # Solo considera archivos con al menos una palabra en común.
            scored.append((overlap, rel_path, content))   # Guarda (puntuación, ruta, contenido).

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k] 

    if not top:  
        return "" # devolver vacío, el bot no tendrá información

    blocks = [f"### Fuente: {rel_path}\n{content.strip()}" for _, rel_path, content in top]

    return "\n\n".join(blocks)