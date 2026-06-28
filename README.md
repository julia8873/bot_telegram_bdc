# bot_telegram_bdc
Bot de Telegram que responde preguntas consultando una Base de Conocimiento (BdC) en Markdown, que estará en un git separado.
Se usará un LLM configurable (cualquiera para pruebas, el de la UGR en producción).
Pensado para estar ejecutándose en background constantemente en Docker.
---

## Índice

- [Visión general](#visión-general)
- [Arquitectura](#arquitectura)
- [Estructura del repo](#estructura-del-repo)
- [Requisitos previos](#requisitos-previos)
- [Puesta en marcha](#puesta-en-marcha)
- [Variables de entorno](#variables-de-entorno)
- [Cómo funciona una consulta](#cómo-funciona-una-consulta)
- [Actualizar la Base de Conocimiento](#actualizar-la-base-de-conocimiento)
- [Cambiar de proveedor LLM](#cambiar-de-proveedor-llm)
- [Comandos útiles](#comandos-útiles)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)

---

## Visión general

- **Entrada**: un usuario escribe una pregunta al bot de Telegram.
- **Recuperación**: el bot busca los fragmentos más relevantes dentro de la
  BdC (ficheros `.md`) clonada localmente.
- **Generación**: construye un prompt con esa pregunta + el contexto
  recuperado y se lo manda al LLM configurado.
- **Salida**: responde por Telegram con la respuesta del LLM, basada solo en
  el contexto encontrado (si no hay contexto relevante, lo indica en vez de
  inventar).

El código y la BdC viven en **repos de GitHub separados**: este repo solo
tiene lógica, el otro tiene contenido. El contenedor clona/actualiza la BdC
en tiempo de ejecución, así que actualizar contenido no requiere reconstruir
ni redesplegar el bot.

## Arquitectura

```
 Usuario (Telegram)
        |
        v
  -----------------
 |     bot.py      |  polling continuo, se ejecuta dentro del contenedor
  -----------------
          |
          v
  ------------------      git clone / pull cada N seg
 │  retrieval.py    |  < --------------------------------
 │ (busca contexto) |                                   |
  ------------------                          -------------------
          │                                  │ Repo BdC (GitHub) |
          v                                   -------------------
  --------------------
 │   llm_client.py    |
  --------------------
          │
          v
   Respuesta al usuario
```

## Estructura del repo

```
bot-telegram-bdc/
├── src/
│   ├── bot.py            # Entrypoint: bot de Telegram + sincronización de la BdC
│   ├── retrieval.py      # Búsqueda de contexto relevante en la BdC
│   └── llm_client.py     # Abstracción de proveedor LLM (OpenAI/Anthropic/UGR)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── .gitignore
```
## Dependencias

Definidas en `requirements.txt`.

| Librería | Para qué se usa |
|---|---|
| [`python-telegram-bot`](https://pypi.org/project/python-telegram-bot/) | Conecta con la API de Telegram: recibe mensajes (polling), gestiona comandos y envía respuestas.|
| [`python-dotenv`](https://pypi.org/project/python-dotenv/) | Carga las variables de `.env` (token, API keys...) como variables de entorno del proceso. |
| [`requests`](https://pypi.org/project/requests/) | Llamadas HTTP al LLM configurado (OpenAI/Anthropic/UGR), usado en `llm_client.py`. |
| [`GitPython`](https://pypi.org/project/GitPython/) | Ejecuta `git clone`/`git pull` desde Python para sincronizar la BdC, usado en `bot.py`. |


## Requisitos previos

- Docker y Docker Compose instalados en el servidor.
- Un bot de Telegram creado vía [@BotFather](https://t.me/BotFather) (token).
- Acceso al repo de GitHub de la BdC (URL, y un Personal Access Token de solo lectura si es privado).
- Credenciales de algún LLM.

## Cómo Ejecutar

```bash
git clone https://github.com/<tu-org>/bot-telegram-bdc.git
cd bot-telegram-bdc
cp .env.example .env
# Edita .env con tus credenciales (ver tabla de variables más abajo)

docker compose build
docker compose up -d
docker compose logs -f   # comprobar que arranca y clona la BdC sin errores
```

Si todo va bien, verás en los logs algo como:

```
BdC clonada por primera vez en ./bdc.
Bot iniciado. Esperando mensajes (polling)...
```

Deberías de poder usar el bot de Telegram.

## Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot, dado por @BotFather | `123456:ABC-...` |
| `BDC_PATH` | Carpeta local donde se clona la BdC dentro del contenedor | `./bdc` |
| `BDC_REPO_URL` | URL del repo de la BdC. Incluye `<PAT>@` si es privado | `https://<PAT>@github.com/org/bdc.git` |
| `BDC_PULL_INTERVAL_SECONDS` | Cada cuánto se sincroniza la BdC | `300` |
| `LLM_PROVIDER` | `openai`, `anthropic` o `ugr` (compatible OpenAI) | `openai` |
| `LLM_BASE_URL` | Endpoint base del proveedor | `https://api.openai.com/v1` |
| `LLM_API_KEY` | API key del proveedor | `sk-...` |
| `LLM_MODEL` | Modelo a usar | `gpt-4o-mini` |
| `RETRIEVAL_TOP_K` | Nº de fragmentos de la BdC usados como contexto por consulta | `4` |

## Cómo funciona una consulta

1. El usuario escribe una pregunta por Telegram.
2. `retrieval.py` busca, por coincidencia de palabras clave, los `TOP_K`
   ficheros `.md` de la BdC más relevantes para esa pregunta.
3. `bot.py` construye un prompt: pregunta + fragmentos recuperados +
   instrucción explícita de responder solo con esa información.
4. `llm_client.generate()` llama al proveedor configurado y devuelve la
   respuesta.
5. El bot responde al usuario por Telegram.

Si no se encuentra contexto relevante, el prompt se construye igualmente
pero indicando al LLM que no hay información suficiente, para evitar
alucinaciones.

## Actualizar la Base de Conocimiento

No requiere tocar este repo ni el contenedor del bot:

```bash
# en el repo de la BdC
git add .
git commit -m "Actualiza guía docente de X"
git push
```

El bot la recoge sola en el siguiente ciclo de sincronización (`BDC_PULL_INTERVAL_SECONDS`, 5 min por defecto).

## Cambiar de proveedor LLM

Editar 4 variables en `.env` (`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL`) y recrear el contenedor:

```bash
docker compose up -d --force-recreate
```

No hace falta tocar ni reconstruir código: `llm_client.py` ya soporta cualquier endpoint compatible con el formato de OpenAI.
Si el endpoint de la UGR tiene un formato distinto, solo hay que adaptar una función en `llm_client.py`.

## Comandos útiles

```bash
docker compose ps              # estado del contenedor
docker compose logs -f         # logs en tiempo real
docker compose restart bot     # reinicio manual
docker compose down            # parar todo (el volumen bdc_data persiste)
docker compose exec bot bash   # entrar al contenedor para depurar
```

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| El contenedor se reinicia en bucle | Falta `TELEGRAM_BOT_TOKEN` o es inválido | Revisar `.env` |
| Error de autenticación al clonar la BdC | PAT incorrecto, caducado o sin permisos | Regenerar el PAT con permiso `Contents: Read-only` sobre ese repo |
| El bot responde "no tengo información suficiente" siempre | La BdC no tiene contenido relevante, o no se clonó | Revisar logs (`docker compose logs -f`) y el contenido de `BDC_PATH` dentro del contenedor |
| Error llamando al LLM | API key incorrecta o `LLM_BASE_URL`/`LLM_MODEL` mal configurados | Revisar `.env` y probar el endpoint manualmente con `curl` |
