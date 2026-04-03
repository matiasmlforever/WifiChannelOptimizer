<p align="center">
  <a href="README.md">🇨🇱 Español</a> |
  <a href="README.us.md">🇺🇸 English</a>
</p>

# 📡 WiFi Channel Optimizer

Optimizador automático de canales Wi-Fi para el **Huawei HG8145X6** (ONT WiFi 6) — probado con **Entel ISP (Chile)**.

Escanea el espectro de RF del entorno, selecciona el canal con menor congestión para las bandas de 2.4 GHz y 5 GHz, y reconfigura el router automáticamente mediante automatización de navegador.

> **Caso de uso principal:** reducir la latencia en **videojuegos multijugador** — las métricas de calidad y la lógica de reversión están calibradas para bajo ping y bajo jitter, no para maximizar velocidad de descarga.

---

## ✨ Características

| Característica | Detalle |
|---|---|
| **Escaneo RF** | Usa `netsh wlan show networks mode=bssid` (nativo de Windows) |
| **Puntaje de congestión** | Suma de potencia en dBm en el canal **y sus canales adyacentes** |
| **Decisión inteligente** | Solo cambia si la mejora supera el **40 %** (normal) o **50 %** (emergencia) |
| **Reglas 2.4 GHz** | Restringe candidatos a canales no solapados: **1, 6, 11** |
| **Reglas 5 GHz** | Prefiere canales no-DFS (36–48, 149–161) para mayor estabilidad |
| **Automatización del router** | Chromium headless vía Playwright — sin intervención manual |
| **Monitoreo gaming-aware** | Mide **RTT al gateway + jitter** antes y después de cada cambio |
| **Reversión automática** | Revierte en 5 min si el jitter o el ping al gateway empeoran |
| **Modo daemon** | Escaneo continuo cada 5 minutos |
| **Modo monitor RF** | Registra snapshots del entorno Wi-Fi en SQLite para análisis de tendencias |
| **Análisis de ventanas** | Detecta las horas de mayor congestión (ventanas de alta carga Wi-Fi) y las escribe en `optimal_windows.json` |
| **Configuración por `.env`** | Credenciales y parámetros fuera del código fuente |

---

## 🎮 Métricas de calidad — por qué importan para gaming

El sistema mide tres métricas **contra el gateway del router** (`192.168.100.1`), no contra un host de internet. Esto aísla el salto Wi-Fi del ruido introducido por el ISP o el backbone.

| Métrica | Qué mide | Por qué importa para gaming |
|---|---|---|
| **RTT al gateway** (`ping_gw_ms`) | Tiempo de ida y vuelta a `192.168.100.1` — solo el salto Wi-Fi | RTT alto al gateway indica que el canal de radio está congestionado. Objetivo: **< 5 ms** |
| **Jitter** (`jitter_ms`) | Desviación estándar de las muestras de RTT (8 pings) | Más dañino que un ping alto pero estable. Causa rubber-banding y fallos de hit-registration. Objetivo: **< 5 ms** |
| **Velocidad de descarga** (`speed_mbps`) | Prueba de 1 MB vía Cloudflare | Señal secundaria — los paquetes de juego pesan < 1 KB, el throughput no afecta la latencia |

### Orden de prioridad para reversión

```
1. Jitter aumentó > JITTER_DEGRADATION_MS    →  revertir  ← más sensible
2. RTT al gateway aumentó > PING_DEGRADATION_MS  →  revertir
3. Velocidad cayó > SPEED_DEGRADATION_PCT    →  revertir  ← menos sensible
```

### Por qué NO se usa ping a 8.8.8.8

Hacer ping a `8.8.8.8` mide el camino completo: salto Wi-Fi + módem + backbone del ISP + red de Google.
Un cambio de canal que genuinamente mejora la radio puede verse "peor" si los servidores de Google están momentáneamente lentos.
Medir el gateway elimina todas las variables excepto el canal en sí.

---

## 🖥️ Requisitos

