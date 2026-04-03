# WiFi Channel Auto-Optimizer - Technical Specification

---

## 🤖 Agent Instructions (read before making any change)

> These rules apply to every AI agent or automated tool working on this codebase.
> They are not optional — treat them as hard constraints.

### 1. Document every business rule change in README.md

Whenever you modify logic that affects **how the system makes decisions**, you must update `README.md` in the same response. This includes, but is not limited to:

- Congestion scoring algorithm (`compute_congestion_scores`, `_adjacent_channels`)
- Channel selection rules (`CHANNELS_24`, `CHANNELS_5_PREFERRED`, hysteresis threshold)
- Quality metrics used for revert decisions (`measure_ping_ms`, `measure_jitter_ms`, `_quality_degraded`)
- Revert trigger thresholds (`PING_DEGRADATION_MS`, `JITTER_DEGRADATION_MS`, `SPEED_DEGRADATION_PCT`)
- Any new `.env` variable that controls behavior

### 2. Document every new `.env` variable in both `README.md` and `.env.example`

If you add a new configurable parameter, it must appear in:
- `.env.example` — with a sensible default and an inline comment explaining its purpose and gaming impact
- `README.md` — in the **Configuration reference** table

### 3. Never hardcode credentials or environment-specific values in `main.py`

All router credentials, URLs, timeouts, and tuning thresholds must come from `os.getenv()`. Defaults in code are acceptable only as fallbacks.

### 4. Keep the primary use case visible

This project exists to **reduce gaming latency**, not to maximize throughput. If you change a metric or threshold, explain in the commit message and in `README.md` why the change is better for low-latency gaming.

---

## Project Overview
Este proyecto consiste en un sistema de automatización en Python diseñado para monitorear la saturación de los canales Wi-Fi (2.4GHz y 5GHz) en el entorno local y reconfigurar automáticamente un router **Huawei HG8145X6** para utilizar los canales con menor interferencia.

## 1. Environment & Stack
- **Language:** Python 3.13.x
- **Package Manager:** `uv`
- **Browser Automation:** Playwright (Headless mode)
- **Host OS:** Windows (Execution via PowerShell/CMD)
- **Target Hardware:** Huawei HG8145X6 (ONT WiFi 6)

## 2. Phase 1: WiFi Environment Scanner
El sistema debe obtener datos del espectro radioeléctrico utilizando el comando nativo de Windows:
`netsh wlan show networks mode=bssid`

### Requirements:
- Parsear la salida de texto para extraer: SSID, BSSID, Signal (%), y Channel.
- **Signal Conversion:** Convertir el porcentaje de calidad de `netsh` a $dBm$ usando la fórmula:
  $$dBm = \frac{\text{calidad}}{2} - 100$$
- **Congestion Scoring:** No se debe elegir el canal basándose solo en la cantidad de redes. El puntaje de congestión de un canal será la suma de las potencias ($dBm$) de todas las redes detectadas en ese canal y sus adyacentes.

## 3. Phase 2: Decision Logic (Optimization)
- **2.4 GHz Rules:** Solo se permiten los canales **1, 6 y 11** (Non-overlapping).
- **5 GHz Rules:** Priorizar canales de banda baja (36-48) o alta (149-161), evitando canales DFS si la estabilidad es prioridad.
- **Hysteresis (modo normal):** El sistema solo debe solicitar un cambio de canal si la mejora de congestión es superior al **40%** respecto al canal actual, para evitar reinicios innecesarios de la radio.
- **Emergency mode (fuera de ventanas óptimas):** solo evaluar cambios cuando exista degradación seria (ping o jitter altos), con histéresis más estricta (**50%**) y cooldown de **1 hora** para evitar flapping.
- **Selector de perfil:** `GAMING_PROFILE=balanced|aggressive` permite ajustar rápidamente la sensibilidad de emergencia; cualquier `EMERGENCY_*` explícito en `.env` tiene prioridad sobre el perfil.

## 4. Phase 3: Router Automation (Playwright)
**Target URL:** `http://192.168.100.1` (o IP asignada por el ISP).

### Implementation Details:
- **Login:** Interactuar con los selectores `#txt_Username` y `#txt_Password`.
- **Navigation:** Acceder a `Advanced` > `Wi-Fi` > `2.4G/5G Basic Settings`.
- **Action:** Cambiar el valor de los dropdowns de canal y ejecutar el submit.
- **Disconnection Handling:** El script debe capturar y silenciar el error de "Timeout" o "Connection Reset" que ocurrirá inevitablemente cuando el router aplique los cambios y reinicie la radio Wi-Fi.

## 5. Safety & Constraints
- **Execution Window:** El script debe permitir programar una ventana de mantenimiento (ej. 03:00 - 05:00 AM) para evitar cortes durante horas de trabajo(opcional).
- **Logging:** Registrar en un archivo `.log` el canal anterior, el nuevo canal seleccionado y el mapa de calor de interferencia detectado.
- **ISP Variance:** Estar preparado para variaciones en los Selectors CSS si el firmware del router está personalizado por el ISP (Mundo, Entel, Movistar, etc.).
- **Monitoring:** 
  - Quisiera ejecutar este script como daemon para que monitoree continuamente el entorno Wi-Fi y realice ajustes automáticos según sea necesario. Idealmente para permitir analizar el comportamiento de las redes circundantes durante el día y tomar decisiones informadas sobre cuándo cambiar de canal.
  - Implementar un mecanismo de monitoreo para detectar si el cambio de canal ha mejorado la calidad de la conexión (ping a Google DNS, velocidad de descarga, etc.) y revertir si se detecta una degradación significativa.
  - El sistema debe ser capaz de revertir al canal anterior si el nuevo canal seleccionado resulta en una peor calidad de conexión después de un período de prueba (ej. 30 minutos).

---
*Documento generado para integración con GitHub Copilot.*