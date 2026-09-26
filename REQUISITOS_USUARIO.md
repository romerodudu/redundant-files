# Especificación y Trazabilidad de Requisitos de Usuario

Este documento recopila y formaliza todos los requerimientos funcionales, no funcionales y de experiencia de usuario planteados a lo largo del ciclo de vida del proyecto **Redundant Files**, detallando el problema de origen, la solución técnica implementada y su verificación.

---

## Índice de Requisitos

1. [REQ-01: Visibilidad de Progreso y Estimación en Fase 4 (Hashes Completos)](#req-01-visibilidad-de-progreso-y-estimación-en-fase-4-hashes-completos)
2. [REQ-02: Interfaz Interactiva de Limpieza a Pantalla Completa (TUI Ncurses)](#req-02-interfaz-interactiva-de-limpieza-a-pantalla-completa-tui-ncurses)
3. [REQ-03: Pantalla de Confirmación Previa al Borrado Definitivo](#req-03-pantalla-de-confirmación-previa-al-borrado-definitivo)
4. [REQ-04: Legibilidad de Nombres Largos y Scroll Horizontal](#req-04-legibilidad-de-nombres-largos-y-scroll-horizontal)
5. [REQ-05: Descubrimiento de Configuración Local y Exclusiones Case-Insensitive](#req-05-descubrimiento-de-configuración-local-y-exclusiones-case-insensitive)
6. [REQ-06: Detección Cruzada de Duplicados entre Múltiples Volúmenes](#req-06-detección-cruzada-de-duplicados-entre-múltiples-volúmenes)
7. [REQ-07: Compatibilidad y Relleno Rápido de Hashes para Volúmenes Previos](#req-07-compatibilidad-y-relleno-rápido-de-hashes-para-volúmenes-previos)
8. [REQ-08: Identificación Explícita de la Procedencia del Volumen](#req-08-identificación-explícita-de-la-procedencia-del-volumen)

---

### REQ-01: Visibilidad de Progreso y Estimación en Fase 4 (Hashes Completos)

* **Origen / Petición del Usuario:**
  > *"Phase 4/4: Resolving full hashes... y se queda colgado, no se ve progreso"*
* **Problema:** Al calcular el hash BLAKE3 completo para ficheros de gran tamaño (decenas o cientos de gigabytes), el terminal parecía congelado al no haber actualización visual hasta finalizar el archivo completo.
* **Requisito:** Mostrar el progreso en vivo durante la lectura de cada fichero grande, incluyendo porcentaje, velocidad de transferencia (MB/s), bytes leídos, tiempo transcurrido y tiempo estimado restante (ETA).
* **Solución Implementada:**
  * Modificación de `_compute_full_hash` en [`scanner.py`](src/redundant_files/scanner.py) para aceptar un `progress_callback` que se invoca en cada bloque leído (chunk de 64 KB).
  * Uso de doble barra de progreso con `rich.progress`: progreso global de todos los ficheros candidatos y progreso individual del fichero activo.
* **Verificación:** Pruebas con archivos grandes y ejecución de `test_scanner.py`.

---

### REQ-02: Interfaz Interactiva de Limpieza a Pantalla Completa (TUI Ncurses)

* **Origen / Petición del Usuario:**
  > *"el cleanup haz que lo pueda seleccionar por medio de una interfaz con ncurses para que sea más operativo"*
* **Problema:** Los prompts secuenciales basados en preguntas de consola eran lentos e incómodos para gestionar decenas de grupos con múltiples ficheros duplicados.
* **Requisito:** Proveer una interfaz de usuario en terminal (TUI) basada en `curses` (`windows-curses` en Windows), rápida, intuitiva y a pantalla completa.
* **Solución Implementada:**
  * Implementación del módulo [`tui_cleanup.py`](src/redundant_files/tui_cleanup.py).
  * Arquitectura de doble panel:
    * **Panel Izquierdo:** Lista de grupos de duplicados (tamaño, número de copias, estado: `[skip]`, `[X del]`, `[IGN]`).
    * **Panel Derecho:** Ficheros del grupo activo con estado `[ ] (KEEP)` o `[*] (DEL)`.
  * Barra inferior con métricas en vivo: total de ficheros a borrar, espacio a recuperar y grupos ignorados.
  * Atajos de teclado completos (`[Tab]`, flechas, `[Espacio]`, `[a]` para autoselección de grupo, `[A]` para autoselección global, `[i]` para ignorar, `[Enter]` para confirmar).
* **Verificación:** Suite de pruebas en [`tests/test_tui_cleanup.py`](tests/test_tui_cleanup.py).

---

### REQ-03: Pantalla de Confirmación Previa al Borrado Definitivo

* **Origen / Petición del Usuario:**
  > *"despues de seleccionar cuando confirmo, antes de borrar cuando me pides confirmacion muéstrame el listado de ficheros seleccionados"*
* **Problema:** El usuario necesita verificar con total certeza qué archivos concretos va a borrar y cuáles va a conservar antes de dar la confirmación destructiva final `[y/N]`.
* **Requisito:** Mostrar una tabla comparativa con cada fichero a eliminar frente a su copia conservada antes de la pregunta final de confirmación.
* **Solución Implementada:**
  * Incorporación del método `confirm_plan` y `show_cleanup_summary` en [`ui.py`](src/redundant_files/ui.py).
  * Muestra una tabla Rich estructurada con: `#`, `Fichero a Borrar (Volumen | Ruta)`, `Tamaño`, y `Copia Conservada (Volumen | Ruta)`.
* **Verificación:** Ejecución integrada en el flujo interactivo de `redundant-files cleanup`.

---

### REQ-04: Legibilidad de Nombres Largos y Scroll Horizontal

* **Origen / Petición del Usuario:**
  > *"no puedo ver el nombre completo de los ficheros al seleccionar"*
* **Problema:** En terminales con ancho limitado o con nombres de ficheros largos con espacios y versiones, los nombres se truncaban y no permitían distinguir las copias.
* **Requisito:** Diseñar la interfaz de modo que se aprecie con claridad el nombre del fichero, su carpeta contenedora y que sea posible desplazar horizontalmente el texto para leer rutas completas.
* **Solución Implementada:**
  * Diseño de 2 filas por fichero en el panel derecho de [`tui_cleanup.py`](src/redundant_files/tui_cleanup.py):
    * Fila 1: Estado (`KEEP`/`DEL`), etiqueta de volumen y nombre limpio del fichero (`filename.ext`).
    * Fila 2: Carpeta contenedora, tamaño formateado y fecha de modificación.
  * **Caja de detalles dedicada** en las líneas inferiores de la pantalla que muestra la ruta completa sin abreviar (`Full Path: ...`).
  * **Desplazamiento horizontal (scroll horizontal):** Navegación con `[← / →]` o teclas `[h / l]` con indicador de desplazamiento (`[+offset]`).
* **Verificación:** Tests específicos de desplazamiento y recorte en `tests/test_tui_cleanup.py`.

---

### REQ-05: Descubrimiento de Configuración Local y Exclusiones Case-Insensitive

* **Origen / Petición del Usuario:**
  > *"creo que no está ignorando los ficheros segun digo en el fichero de configuración ¿por que? ¿no usa el fichero de donde esta mi proyecto? ¿que fichero de config usa?"*
* **Problema:**
  1. El escáner sólo leía la configuración de `~/.redundant-files/config.yaml`, ignorando el `config.yaml` presente en el directorio del proyecto donde trabajaba el usuario.
  2. Las rutas y extensiones en Windows se evaluaban de forma estricta según mayúsculas/minúsculas (`case-sensitive`), fallando si la extensión o carpeta tenía mayúsculas (p. ej. `.BAK` vs `.bak` o `SUBDIR` vs `subdir`).
* **Requisito:**
  1. Detectar automáticamente `./config.yaml` en el directorio de ejecución actual antes de recurrir a la ruta global.
  2. Aplicar exclusiones insensibles a mayúsculas/minúsculas y dar soporte tanto a nombres simples como a rutas relativas/patrones glob.
* **Solución Implementada:**
  * En [`config.py`](src/redundant_files/config.py): función `discover_config_path()` que prioriza `./config.yaml` o `./config.yml`.
  * En [`scanner.py`](src/redundant_files/scanner.py): compilación de expresiones regulares con `re.IGNORECASE` y normalización de separadores (`/` y `\`).
* **Verificación:** Pruebas unitarias en `test_scanner_size_pregrouping_and_exclusions` y `test_scanner_case_insensitive_and_path_exclusions`.

---

### REQ-06: Detección Cruzada de Duplicados entre Múltiples Volúmenes

* **Origen / Petición del Usuario:**
  > *"¿se está haciendo comparación con ficheros encontrados en otros volumenes tambien para determinar la duplicidad?"*
* **Problema:** Si un archivo tenía un tamaño único dentro de su propio disco en el momento en que se escaneó, la Fase 3 se saltaba el cálculo de `quick_hash`. Si posteriormente se escaneaba otro disco con una copia idéntica, el primer archivo carecía de hash rápido y la coincidencia no se detectaba.
* **Requisito:** Comparar y detectar duplicados entre diferentes discos externos y unidades, cruzando candidatos entre todos los volúmenes registrados.
* **Solución Implementada:**
  * En [`scanner.py`](src/redundant_files/scanner.py): la Fase 3 ahora calcula siempre el `quick_hash` (128 KB) para todos los ficheros que cumplen el filtro de tamaño mínimo.
  * La Fase 4 consulta duplicados potenciales en toda la base de datos y resuelve los hashes completos de los ficheros candidatos en todos los discos que estén actualmente montados.
  * Si algún volumen está desconectado, el escáner y los comandos `status`/`duplicates` notifican los candidatos pendientes de verificación.
* **Verificación:** Test `test_cross_volume_duplicate_detection` en [`tests/test_scanner.py`](tests/test_scanner.py).

---

### REQ-07: Compatibilidad y Relleno Rápido de Hashes para Volúmenes Previos

* **Origen / Petición del Usuario:**
  > *"¿y qué pasa con los volumenes que ya he escaneado pero esto no se tuvo en cuenta?"*
* **Problema:** En bases de datos existentes, miles de registros antiguos tenían `quick_hash IS NULL`. Un escaneo posterior no los procesaba porque su fecha de modificación (`mtime`) no había cambiado, a menos que se hiciera un escaneo completo (`--full`) lento.
* **Requisito:** Permitir que los volúmenes antiguos se actualicen de manera instantánea, sin tener que releer ficheros de cientos de gigabytes y preservando los hashes completos que ya existían.
* **Solución Implementada:**
  * Modificación de `_needs_rescan` en [`scanner.py`](src/redundant_files/scanner.py):
    ```python
    def _needs_rescan(self, db_file: dict, fs_modified: float) -> bool:
        if db_file.get('quick_hash') is None:
            return True
        return abs(db_file['file_modified_at'] - fs_modified) > 0.001
    ```
  * En la Fase 3, al rellenar el `quick_hash` se conserva el `full_hash` previo si el fichero no ha sido modificado.
  * El escaneo de un disco con miles de ficheros se completa en apenas 3 a 5 segundos (solo lee 128 KB por archivo en lugar de gigabytes).
* **Verificación:** Test `test_rescan_backfills_missing_quick_hash_and_preserves_full_hash` en [`tests/test_scanner.py`](tests/test_scanner.py).

---

### REQ-08: Identificación Explícita de la Procedencia del Volumen

* **Origen / Petición del Usuario:**
  > *"no se puede ver el volumen de procedencia de cada fichero comparado, solo se ve el path"*
* **Problema:** En los listados de duplicados y en la TUI sólo se visualizaba `:ruta/al/fichero` sin saber a qué disco duro o volumen correspondía cada fichero comparado, debido a una discordancia de alias en las consultas SQL.
* **Requisito:** Visualizar claramente en todo momento el volumen físico de origen de cada fichero (nombre del disco y letra de unidad, ej: `[Elements (K:)]` o `[My Passport (E:)]`) y la ruta absoluta completa.
* **Solución Implementada:**
  * Corrección en [`duplicates.py`](src/redundant_files/duplicates.py) de la lectura de columnas `volume_label` y `volume_serial`.
  * Incorporación de las propiedades `volume_display_name` y `full_path` en las clases `FileInfo`, `FileToDelete` y `FileToKeep`.
  * Actualización de [`ui.py`](src/redundant_files/ui.py) y [`tui_cleanup.py`](src/redundant_files/tui_cleanup.py) para mostrar de forma destacada:
    * En listados: `[Elements (K:)] K:\Películas\archivo.mkv`
    * En la TUI: línea 1 con `[Elements (K:)]`, línea 2 con `Vol: Elements (K:)`, y caja inferior de detalles con `Full Path: [Elements (K:)] K:\...`.
* **Verificación:** Test `test_volume_display_and_provenance` en [`tests/test_database_and_duplicates.py`](tests/test_database_and_duplicates.py).
