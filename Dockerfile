# Define cómo se construye la imagen

# Clonaremos el git para poder hacer git pull sin tener que actualizar la imagen todo el rato

# Imagen base: cogemos slim porque ocupa mucho menos
# la versión 3.12 completa sería para herramientas de compilación o depuración dentro de la propia imagen
# dependerá de la librería que instalemos, si tienen un fichero wheel. Sino, habría que considerar la versión completa
FROM python:3.12-slim

# GitPython (src/bot.py) irá clonando y actualizando la BdC.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# apt-get update -> actualizar paquetes disponibles
# apt-get install -y --no-install-recomends git -> instala el paquete git
    # -y -> introduce 'yes'
    # --no-install-recomends -> solo instalamos el paquete pedido, no los que nos recomiende después
# rm -rf /var/lib/apt/lists/* -> borrar el índide descargado en "apt-get update"


# Todo se hará a partir de este directorio
WORKDIR /app

# Copiamos primero requirements.txt, que no cambiará mucho
# Docker va por capas: si el código cambia y las dependecias no -> no reinstalará las dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todo el código 
COPY src/ ./src/

# Se ejecuta al arrancar el contenedor
# Equivale a hacer "python src/bot.py" dentro del contenedor
CMD [ "python", "src/bot.py" ]