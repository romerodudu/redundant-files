# Manual de Usuario de Redundant Files

**Redundant Files** es una herramienta de consola de alto rendimiento diseñada para detectar, comparar y gestionar archivos duplicados en Windows a través de múltiples volúmenes de almacenamiento (discos duros internos, unidades USB externas y discos extraíbles).

---

## Índice

1. [Conceptos Básicos y Arquitectura](#1-conceptos-básicos-y-arquitectura)
2. [Instalación y Requisitos](#2-instalación-y-requisitos)
3. [Configuración (`config.yaml`)](#3-configuración-configyaml)
4. [Escaneo de Unidades (`scan`)](#4-escaneo-de-unidades-scan)
5. [Detección de Duplicados (`duplicates`)](#5-detección-de-duplicados-duplicates)
6. [Limpieza Interactiva con Interfaz TUI (`cleanup`)](#6-limpieza-interactiva-con-interfaz-tui-cleanup)
7. [Modos de Borrado y Estrategias](#7-modos-de-borrado-y-estrategias)
8. [Gestión de Duplicados Ignorados (`ignored`)](#8-gestión-de-duplicados-ignorados-ignored)
9. [Gestión de Volúmenes y Reseteo (`volumes`, `reset`, `status`)](#9-gestión-de-volúmenes-y-reseteo-volumes-reset-status)
10. [Preguntas Frecuentes y Consejos](#10-preguntas-frecuentes-y-consejos)

---

## 1. Conceptos Básicos y Arquitectura

Para procesar terabytes de datos en pocos minutos sin saturar la memoria RAM ni colapsar los discos duros, **Redundant Files** divide el proceso en un pipeline optimizado de 4 fases:

```
┌────────────────────────────────────────────────────────┐
│ Fase 1: Enumeración rápida en disco (os.scandir)       │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Fase 2: Detección incremental de cambios (mtime/hashes)│
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Fase 3: Quick Hash (128 KB: inicio + final del archivo)│
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Fase 4: Full Hash (BLAKE3 en bloques con progreso)     │
└────────────────────────────────────────────────────────┘
```

1. **Fase 1: Enumeración rápida:** Lee la estructura del disco mediante `os.scandir` en pila para máxima velocidad en sistemas NTFS/FAT32.
2. **Fase 2: Filtro incremental:** Si un fichero no ha cambiado su fecha de modificación respecto a la base de datos y ya tiene sus hashes calculados, se salta instantáneamente.
3. **Fase 3: Quick Hash (128 KB):** Lee únicamente los primeros 64 KB y los últimos 64 KB de cada archivo. Esto permite descartar archivos de igual tamaño pero distinto contenido sin leerlos por completo.
4. **Fase 4: Full Hash (BLAKE3):** Solo los ficheros que coinciden en tamaño y en `quick_hash` se leen por completo para certificar al 100% que son idénticos.

> [!NOTE]
> **Identificación de Volúmenes:** Aunque desconectes un disco USB y al volverlo a conectar Windows le asigne otra letra de unidad (por ejemplo de `E:` a `F:`), **Redundant Files** lo reconoce automáticamente gracias a su número de serie Win32 y UUID persistente.

---

## 2. Instalación y Requisitos

### Requisitos del Sistema
* **Sistema Operativo:** Windows 10 / 11 (64-bit) o Windows Server.
* **Python:** Versión 3.11 o superior.

### Instalación Rápida

1. Abre tu terminal de PowerShell en el directorio del proyecto:
   ```powershell
   cd d:\Projects\redundant-files
   ```
2. Instala las dependencias y la herramienta en modo editable:
   ```powershell
   pip install -e .
   ```
   O bien mediante el archivo de requisitos:
   ```powershell
   pip install -r requirements.txt
   ```
3. Comprueba que el comando está disponible:
   ```powershell
   redundant-files --help
   ```

---

## 3. Configuración (`config.yaml`)

El sistema busca el archivo de configuración en el siguiente orden de prioridad:
1. `./config.yaml` o `./config.yml` en la **carpeta actual donde ejecutes el comando**.
2. Archivo global de usuario en `~/.redundant-files/config.yaml`.

### Ejemplo de Configuración Recomendada

Crea o edita un archivo `config.yaml` en la raíz de tu proyecto o directorio de trabajo:

```yaml
# Tamaño mínimo de fichero para ser considerado (evita indexar ficheros pequeños/temporales)
# 504857600 bytes = ~481 MB (ideal para películas, ISOs, máquinas virtuales)
# 10485760 bytes = 10 MB (recomendado para librerías generales)
min_file_size: 524288000 # 500 MB

# Carpetas a ignorar (coincidencia insensible a mayúsculas/minúsculas)
exclude_dirs:
  - "$RECYCLE.BIN"
  - "System Volume Information"
  - ".git"
  - "node_modules"
  - "AppData"

# Extensiones excluidas
exclude_extensions:
  - "tmp"
  - "log"
  - "bak"

# Patrones glob para rutas
exclude_patterns:
  - "**/cache/*"
  - "**/temp/*"
```

---

## 4. Escaneo de Unidades (`scan`)

### Escanear una unidad o carpeta
Para escanear un disco completo o una carpeta específica:

```powershell
# Escanear el disco externo E:
redundant-files scan E:

# Escanear una subcarpeta concreta
redundant-files scan E:\Peliculas
```

### Escaneo Incremental y Rescates Interrumpidos
* **Reanudar escaneos interrumpidos:** Si pulsas `Ctrl+C` durante el escaneo, el progreso se guarda en SQLite. Al volver a ejecutar el comando, se reanuda automáticamente sin perder el trabajo anterior.
* **Actualización ultrarrápida de volúmenes antiguos:** Si ya habías escaneado un disco con versiones anteriores, al lanzar un escaneo normal el sistema rellenará los `quick_hash` faltantes en unos pocos segundos (lee solo 128 KB por archivo en lugar de gigabytes).
* **Forzar rescaneo completo:** Si deseas recalcular los hashes de absolutamente todos los archivos desde cero:
  ```powershell
  redundant-files scan --full E:
  ```

---

## 5. Detección de Duplicados (`duplicates`)

Una vez escaneadas tus unidades, visualiza los duplicados encontrados:

```powershell
# Listar todos los grupos de duplicados encontrados
redundant-files duplicates

# Filtrar por tamaño mínimo (ejemplo: solo archivos mayores a 1 GB)
redundant-files duplicates --min-size 1GB
```

### Interpretación de la Salida

La tabla muestra claramente el **volumen de procedencia** y la **ruta completa** de cada copia:

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Group # ┃ Hash (short) ┃     Size ┃ Count ┃   Wasted ┃ Files (Volume | Path)                          ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│       1 │ 7d493690     │  1.20 GB │     2 │  1.20 GB │ [Elements (K:)] K:\Peliculas\Matrix (1999).mkv │
│         │              │          │       │          │ [My Passport (E:)] E:\Backup\Matrix.mkv        │
└─────────┴──────────────┴──────────┴───────┴──────────┴────────────────────────────────────────────────┘

Notice: 4 candidate duplicate files on other volumes need full hash verification:
  • Elements (J:): 4 files waiting. (Insert volume and scan to verify)
```

> [!TIP]
> Si recibes un aviso de duplicados pendientes en otros volúmenes, significa que un fichero coincide en tamaño y en `quick_hash` con otro disco desconectado. Al conectar ese disco y lanzar un escaneo rápido, la coincidencia se verificará al 100%.

---

## 6. Limpieza Interactiva con Interfaz TUI (`cleanup`)

La herramienta incluye una interfaz gráfica para terminal (TUI) basada en curses a pantalla completa que te permite decidir exactamente qué archivos borrar y cuáles conservar.

Lanza la interfaz con:
```powershell
redundant-files cleanup
```

### Pantalla Dual-Panel de la TUI

```
┌─── Duplicate Groups ───────────────┬─── Files in Group 1 (Size: 1.20 GB) ──────────────────────────┐
│ > 1.  1.20 GB (2) [1 del]          │ > [*] (DEL)  [Elements (K:)] Matrix (1999).mkv                │
│   2. 850.0 MB (3) [skip]           │        Vol: Elements (K:) | Folder: Peliculas | 1.2 GB | ...  │
│   3. 512.0 MB (2) [IGN]            │   [ ] (KEEP) [My Passport (E:)] Matrix.mkv                    │
│                                    │        Vol: My Passport (E:) | Folder: Backup | 1.2 GB | ...  │
├────────────────────────────────────┴───────────────────────────────────────────────────────────────┤
│ Full Path: [Elements (K:)] K:\Peliculas\Matrix (1999).mkv                                          │
│ Status: [MARKED FOR DELETION] | Volume: Elements (K:) | Size: 1.20 GB | Modified: 2024-05-10       │
├────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Delete: 1 files (1.20 GB) | Ignored: 1 groups | [Tab] Switch | [Space] Toggle | [Enter] Confirm    │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Atajos de Teclado de la TUI

| Tecla / Atajo | Acción Realizada |
| :--- | :--- |
| **`[↑ / ↓]`** o **`[j / k]`** | Subir / Bajar en la lista activa (grupos o ficheros). |
| **`[Tab]`** | Alternar el foco entre el panel de Grupos (izq) y el de Ficheros (der). |
| **`[← / →]`** o **`[h / l]`** | En el panel derecho: **Desplazamiento horizontal (scroll)** para leer nombres largos o rutas completas. |
| **`[Espacio]`** | **Marcar / Desmarcar:** En el panel de ficheros alterna entre `[ ] (KEEP)` y `[*] (DEL)`. |
| **`[a]`** | **Autoselección de grupo:** Conserva la copia más antigua y marca las demás para borrar. |
| **`[A]`** | **Autoselección global:** Aplica la autoselección a **todos los grupos** de la lista. |
| **`[i]`** | **Ignorar grupo:** Marca el grupo como duplicado intencionado (no volverá a aparecer). |
| **`[s]`** | **Resetear grupo:** Limpia las marcas de borrado del grupo activo (`[skip]`). |
| **`[Enter]`** o **`[c]`** | **Confirmar:** Sale de la interfaz y pasa a la pantalla de resumen previa al borrado. |
| **`[q]`** o **`[Esc]`** | **Cancelar:** Sale sin realizar ninguna modificación ni borrado. |

### Medida de Seguridad
El sistema **impide físicamente marcar todas las copias de un archivo para borrar**. Siempre debe quedar al menos una copia conservada con el estado `(KEEP)`.

---

## 7. Modos de Borrado y Estrategias

Tras pulsar `[Enter]` en la TUI, se muestra una **tabla resumen pre-borrado** detallando cada archivo a eliminar frente a la copia que se conservará:

```text
Files Selected for Deletion:
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ File to Delete (Volume | Path)         ┃     Size ┃ Kept Copy (Volume | Path)              ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ [Elements (K:)] K:\Peliculas\Matrix.mkv│  1.20 GB │ [My Passport (E:)] E:\Backup\Matrix.mkv│
└───┴────────────────────────────────────────┴──────────┴────────────────────────────────────────┘
Cleanup Plan Summary:
Files to delete: 1 | Space to save: 1.20 GB | Volumes involved: 2

Proceed with deletion? [y/N]:
```

### Estrategia 1: Borrado Directo (Por Defecto)
Si los discos están conectados, al responder `y` los archivos marcados se eliminan inmediatamente del disco y de la base de datos.

### Estrategia 2: Borrado Seguro en Dos Fases (`--two-pass`)
Si gestionas múltiples discos duros externos y no quieres conectar todos a la vez:

1. **Fase 1 (Mover a cuarentena):**
   ```powershell
   redundant-files cleanup --two-pass
   ```
   En lugar de borrar los archivos, se mueven a una carpeta `DUPLICATED/` dentro de cada disco, acompañados de un archivo `.info.txt` con la procedencia y el hash.
2. **Fase 2 (Vaciado definitivo):**
   ```powershell
   redundant-files cleanup --pass2
   ```
   El sistema te guiará ordenadamente pidiéndote conectar un disco cada vez, minimizando los intercambios de discos USB y liberando el espacio permanentemente.

---

## 8. Gestión de Duplicados Ignorados (`ignored`)

Si tienes duplicados intencionados (por ejemplo, copias de seguridad deliberadas de archivos clave), puedes marcarlos para que no aparezcan en futuros escaneos ni limpiezas:

```powershell
# Listar todos los grupos ignorados actualmente
redundant-files ignored

# Menú interactivo con checkboxes para des-ignorar grupos específicos
redundant-files ignored --manage

# Borrar toda la lista de ignorados
redundant-files ignored --clear
```

---

## 9. Gestión de Volúmenes y Reseteo (`volumes`, `reset`, `status`)

### Consultar Estado Global
```powershell
redundant-files status
```
Muestra el recuento de volúmenes, total de archivos indexados, tamaño global, espacio desperdiciado en duplicados y candidatos pendientes de verificación.

### Listar Volúmenes Conocidos
```powershell
redundant-files volumes
```
Muestra todos los discos indexados con su etiqueta, número de serie Win32, letra de unidad asignada y última fecha de escaneo.

### Resetear Información de un Volumen (Solo Base de Datos)
Si formateaste un disco o deseas vaciar sus registros sin tocar ningún archivo físico en el disco:

```powershell
# Limpiar los ficheros indexados del volumen ID 1
redundant-files reset 1

# Limpiar los ficheros y además olvidar el volumen por completo
redundant-files reset 1 --remove-volume
```

---

## 10. Preguntas Frecuentes y Consejos

### ¿Qué pasa si cambio la letra de unidad en Windows?
No hay problema. **Redundant Files** identifica el disco mediante su número de serie físico. En cuanto ejecutes `redundant-files scan <nueva_letra>`, actualizará la letra de unidad automáticamente manteniendo todo su historial.

### ¿Se pueden comparar discos que no están conectados a la vez?
Sí. El sistema almacena los hashes de todos los archivos escaneados. Cuando escaneas el disco A y luego el disco B, la herramienta detecta la coincidencia por `quick_hash`. Para la verificación final del `full_hash`, el comando `duplicates` o `status` te indicará qué volúmenes conectar.

### ¿Cómo optimizar la velocidad con miles de archivos?
Asegúrate de ajustar `min_file_size` en tu `config.yaml`. Para colecciones multimedia o backups pesados, establecer `min_file_size: 104857600` (100 MB) o `524288000` (500 MB) acelerará drásticamente el escaneo descartando millones de archivos pequeños insignificantes.