- Windows 10/11 (requiere `netsh`)
- Python ≥ 3.13
- [`uv`](https://github.com/astral-sh/uv) (recomendado) **o** pip

---

## ⚡ Instalación

### 1 — Clonar el repositorio

```bash
git clone https://github.com/YOUR_USER/WifiChannelOptimizer.git
cd WifiChannelOptimizer
```

### 2 — Crear entorno virtual e instalar dependencias

**Con uv (recomendado):**
```bash
uv venv
uv pip install -e .
```

**Con pip:**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### 3 — Instalar el navegador Chromium de Playwright

```bash
python -m playwright install chromium
```

### 4 — Configurar credenciales

```bash
copy .env.example .env
```

Abre `.env` y completa las credenciales de tu router:

```dotenv
ROUTER_URL=http://192.168.100.1
ROUTER_USER=admin
ROUTER_PASS=TU_CONTRASEÑA_DEL_ROUTER
```

> ⚠️ **Nunca subas `.env` a Git.** Ya está en `.gitignore`.

---

## 🚀 Uso

```bash
# ── Flujo recomendado de 3 pasos ──────────────────────────────────────────

# Paso 1 — Acumular datos del entorno RF (sin tocar el router)
python main.py --monitor --interval 30          # indefinido
python main.py --monitor --interval 30 --duration 86400  # 24 horas

# Paso 2 — Analizar y generar ventanas óptimas
python main.py --analyze                        # UTC-3 (Chile), top 8 horas
python main.py --analyze --tz-offset -3 --top-n 6  # personalizado

# Paso 3 — Ejecutar el optimizer (respetará optimal_windows.json si existe)
python main.py                                  # daemon
python main.py --once                           # una vez

# ── Otros modos ───────────────────────────────────────────────────────────

# Sin restricción de ventana: borrar el archivo generado
# del optimal_windows.json

# Dry run — ciclo completo sin tocar el router
python main.py --once --dry-run

# Modo inspect — abre el navegador visible para depurar selectores
python main.py --inspect
```

### Flags disponibles

| Flag | Descripción |
|---|---|
| *(ninguno)* | Modo daemon — loop continuo, Ctrl+C para detener |
| `--once` | Un ciclo de optimización y termina |
| `--dry-run` | Ciclo completo (escaneo + score + ping) pero **sin cambios en el router** |
| `--inspect` | Abre Chromium visible + guarda archivos HTML de diagnóstico |
| `--monitor` | Modo observatorio — registra snapshots RF en `wifi_monitor.db` |
| `--interval N` | (con `--monitor`) Segundos entre scans. Default: `30` |
| `--duration N` | (con `--monitor`) Detener tras N segundos. Default: ilimitado |
| `--analyze` | Lee `wifi_monitor.db` y escribe las ventanas óptimas en `optimal_windows.json` |
| `--tz-offset N` | (con `--analyze`) Offset UTC en horas. Default: `-3` (Chile) |
| `--top-n N` | (con `--analyze`) Cantidad de horas óptimas a incluir. Default: `8` |

---

## 🧠 Filosofía de diseño del optimizador

Escanear frecuentemente y cambiar de canal con moderación no es una limitación — es una decisión de diseño deliberada basada en tres principios:

### 1. Protección de la NVRAM del router

Cada cambio de canal escribe en la **memoria flash** del router (NVRAM). Aunque los chips modernos soportan cientos de miles de ciclos de escritura, no tiene sentido desgastarlos innecesariamente.

Con `CHANGE_COOLDOWN_SECONDS=3600`, en el peor escenario posible (un entorno RF que cambia constantemente durante las 24 horas), el router recibe **máximo 24 escrituras por día**. Eso es prácticamente nada — tu router vivirá muchos años sin problemas de memoria.

El escaneo con `netsh`, en cambio, ocurre cada `SCAN_INTERVAL_SECONDS` y **no toca el router** — solo lee el espectro RF desde tu PC.

### 2. Evitar el *flapping* (oscilación de canal)

En redes, el **flapping** ocurre cuando un sistema salta del canal A al B, y luego vuelve al A casi de inmediato porque las condiciones fluctuaron levemente.

```
sin cooldown:  ch6 → ch11 → ch6 → ch11 → ch6  (cada 5 min)
con cooldown:  ch6 ────────────────────── ch11  (solo si la mejora persiste 1h)
```

El cooldown de 1 hora le da tiempo al entorno para **estabilizarse**. Si el canal óptimo sigue siendo diferente al cabo de una hora, es porque hay una tendencia real de interferencia — no un ruido pasajero como alguien usando un dispositivo Bluetooth cerca o un microondas encendiéndose.

El umbral del 40% (`HYSTERESIS_THRESHOLD`) refuerza esto: no basta con que otro canal sea *un poco* mejor, tiene que ser *dramáticamente* mejor para justificar el corte.

### 3. Predictibilidad para el usuario

Desde la perspectiva de quien usa la conexión, hay una diferencia enorme entre:

- ❌ Cortes aleatorios de ~5 s cada 5–10 minutos (impredecible, arruina partidas)
- ✅ Un corte de ~5 s **máximo una vez por hora** (manejable, esperable)

Con la configuración por defecto, en el peor caso pierdes conexión **10 segundos, una vez por hora**. En la práctica, los canales cambian mucho menos seguido porque el entorno RF tiende a ser estable durante horas.

### Resumen de la separación scan / change

| Variable | Qué controla | Impacto |
|---|---|---|
| `SCAN_INTERVAL_SECONDS` | Frecuencia del escaneo RF | Solo CPU local, cero impacto en el router |
| `CHANGE_COOLDOWN_SECONDS` | Frecuencia máxima de cambios al router | Protege NVRAM, evita flapping, da predictibilidad |
| `HYSTERESIS_THRESHOLD` | Magnitud mínima de mejora para actuar | Filtra ruido y fluctuaciones momentáneas |

---



| Variable | Default | Descripción |
|---|---|---|
| `ROUTER_URL` | `http://192.168.100.1` | URL del panel de administración del router |
| `ROUTER_USER` | `admin` | Usuario administrador |
| `ROUTER_PASS` | `admin` | Contraseña administrador |
| `ROUTER_DRIVER` | `huawei_hg8145x6` | Driver de automatización a usar (ver [Agregar un router](#-agregar-soporte-para-otro-router)) |
| `SCAN_INTERVAL_SECONDS` | `300` | Segundos entre escaneos RF en modo daemon. Barato — solo usa `netsh`, sin contacto con el router. |
| `CHANGE_COOLDOWN_SECONDS` | `3600` | Tiempo mínimo entre cambios de canal al router (segundos). El escaneo sigue ocurriendo pero no se aplica ningún cambio hasta que expire este cooldown. Previene golpear el router repetidamente. |
| `HYSTERESIS_THRESHOLD` | `0.40` | Mejora relativa mínima para aplicar un cambio en modo normal (0.40 = 40%). |
| `TRIAL_PERIOD_SECONDS` | `300` | Segundos de espera tras un cambio de canal antes de evaluar la calidad. 5 min es suficiente para estabilizarse sin arruinar una partida. |
| `PING_DEGRADATION_MS` | `20` | Aumento de RTT al gateway (ms) que activa una reversión. 20 ms es perceptible en gaming competitivo. |
| `JITTER_DEGRADATION_MS` | `15` | Aumento de jitter (ms) que activa una reversión. 15 ms extra causa rubber-banding en la mayoría de los juegos. |
| `BASELINE_GOOD_PING_MS` | `15` | Si el ping al gateway está por debajo de este valor **y** el jitter también está bien, la conexión ya es buena — no hay nada que mejorar. El optimizer se saltea el ciclo. Evita cambios innecesarios cuando la señal ya es óptima. |
| `BASELINE_GOOD_JITTER_MS` | `5` | Umbral de jitter para la guardia de baseline. Ambas condiciones (ping Y jitter) deben cumplirse para saltearse. |
| `GAMING_PROFILE` | `balanced` | Perfil de emergencia fuera de ventanas. `balanced` usa 40/20/0.50/3600 y `aggressive` usa 30/12/0.35/1800 (ping/jitter/histeresis/cooldown). |
| `EMERGENCY_PING_MS` | `40` | Fuera de ventanas óptimas, solo evalúa cambio si el ping al gateway supera este valor o si el jitter supera su umbral de emergencia. |
| `EMERGENCY_JITTER_MS` | `20` | Umbral de jitter para habilitar acciones en modo emergencia durante horarios fuera de ventana. |
| `EMERGENCY_HYSTERESIS` | `0.50` | Mejora RF mínima en modo emergencia (más estricta que normal para evitar cortes innecesarios en plena partida). |
| `EMERGENCY_COOLDOWN_SECONDS` | `3600` | Cooldown entre cambios de emergencia (1 hora) para reducir flapping y escrituras repetidas. |

---

## 🎯 Perfil: Gaming agresivo (opcional)

Si juegas en horarios fuera de `optimal_windows.json` y prefieres que el optimizer reaccione antes, activa el perfil agresivo con una sola variable:

```dotenv
GAMING_PROFILE=aggressive
```

Efecto esperado: actúa con más facilidad ante degradación nocturna, pero acepta más riesgo de cambios/cortes durante sesiones largas.

Si quieres afinar manualmente, `EMERGENCY_*` siempre tiene prioridad sobre el perfil.

---

## 🛠️ Compatibilidad con routers

Este proyecto fue construido y probado con el **Huawei HG8145X6** ONT provisto por **Entel (Chile)**.

La automatización depende de los siguientes selectores HTML confirmados:

| Elemento | Selector |
|---|---|
| Campo usuario | `#txt_Username` |
| Campo contraseña | `#txt_Password` |
| Botón login | `#loginbutton` (`.nth(1)` — hay dos en el DOM) |
| Menú WLAN | `#name_wlanconfig` |
| Submenú 2.4G Advanced | `#wlan2adv` |
| Submenú 5G Advanced | `#wlan5adv` |
| Dropdown de canal | `#Channel` (mismo ID para ambas bandas, cargado en iframes separados) |
| Botón Apply | `#applyButton` (ejecuta `Submit()`) |
| iframe del panel | `WlanAdvance.asp?2G` / `WlanAdvance.asp?5G` |

### Otras ISPs / variantes de firmware

Si tu ISP (Movistar, Claro, WOM, etc.) tiene una versión de firmware diferente, ejecuta `--inspect` para abrir un navegador visible y encontrar los selectores. Los archivos HTML guardados como `router_*.html` sirven de guía.

---

## 📄 Logs

Cada ejecución agrega al archivo `wifi_optimizer.log`:

```
2026-03-29 21:47:01 [INFO] Current 2.4 GHz channel: ch6
2026-03-29 21:47:01 [INFO] Congestion scores 2.4 GHz: {1: -563.0, 6: -227.0, 11: -222.5}
2026-03-29 21:47:01 [INFO] 2.4 GHz — current: ch6 (-227.0), optimal: ch11 (-222.5), improvement: 2.0% → keep
2026-03-29 21:47:01 [INFO] Already on optimal channels (or within hysteresis). No changes.
2026-03-29 21:47:01 [INFO] Wi-Fi quality → gateway ping: 11.0 ms | jitter: 4.2 ms | speed: 85.3 Mbps
```

---

## 📁 Estructura del proyecto

```
WifiChannelOptimizer/
├── main.py                        # Entry point — carga config, selecciona driver, loop daemon
├── wifi_optimizer/
│   ├── scanner.py                 # Fase 1: escaneo netsh + conversión a dBm
│   ├── decision.py                # Fase 2: scoring de congestión, histéresis, selección de canal
│   ├── quality.py                 # Métricas gaming: RTT al gateway, jitter, velocidad
│   ├── optimizer.py               # Ciclo principal: orquesta las 3 fases + driver del router
│   ├── monitor.py                 # Modo observatorio: registra snapshots RF en SQLite
│   ├── analyzer.py                # Análisis de ventanas: lee DB, escribe optimal_windows.json
│   └── routers/
│       ├── base.py                # BaseRouter ABC — contrato que todo driver debe implementar
│       └── huawei_hg8145x6.py    # Driver concreto para Huawei HG8145X6 (Entel, Chile)
├── analyze_windows.py             # Wrapper standalone para --analyze
├── .env.example                   # Plantilla de configuración — copiar a .env y completar
├── .env                           # Credenciales locales (ignorado por git)
├── pyproject.toml                 # Metadatos del proyecto y dependencias
├── PROMPT.md                      # Instrucciones para agentes y especificación de reglas de negocio
├── .gitignore
├── README.md                      # Este archivo (Español)
├── README.us.md                   # English version
├── wifi_optimizer.log             # Log de ejecución (ignorado por git)
├── wifi_monitor.db                # Base de datos del monitor RF (ignorado por git)
└── optimal_windows.json           # Ventanas horarias óptimas generadas por --analyze (ignorado por git)
```

---

## 🔄 Flujo completo: monitor → analizar → optimizar

Este es el flujo recomendado para que el optimizer actúe **solo cuando tiene sentido hacerlo**.

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│  --monitor      │────▶│  --analyze       │────▶│  (daemon / --once)   │
│                 │     │                  │     │                      │
│  wifi_monitor   │     │  identifica las  │     │  Step 0: ¿estamos    │
│  .db (SQLite)   │     │  horas de MAYOR  │     │  en hora de alta     │
│                 │     │  congestión →    │     │  congestión? Si no   │
│                 │     │  optimal_windows │     │  → skip              │
│                 │     │  .json           │     │  Step 2.5: ¿baseline │
│                 │     │                  │     │  ya es bueno? → skip │
└─────────────────┘     └──────────────────┘     └──────────────────────┘
```

> **¿Por qué actuar en horas de ALTA congestión?**
> Cuando hay mucha interferencia en el canal actual, cambiar a uno menos poblado
> produce una mejora real y medible. En horas tranquilas la conexión ya está bien
> — cambiar de canal ahí es innecesario y puede empeorar lo que funciona.
> Esto está validado empíricamente: los 3 reverts del log ocurrieron exactamente
> en ciclos donde el baseline previo ya era bueno (ping < 30 ms, jitter < 26 ms).

### Paso 1 — Acumular datos (24–48 h mínimo recomendado)

```bash
python main.py --monitor --interval 30
```

Cuantos más días de datos, más representativo el análisis. Sin datos de fines de semana o días laborales no hay un patrón completo.

### Paso 2 — Analizar y generar ventanas

```bash
python main.py --analyze --tz-offset -3 --top-n 8
```

Genera `optimal_windows.json` con las **8 horas más congestionadas** del día. Son las horas donde hay más interferencia de vecinos — y por lo tanto más que ganar al cambiar a un canal libre.

### Paso 3 — Ejecutar el optimizer

```bash
python main.py          # daemon — escanea siempre, actúa solo en ventanas de alta congestión
```

El optimizer aplica dos filtros antes de tocar el router:

```
Step 0:   ¿Estamos en una hora de alta congestión? (según optimal_windows.json)
          Si no → skip. La conexión probablemente está bien.

Step 2.5: ¿El baseline actual es ping ≤ 15 ms Y jitter ≤ 5 ms?
          Si sí → skip. La conexión ya es buena, no hay nada que mejorar.

Si pasa ambos filtros → evalúa canales y aplica si la mejora RF ≥ 40% (modo normal).

Fuera de `optimal_windows.json` entra en **modo emergencia**: solo actúa si hay degradación seria (ping > 40 ms o jitter > 20 ms), exige mejora RF ≥ 50% y respeta cooldown de 1 hora.
```

```
[INFO] Within optimal window (03:00 ✅). Proceeding with cycle.
[INFO] Wi-Fi quality → gateway ping: 45.0 ms  jitter: 28.0 ms  → PROCEDE
[INFO] 2.4 GHz — improvement: 91.9% (threshold 40%) → CHANGE

[INFO] Within optimal window (03:00 ✅). Proceeding with cycle.
[INFO] Wi-Fi quality → gateway ping: 4.0 ms  jitter: 1.6 ms
[INFO] Baseline already good (ping 4.0 ms ≤ 15 ms, jitter 1.6 ms ≤ 5 ms). Skipping.
```

### Desactivar la restricción de ventana

```bash
del optimal_windows.json    # Windows
rm optimal_windows.json     # macOS/Linux
```

Sin el archivo, el optimizer actúa en cualquier hora (comportamiento original).

---

## 📡 Modo monitor RF

El modo monitor es completamente independiente del optimizador — **no toca el router, no requiere credenciales, no hace ningún cambio**. Solo escanea y registra.

### Cuándo usarlo

- Para **entender el entorno RF** antes de habilitar el optimizador
- Para detectar **patrones horarios** de congestión (¿los vecinos saturan el canal 6 de noche?)
- Para **validar** que un cambio de canal manual o automático tuvo efecto real
- Para tener **evidencia histórica** ante problemas de conectividad

### Base de datos SQLite (`wifi_monitor.db`)

Tabla `snapshots`:

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK autoincremental |
| `ts` | TEXT | Timestamp ISO-8601 UTC |
| `ssid` | TEXT | Nombre de la red |
| `bssid` | TEXT | MAC del punto de acceso |
| `channel` | INTEGER | Canal Wi-Fi |
| `band` | TEXT | `'2.4'` o `'5'` |
| `signal_pct` | INTEGER | Señal en % (valor crudo de netsh) |
| `signal_dbm` | REAL | Señal en dBm (`pct/2 - 100`) |

### Consultas de ejemplo

```python
import sqlite3
import pandas as pd

con = sqlite3.connect("wifi_monitor.db")
df  = pd.read_sql("SELECT * FROM snapshots", con, parse_dates=["ts"])

# Congestión promedio por canal
df.groupby(["channel", "band"])["signal_dbm"].mean()

# Evolución de señal de una red específica en el tiempo
df[df["ssid"] == "MiVecino"].set_index("ts")["signal_dbm"].plot()

# Hora del día con mayor cantidad de redes activas
df["hour"] = df["ts"].dt.hour
df.groupby("hour")["bssid"].nunique().plot(kind="bar", title="Redes únicas por hora")
```

```sql
-- Top 5 canales más congestionados (promedio dBm)
SELECT channel, band, COUNT(*) as scans, AVG(signal_dbm) as avg_dbm
FROM snapshots
GROUP BY channel, band
ORDER BY avg_dbm ASC
LIMIT 5;
```

```sql
-- Redes únicas activas por hora del día
-- Equivalente SQL de: df.groupby("hour")["bssid"].nunique()
SELECT
    CAST(strftime('%H', ts) AS INTEGER)  AS hour,
    COUNT(DISTINCT bssid)                AS unique_networks
FROM snapshots
GROUP BY hour
ORDER BY hour;
```

```sql
-- ─────────────────────────────────────────────────────────────────
-- Ventanas horarias óptimas para ejecutar el optimizador
-- (hora local Chile = UTC-3, ajustar TZ_OFFSET según tu zona)
-- Score combinado: suma de dBm en 2.4 GHz + 5 GHz por hora
-- MÁS negativo = más congestionado = mejor momento para actuar
-- (hay interferencia real → hay canal libre al que escapar)
-- ─────────────────────────────────────────────────────────────────
WITH tz_offset AS (SELECT -3 AS offset),   -- Chile (UTC-3)

congestion_by_hour AS (
    SELECT
        (CAST(strftime('%H', ts) AS INTEGER) + (SELECT offset FROM tz_offset) + 24) % 24
                                                        AS local_hour,
        band,
        SUM(signal_dbm)                                 AS band_score
    FROM snapshots
    GROUP BY local_hour, band
),

combined AS (
    SELECT
        local_hour,
        SUM(band_score)                                 AS combined_score,
        SUM(CASE WHEN band = '2.4' THEN band_score END) AS score_24,
        SUM(CASE WHEN band = '5'   THEN band_score END) AS score_5
    FROM congestion_by_hour
    GROUP BY local_hour
)

SELECT
    local_hour                          AS hora,
    ROUND(combined_score, 1)            AS score_combinado,
    ROUND(score_24, 1)                  AS score_24ghz,
    ROUND(score_5,  1)                  AS score_5ghz,
    RANK() OVER (ORDER BY combined_score ASC) AS ranking  -- más negativo = rank 1
FROM combined
ORDER BY ranking;
```

> **Resultado con los datos acumulados (Chile):**
>
> | Rank | Hora | Observación |
> |---|---|---|
> | 🥇 1 | **02:00** | Mayor congestión del día — mejor ventana para actuar |
> | 🥈 2 | **04:00** | Segunda más congestionada |
> | 🥉 3 | **03:00** | Tercera más congestionada |
> | — | 12:00–21:00 | **Horas tranquilas** — conexión ya está bien, no cambiar |
>
> La franja **02:00–10:00 hora Chile** concentra la mayor congestión RF.
> Son las horas donde cambiar a un canal menos poblado produce una mejora real.

---

## 🔌 Agregar soporte para otro router

El núcleo del optimizador (`optimizer.py`) nunca habla con el router directamente — solo llama dos métodos sobre una instancia de `BaseRouter`. Todo lo específico del router está aislado en un único archivo de driver.

### El contrato de `BaseRouter`

Todo driver debe implementar exactamente dos métodos:

```python
def read_channels(self) -> tuple[int | None, int | None]:
    """
    Hacer login, leer el canal activo en cada banda, cerrar sesión y retornar
    (canal_2_4ghz, canal_5ghz). Retornar None si una banda no pudo leerse.
    Se llama una vez al inicio para inicializar el estado de histéresis.
    """

def apply_channels(
    self,
    channel_24: int | None,
    channel_5:  int | None,
    *,
    headed: bool = False,
) -> None:
    """
    Hacer login, configurar los canales solicitados, confirmar el cambio, cerrar sesión.
    Pasar None en una banda para dejarla sin cambios.
    headed=True abre el navegador visible (activado por --inspect).
    """
```

La clase base también provee `self.url`, `self.username`, `self.password` y la propiedad `gateway_host` (la IP LAN derivada de `self.url`) que usa el monitor de calidad para medir ping/jitter.

---

### Guía paso a paso

#### Paso 1 — Descubrir los selectores con `--inspect`

```bash
python main.py --inspect
```

Esto abre una **ventana visible de Chromium** y guarda cuatro snapshots HTML en la raíz del proyecto:

| Archivo | Cuándo se guarda |
|---|---|
| `router_login_page.html` | Inmediatamente al navegar a `ROUTER_URL` |
| `router_post_login.html` | Tras un login exitoso |
| `router_wlan24.html` | Al abrir el panel de configuración 2.4 GHz |
| `router_wlan5.html` | Al abrir el panel de configuración 5 GHz |

Abre estos archivos en el navegador y usa DevTools (`F12 → Inspector`) para encontrar:

- Los **IDs de los campos** usuario y contraseña
- El **botón de login** (ID o tipo)
- La **ruta de navegación** hasta la configuración de canal Wi-Fi (ítems de menú, links, iframes)
- El **selector del dropdown de canal** (`<select>` o `<input>`)
- El **ID del botón Apply / Guardar**

> **Tip:** muchos routers cargan el panel de configuración dentro de un `<iframe>` oculto.
> Revisa `page.frames` en el log — el driver debe apuntar al frame correcto.

---

#### Paso 2 — Crear el archivo del driver

```python
# wifi_optimizer/routers/mi_router.py
"""
Driver para <Marca> <Modelo> — <ISP, País>.

Selectores confirmados (versión de firmware X.Y.Z):
  Login:    #username, #password, #loginBtn
  Canal:    select#ch_2g (2.4 GHz), select#ch_5g (5 GHz)
  Apply:    #btnApply
"""
from __future__ import annotations
import logging
from playwright.sync_api import sync_playwright
from .base import BaseRouter

log = logging.getLogger(__name__)


class MiRouter(BaseRouter):

    def read_channels(self) -> tuple[int | None, int | None]:
        ch24 = ch5 = None
        try:
            with sync_playwright() as p:
                page = self._open(p)
                self._login(page)

                page.click("#wifiMenu")
                page.wait_for_timeout(1_500)

                ch24 = int(page.locator("select#ch_2g").input_value())
                log.info("Canal actual 2.4 GHz: ch%s", ch24)

                ch5 = int(page.locator("select#ch_5g").input_value())
                log.info("Canal actual 5 GHz: ch%s", ch5)

                page.context.browser.close()
        except Exception as exc:
            log.warning("No se pudieron leer los canales: %s", exc)
        return ch24, ch5

    def apply_channels(
        self,
        channel_24: int | None,
        channel_5:  int | None,
        *,
        headed: bool = False,
    ) -> None:
        if channel_24 is None and channel_5 is None:
            return
        try:
            with sync_playwright() as p:
                page = self._open(p, headless=not headed)
                self._login(page)

                page.click("#wifiMenu")
                page.wait_for_timeout(1_500)

                if channel_24 is not None:
                    page.locator("select#ch_2g").select_option(str(channel_24))
                    log.info("2.4 GHz → ch%s", channel_24)

                if channel_5 is not None:
                    page.locator("select#ch_5g").select_option(str(channel_5))
                    log.info("5 GHz → ch%s", channel_5)

                page.click("#btnApply")
                page.wait_for_timeout(3_000)   # esperar reinicio de la radio
                page.context.browser.close()
        except Exception as exc:
            log.error("Error en la automatización del router: %s", exc)

    def _open(self, p, *, headless: bool = True):
        browser = p.chromium.launch(headless=headless)
        page    = browser.new_context(ignore_https_errors=True).new_page()
        page.set_default_timeout(20_000)
        return page

    def _login(self, page) -> None:
        page.goto(self.url, wait_until="domcontentloaded")
        page.wait_for_selector("#username", state="visible", timeout=10_000)
        page.fill("#username", self.username)
        page.fill("#password", self.password)
        page.click("#loginBtn")
        # Esperar un elemento conocido post-login en vez de networkidle —
        # la mayoría de las UIs de routers tienen polling JS que impide networkidle.
        page.wait_for_selector("#wifiMenu", state="visible", timeout=15_000)
        log.info("Login exitoso.")
```

> **Importante:** evitar `wait_for_load_state("networkidle")` — las UIs de routers casi siempre
> tienen polling JS en segundo plano que impide que este estado se alcance.
> Usar `wait_for_selector` sobre un elemento conocido post-login.

---

#### Paso 3 — Registrar el driver en `main.py`

```python
# main.py
from wifi_optimizer.routers.mi_router import MiRouter   # ← agregar import

ROUTER_DRIVERS = {
    "huawei_hg8145x6": HuaweiHG8145X6,
    "mi_router":        MiRouter,                        # ← agregar entrada
}
```

#### Paso 4 — Seleccionarlo en `.env`

```dotenv
ROUTER_DRIVER=mi_router
ROUTER_URL=http://192.168.1.1
ROUTER_USER=admin
ROUTER_PASS=tu_contraseña
```

#### Paso 5 — Probarlo

```bash
# Verificar que read_channels funciona
python main.py --once --dry-run

# Verificar que apply_channels funciona (navegador visible)
python main.py --inspect
```

#### Paso 6 — Documentar los selectores en `README.md`

Agrega una fila a la tabla de **Compatibilidad con routers** y lista tus selectores confirmados. Esto ayuda a otros usuarios con el mismo router o ISP.

---

### Problemas comunes

| Problema | Causa probable | Solución |
|---|---|---|
| Botón de login no encontrado | Múltiples botones en el DOM, solo uno visible | Usar `.nth(1)` o `.filter()` para apuntar al visible |
| Dropdown de canal no encontrado | El panel carga dentro de un `<iframe>` | Iterar `page.frames` y apuntar al frame que contiene el dropdown |
| Timeout tras el login | El router nunca alcanza `networkidle` por polling JS | Usar `wait_for_selector` sobre un elemento post-login conocido |
| Apply no hace nada | El submit ejecuta JS, no un submit de formulario real | Usar `page.click("#applyBtn")` — evitar `page.evaluate("form.submit()")` |
| La conexión se cae tras Apply | Comportamiento esperado al reiniciar la radio | Envolver la espera post-apply en `try/except` y loguear como informativo |

---

## 📜 Licencia

MIT

---

## ☕ Apoya el proyecto

Si este script te ayudó a estabilizar tu conexión, bajar el ping o simplemente te ahorró el dolor de cabeza de configurar el router manualmente, considera apoyarme para seguir mejorando esta herramienta y agregar soporte a más modelos.

### 🇨🇱 Para Chile (Webpay / Débito / Crédito)
Puedes realizar una donación de monto abierto a través de **Flow**:

[![Flow Pago](https://img.shields.io/badge/Donar_vía-Flow-00c0f3?style=for-the-badge&logo=opsgenie&logoColor=white)](https://www.flow.cl/btn.php?token=b7cbabd1861bcf3cbbb6bf9b8cf05af15cce8fc)

### 🌎 Internacional (PayPal / Tarjeta de crédito)
Apoya el desarrollo vía **Buy Me a Coffee**:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy_Me_a_Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/matiasmlforever)

> **Tip:** También puedes usar el botón **Sponsor** en la parte superior del repositorio para donaciones recurrentes vía GitHub Sponsors.
