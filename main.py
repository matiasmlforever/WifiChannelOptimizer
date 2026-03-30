import re
import subprocess
import logging
import sys
import time
import urllib.request
import threading
from datetime import datetime
from typing import Any
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load .env from the project root (falls back to environment variables)
load_dotenv(Path(__file__).parent / ".env")

import os

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("wifi_optimizer.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration  (values come from .env — see .env.example)
# ---------------------------------------------------------------------------
ROUTER_URL  = os.getenv("ROUTER_URL",  "http://192.168.100.1")
ROUTER_USER = os.getenv("ROUTER_USER", "admin")
ROUTER_PASS = os.getenv("ROUTER_PASS", "admin")

# Umbral de histéresis (20 %)
HYSTERESIS_THRESHOLD = 0.20

# Canales permitidos 2.4 GHz (non-overlapping)
CHANNELS_24 = [1, 6, 11]

# Canales preferidos 5 GHz (no-DFS: bandas baja y alta)
CHANNELS_5_PREFERRED = [36, 40, 44, 48, 149, 153, 157, 161]
# Todos los canales 5 GHz estándar (DFS incluidos como fallback)
CHANNELS_5_ALL = (
    CHANNELS_5_PREFERRED
    + [52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144]
)

# Daemon: intervalo entre escaneos (segundos)
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

# Monitoreo post-cambio
PROBE_HOST            = "8.8.8.8"
PROBE_DOWNLOAD_URL    = "http://speed.cloudflare.com/__down?bytes=1000000"
TRIAL_PERIOD_SECONDS  = int(os.getenv("TRIAL_PERIOD_SECONDS",  "1800"))
PING_DEGRADATION_MS   = int(os.getenv("PING_DEGRADATION_MS",   "100"))
SPEED_DEGRADATION_PCT = float(os.getenv("SPEED_DEGRADATION_PCT", "0.40"))

def signal_percent_to_dbm(signal_percent: int) -> float:
    """Convert a netsh signal quality percentage to approximate dBm."""
    return (signal_percent / 2) - 100


def scan_wifi_networks() -> list[dict[str, Any]]:
    """
    Scan nearby Wi-Fi networks on Windows using netsh.

    Returns:
        list[dict[str, Any]]: One entry per BSSID with keys:
            - ssid (str)
            - bssid (str)
            - signal_percent (int)
            - signal_dbm (float)
            - channel (int)
    """
    cmd = ["netsh", "wlan", "show", "networks", "mode=bssid"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="cp1252",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("No se encontro 'netsh' en el sistema.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"Error ejecutando netsh: {stderr or exc}") from exc

    output = result.stdout

    ssid_re    = re.compile(r"^\s*SSID\s+\d+\s*:\s*(.*)$", re.IGNORECASE)
    bssid_re   = re.compile(r"^\s*BSSID\s+\d+\s*:\s*([0-9A-Fa-f:]{17})\s*$", re.IGNORECASE)
    signal_re  = re.compile(r"^\s*(?:Signal|Se.al)\s*:\s*(\d+)\s*%", re.IGNORECASE)
    channel_re = re.compile(r"^\s*(?:Channel|Canal)\s*:\s*(\d+)\s*$", re.IGNORECASE)

    networks: list[dict[str, Any]] = []
    current_ssid: str = ""
    current_entry: dict[str, Any] | None = None

    for line in output.splitlines():
        ssid_match = ssid_re.match(line)
        if ssid_match:
            parsed = ssid_match.group(1).strip()
            if parsed:
                current_ssid = parsed
            continue

        bssid_match = bssid_re.match(line)
        if bssid_match:
            if current_entry and {"bssid", "signal_percent", "signal_dbm", "channel"}.issubset(current_entry):
                networks.append(current_entry)
            current_entry = {
                "ssid": current_ssid,
                "bssid": bssid_match.group(1).lower(),
            }
            continue

        if current_entry is None:
            continue

        signal_match = signal_re.match(line)
        if signal_match:
            signal_percent = int(signal_match.group(1))
            current_entry["signal_percent"] = signal_percent
            current_entry["signal_dbm"] = signal_percent_to_dbm(signal_percent)
            continue

        channel_match = channel_re.match(line)
        if channel_match:
            current_entry["channel"] = int(channel_match.group(1))
            continue

    if current_entry and {"bssid", "signal_percent", "signal_dbm", "channel"}.issubset(current_entry):
        networks.append(current_entry)

    return networks


# ---------------------------------------------------------------------------
# Phase 2: Decision Logic
# ---------------------------------------------------------------------------

def _band(channel: int) -> str:
    """Devuelve '2.4' o '5' según el número de canal."""
    return "2.4" if channel <= 14 else "5"


def _adjacent_channels(channel: int) -> set[int]:
    """
    Canales adyacentes que se solapan con el canal dado.
    2.4 GHz: ±4 canales (20 MHz de ancho de banda).
    5  GHz:  ±4 pasos de 4 (bloques de 20 MHz).
    """
    if _band(channel) == "2.4":
        return {c for c in range(max(1, channel - 4), min(14, channel + 4) + 1)}
    else:
        return {c for c in range(max(36, channel - 4), channel + 4 + 1, 4)}


def compute_congestion_scores(
    networks: list[dict[str, Any]], candidate_channels: list[int]
) -> dict[int, float]:
    """
    Congestion score = suma de dBm de todas las redes en el canal
    y sus adyacentes. Valor más negativo → menos congestión → mejor.
    """
    scores: dict[int, float] = {}
    for ch in candidate_channels:
        influence_zone = _adjacent_channels(ch) | {ch}
        scores[ch] = sum(
            net["signal_dbm"]
            for net in networks
            if net["channel"] in influence_zone
        )
    return scores


def best_channel(
    networks: list[dict[str, Any]], band: str, current_channel: int | None
) -> tuple[int, bool]:
    """
    Devuelve (mejor_canal, debe_cambiar).
    debe_cambiar es True solo si la mejora supera el umbral de histéresis.
    """
    candidates = CHANNELS_24 if band == "2.4" else CHANNELS_5_PREFERRED
    scores = compute_congestion_scores(networks, candidates)
    log.info("Congestion scores %s GHz: %s", band, scores)

    optimal = min(scores, key=lambda ch: scores[ch])

    if current_channel is None:
        return optimal, True

    current_score = scores.get(current_channel)
    if current_score is None:
        return optimal, True

    optimal_score = scores[optimal]
    improvement = (
        (current_score - optimal_score) / abs(current_score)
        if current_score != 0 else 0
    )
    should_change = (optimal != current_channel) and (improvement > HYSTERESIS_THRESHOLD)

    log.info(
        "%s GHz — actual: ch%s (%.1f), óptimo: ch%s (%.1f), mejora: %.1f%% → %s",
        band, current_channel, current_score,
        optimal, optimal_score,
        improvement * 100,
        "CAMBIAR" if should_change else "mantener",
    )
    return optimal, should_change


def log_interference_heatmap(networks: list[dict[str, Any]]) -> None:
    """Loguea un mapa de calor de interferencia por canal."""
    channel_data: dict[int, list[float]] = {}
    for net in networks:
        ch = net["channel"]
        channel_data.setdefault(ch, []).append(net["signal_dbm"])

    lines = ["=== Mapa de interferencia por canal ==="]
    for ch in sorted(channel_data):
        dbm_vals = channel_data[ch]
        avg = sum(dbm_vals) / len(dbm_vals)
        bar = "█" * len(dbm_vals)
        lines.append(
            f"  Ch{ch:>3}: {bar:<10} {len(dbm_vals)} red(es), avg {avg:.1f} dBm"
        )
    log.info("\n%s", "\n".join(lines))


# ---------------------------------------------------------------------------
# Phase 3: Router Automation (Playwright)
# ---------------------------------------------------------------------------

def _router_session(headed: bool = False):
    """Context manager que devuelve (playwright, browser, page) ya en el router."""
    # Usado internamente — ver apply_router_changes y read_current_channels.
    pass  # implementado inline en cada función para mayor claridad


def read_current_channels() -> tuple[int | None, int | None]:
    """
    Hace login al router y lee el canal actualmente configurado
    en 2.4 GHz y 5 GHz. Devuelve (ch24, ch5) o (None, None) si falla.
    """
    log.info("Leyendo canales actuales del router…")
    ch24 = None
    ch5  = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page    = context.new_page()
            page.set_default_timeout(20_000)

            _router_login(page)

            # Leer canal 2.4 GHz
            _safe_click_any(page, ["text=Advanced", "text=Avanzado"])
            page.wait_for_selector("#name_wlanconfig", state="visible", timeout=10_000)
            page.click("#name_wlanconfig")
            page.wait_for_selector("#wlan2adv", state="visible", timeout=8_000)
            page.click("#wlan2adv")
            page.wait_for_timeout(2_000)   # panel AJAX
            frame24 = _find_panel_frame(page, "#Channel")
            ch24 = _read_channel_from_frame(frame24, "2.4")

            # Leer canal 5 GHz
            _safe_click_any(page, ["#name_wlanconfig"])
            page.wait_for_selector("#wlan5adv", state="visible", timeout=8_000)
            page.click("#wlan5adv")
            page.wait_for_timeout(2_000)   # panel AJAX
            frame5 = _find_panel_frame(page, "#Channel")
            ch5 = _read_channel_from_frame(frame5, "5")

            browser.close()
    except Exception as exc:
        log.warning("No se pudieron leer los canales actuales: %s", exc)

    return ch24, ch5


def _router_login(page) -> None:
    """Hace login en el router. Reutilizable entre read_current_channels y apply_router_changes."""
    page.goto(ROUTER_URL, wait_until="domcontentloaded")

    try:
        page.wait_for_selector("#txt_Username", state="visible", timeout=10_000)
        login_frame = page
    except Exception:
        login_frame = None
        for frame in page.frames:
            try:
                frame.wait_for_selector("#txt_Username", state="visible", timeout=3_000)
                login_frame = frame
                break
            except Exception:
                continue
        if login_frame is None:
            raise RuntimeError("No se encontró #txt_Username en la página ni en iframes.")

    login_frame.fill("#txt_Username", ROUTER_USER)
    login_frame.fill("#txt_Password", ROUTER_PASS)

    # nth(1) = el #loginbutton visible (el HG8145X6 tiene dos en el DOM)
    clicked = False
    try:
        login_frame.locator("#loginbutton").nth(1).click(timeout=5_000)
        clicked = True
    except Exception:
        pass

    if not clicked:
        for sel in ["#loginBtn", "#btn_login",
                    "input[type='button'][value='Log In']",
                    "input[type='button'][value*='ogin']",
                    "input[type='submit']", "button[type='submit']"]:
            try:
                login_frame.click(sel, timeout=5_000)
                clicked = True
                break
            except Exception:
                continue

    if not clicked:
        raise RuntimeError("No se encontró el botón de login.")

    # Esperar a que aparezca el menú principal.
    # Playwright no soporta selectores con coma en wait_for_selector,
    # hay que probar cada uno individualmente.
    post_login_selectors = [
        "text=Advanced",
        "text=Avanzado",
        "#name_Advanced",
        "#indexMenuMain",   # contenedor del menú en algunos firmwares
    ]
    logged_in = False
    for sel in post_login_selectors:
        try:
            page.wait_for_selector(sel, state="visible", timeout=10_000)
            logged_in = True
            break
        except Exception:
            continue
    if not logged_in:
        # Fallback: esperar que la URL cambie a index.asp
        try:
            page.wait_for_url("**/index.asp", timeout=10_000)
            logged_in = True
        except Exception:
            pass
    if not logged_in:
        raise RuntimeError("Login completado pero no se detectó el menú principal.")
    log.info("Login exitoso.")


def apply_router_changes(
    new_24_channel: int | None, new_5_channel: int | None,
    *,
    dry_run: bool = False,
    headed: bool = False,
) -> None:
    """
    Login al router Huawei HG8145X6 y cambia los canales indicados.
    Silencia el timeout/reset esperado al reiniciar la radio Wi-Fi.
    """
    if new_24_channel is None and new_5_channel is None:
        log.info("No hay cambios que aplicar en el router.")
        return

    if dry_run:
        log.info(
            "[DRY-RUN] Se aplicaría → 2.4 GHz: ch%s | 5 GHz: ch%s",
            new_24_channel, new_5_channel,
        )
        return

    log.info(
        "Automatización del router → 2.4 GHz: ch%s | 5 GHz: ch%s",
        new_24_channel, new_5_channel,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, slow_mo=300 if headed else 0)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(20_000)

        try:
            log.info("Accediendo a %s …", ROUTER_URL)

            # ── Login ────────────────────────────────────────────────────────
            _router_login(page)

            # ── Diagnóstico post-login (solo inspect) ────────────────────────
            if headed:
                with open("router_post_login.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                log.info("HTML post-login guardado (URL: %s)", page.url)

            # ── Navegación ───────────────────────────────────────────────────
            _safe_click_any(page, ["text=Advanced", "text=Avanzado", "a[href*='advanced']"])
            page.wait_for_selector("#name_wlanconfig", state="visible", timeout=10_000)
            page.click("#name_wlanconfig")

            # ── Canal 2.4 GHz ────────────────────────────────────────────────
            if new_24_channel is not None:
                page.wait_for_selector("#wlan2adv", state="visible", timeout=8_000)
                page.click("#wlan2adv")
                page.wait_for_timeout(2_000)   # esperar render AJAX del panel
                if headed:
                    with open("router_wlan24.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    log.info("HTML 2.4G guardado en router_wlan24.html")
                panel_24 = _find_panel_frame(page, "#Channel")
                _dump_selects_and_inputs(panel_24, "2.4G")
                _set_channel_dropdown(panel_24, band="2.4", channel=new_24_channel)
                _submit_and_wait(panel_24)

            # ── Canal 5 GHz ──────────────────────────────────────────────────
            if new_5_channel is not None:
                _safe_click_any(page, ["#name_wlanconfig"])
                page.wait_for_selector("#wlan5adv", state="visible", timeout=8_000)
                page.click("#wlan5adv")
                page.wait_for_timeout(2_000)   # esperar render AJAX del panel
                if headed:
                    with open("router_wlan5.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                    log.info("HTML 5G guardado en router_wlan5.html")
                panel_5 = _find_panel_frame(page, "#Channel")
                _dump_selects_and_inputs(panel_5, "5G")
                _set_channel_dropdown(panel_5, band="5", channel=new_5_channel)
                _submit_and_wait(panel_5)

        except Exception as exc:
            log.error("Error durante la automatización del router: %s", exc)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _read_channel_from_frame(frame, band: str) -> int | None:
    """Lee el valor actual del dropdown #Channel dentro del frame dado."""
    try:
        val = frame.locator("#Channel").first.evaluate("el => el.value")
        ch = int(val)
        log.info("Canal actual %s GHz: ch%s", band, ch)
        return ch
    except Exception as exc:
        log.warning("No se pudo leer canal %s GHz: %s", band, exc)
        return None


def _find_panel_frame(page, selector: str = "#Channel"):
    """
    Busca el frame (o la página principal) que contiene `selector`.
    El HG8145X6 carga los paneles WLAN dentro de un <iframe> embebido.
    Devuelve el frame/page donde se encontró el elemento, o `page` como fallback.
    """
    # Primero intentar en la página principal
    try:
        if page.locator(selector).count() > 0:
            return page
    except Exception:
        pass

    # Buscar en todos los frames del contexto
    for frame in page.frames:
        try:
            frame.wait_for_selector(selector, state="attached", timeout=2_000)
            log.info("Panel encontrado en iframe: %s", frame.url)
            return frame
        except Exception:
            continue

    log.warning("No se encontró el frame con '%s'; usando página principal.", selector)
    return page


def _dump_selects_and_inputs(page, label: str) -> None:
    """Lista todos los <select> e <input> del panel actual en el log (solo modo inspect)."""
    log.info("── Elementos del panel %s ──", label)
    for sel in ["select", "input[type='text']", "input[type='button']", "button[type='button']"]:
        els = page.locator(sel).all()
        for el in els:
            try:
                log.info(
                    "  %-8s id=%-30s name=%-25s value=%-15s visible=%s",
                    sel.split("[")[0],
                    el.get_attribute("id") or "",
                    el.get_attribute("name") or "",
                    el.get_attribute("value") or el.input_value() or "",
                    el.is_visible(),
                )
            except Exception:
                pass


def _safe_click_any(page, selectors: list[str], timeout: int = 6_000) -> bool:
    """Itera una lista de selectores y hace click en el primero que exista. Devuelve True si tuvo éxito."""
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout)
            return True
        except Exception:
            continue
    log.warning("No se pudo hacer click con ninguno de: %s", selectors)
    return False


def _safe_click(page, selector: str) -> None:
    """Wrapper de compatibilidad: acepta selectores separados por coma."""
    _safe_click_any(page, [s.strip() for s in selector.split(",")])


def _set_channel_dropdown(page, band: str, channel: int) -> None:
    """
    Selecciona el canal en el dropdown de la página de Advanced WLAN.
    En el HG8145X6 ambas bandas usan id='Channel' (la página se recarga
    por submenú, así que siempre hay un solo #Channel visible).
    """
    selectors = [
        "#Channel",                          # Huawei HG8145X6 — id exacto
        "select#Channel",
        f"select[id*='Channel']",
        f"select[id*='channel']",
    ]
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            locator.wait_for(state="visible", timeout=5_000)
            locator.select_option(str(channel))
            log.info("Canal %s GHz → ch%s (selector: '%s')", band, channel, sel)
            return
        except Exception:
            continue
    log.warning("No se encontró dropdown #Channel para %s GHz.", band)


def _submit_and_wait(page) -> None:
    """Hace click en el botón Apply y espera el reinicio de la radio."""
    submit_selectors = [
        "#applyButton",                                # Huawei HG8145X6 — id exacto (onclick="Submit();")
        "#confirmokbtn",
        "#apply_btn", "#btn_apply", "#btn_ok", "#submitId",
        "button[type='button'][id*='apply']",
        "input[type='button'][value='Apply']",
        "input[type='button'][value*='pply']",
        "input[type='submit']",
        "button[type='submit']",
    ]
    submitted = False
    for sel in submit_selectors:
        try:
            page.click(sel, timeout=5_000)
            submitted = True
            log.info("Submit OK con selector '%s'.", sel)
            break
        except Exception:
            continue

    if not submitted:
        log.warning("No se encontró botón de submit; los cambios pueden no haberse guardado.")
        return

    log.info("Cambios enviados. Esperando reinicio de la radio…")
    try:
        # Esperar que el panel vuelva a estar disponible (o el timeout del reset)
        page.wait_for_timeout(3_000)
    except Exception:
        log.info("Desconexión/timeout esperado al reiniciar la radio — OK.")


# ---------------------------------------------------------------------------
# Monitoring: ping & download speed
# ---------------------------------------------------------------------------

def measure_ping_ms(host: str = PROBE_HOST, count: int = 4) -> float | None:
    """Ping a host usando el comando del SO. Devuelve latencia media en ms o None."""
    try:
        result = subprocess.run(
            ["ping", "-n", str(count), host],
            capture_output=True, text=True,
            encoding="cp1252", errors="replace",
            timeout=20,
        )
        out = result.stdout
        # EN: "Average = 12ms"  ES-EU: "Promedio = 12ms"
        m = re.search(r"(?:Average|Promedio)\s*=\s*(\d+)\s*ms", out, re.IGNORECASE)
        if m:
            return float(m.group(1))
        # ES-LATAM Windows: líneas como "Mínimo = 10ms, Máximo = 15ms, Media = 12ms"
        m = re.search(r"(?:Media|Average)\s*=\s*(\d+)\s*ms", out, re.IGNORECASE)
        if m:
            return float(m.group(1))
        # Fallback: promediar todos los "tiempo=Xms" / "time=Xms" individuales
        times = re.findall(r"t(?:iempo|ime)[<=]\s*(\d+)\s*ms", out, re.IGNORECASE)
        if times:
            vals = [float(t) for t in times]
            return sum(vals) / len(vals)
    except Exception as exc:
        log.debug("Error midiendo ping: %s", exc)
    return None


def measure_download_mbps(url: str = PROBE_DOWNLOAD_URL, timeout: int = 15) -> float | None:
    """Descarga un archivo de prueba y calcula Mbps. Devuelve None si falla."""
    try:
        start = time.monotonic()
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
        elapsed = time.monotonic() - start
        if elapsed > 0:
            return (len(data) * 8) / (elapsed * 1_000_000)   # Mbps
    except Exception as exc:
        log.debug("Error midiendo velocidad: %s", exc)
    return None


def measure_quality() -> dict[str, float | None]:
    """Mide ping y velocidad de descarga actuales."""
    ping   = measure_ping_ms()
    speed  = measure_download_mbps()
    log.info("Calidad actual → ping: %s ms | velocidad: %s Mbps",
             f"{ping:.1f}" if ping else "N/A",
             f"{speed:.2f}" if speed else "N/A")
    return {"ping_ms": ping, "speed_mbps": speed}


def _quality_degraded(baseline: dict, current: dict) -> bool:
    """
    Devuelve True si la calidad actual es significativamente peor
    que la línea base medida antes del cambio de canal.
    """
    b_ping,  c_ping  = baseline.get("ping_ms"),  current.get("ping_ms")
    b_speed, c_speed = baseline.get("speed_mbps"), current.get("speed_mbps")

    if b_ping and c_ping and (c_ping - b_ping) > PING_DEGRADATION_MS:
        log.warning(
            "Degradación de ping detectada: %.1f ms → %.1f ms (Δ%.1f ms)",
            b_ping, c_ping, c_ping - b_ping,
        )
        return True

    if b_speed and c_speed and b_speed > 0:
        drop = (b_speed - c_speed) / b_speed
        if drop > SPEED_DEGRADATION_PCT:
            log.warning(
                "Degradación de velocidad detectada: %.2f → %.2f Mbps (−%.0f%%)",
                b_speed, c_speed, drop * 100,
            )
            return True

    return False


def monitor_and_revert(
    prev_24: int | None, prev_5: int | None,
    new_24:  int | None, new_5:  int | None,
    baseline: dict,
) -> None:
    """
    Ejecutado en un hilo separado. Espera TRIAL_PERIOD_SECONDS y luego
    mide la calidad. Si hay degradación, revierte al canal anterior.
    """
    log.info(
        "Período de prueba iniciado (%d min). Monitoreando calidad…",
        TRIAL_PERIOD_SECONDS // 60,
    )
    time.sleep(TRIAL_PERIOD_SECONDS)

    current = measure_quality()
    if _quality_degraded(baseline, current):
        log.warning(
            "Calidad degradada tras %d min. Revirtiendo: 2.4 GHz ch%s → ch%s | 5 GHz ch%s → ch%s",
            TRIAL_PERIOD_SECONDS // 60,
            new_24, prev_24, new_5, prev_5,
        )
        apply_router_changes(new_24_channel=prev_24, new_5_channel=prev_5)
        log.info("Reversión completada.")
    else:
        log.info(
            "Calidad estable tras %d min. Canal confirmado. ping=%.1f ms | speed=%.2f Mbps",
            TRIAL_PERIOD_SECONDS // 60,
            current.get("ping_ms") or 0,
            current.get("speed_mbps") or 0,
        )


# ---------------------------------------------------------------------------
# Core optimizer cycle
# ---------------------------------------------------------------------------

def run_optimization_cycle(state: dict, *, dry_run: bool = False, headed: bool = False) -> None:
    """
    Un ciclo de optimización completo:
      1. Escanea redes
      2. Calcula el mejor canal con histéresis
      3. Aplica cambios si corresponde (salvo dry_run)
      4. Lanza monitoreo post-cambio en hilo separado
    state: dict mutable con claves 'current_24' y 'current_5'
    """
    log.info("─" * 60)
    log.info("Iniciando ciclo de optimización — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    networks = scan_wifi_networks()
    if not networks:
        log.warning("No se detectaron redes. Omitiendo ciclo.")
        return

    log.info("Redes detectadas: %d", len(networks))
    log_interference_heatmap(networks)

    best_24, change_24 = best_channel(networks, "2.4", state["current_24"])
    best_5,  change_5  = best_channel(networks, "5",   state["current_5"])

    apply_24 = best_24 if change_24 else None
    apply_5  = best_5  if change_5  else None

    if not apply_24 and not apply_5:
        log.info("Canal ya óptimo o dentro del umbral de histéresis. Sin cambios.")
        return

    # Medir calidad ANTES del cambio
    log.info("Midiendo calidad de conexión antes del cambio…")
    baseline = measure_quality()

    prev_24 = state["current_24"]
    prev_5  = state["current_5"]

    apply_router_changes(
        new_24_channel=apply_24,
        new_5_channel=apply_5,
        dry_run=dry_run,
        headed=headed,
    )

    if dry_run:
        return

    # Actualizar estado
    if apply_24:
        state["current_24"] = apply_24
    if apply_5:
        state["current_5"] = apply_5

    log.info(
        "Canales aplicados → 2.4 GHz: ch%s | 5 GHz: ch%s",
        state["current_24"], state["current_5"],
    )

    # Lanzar monitoreo post-cambio en hilo daemon (no bloquea el loop principal)
    t = threading.Thread(
        target=monitor_and_revert,
        args=(prev_24, prev_5, apply_24, apply_5, baseline),
        daemon=True,
        name="monitor-revert",
    )
    t.start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    dry_run = "--dry-run" in args
    headed  = "--inspect" in args   # abre Chromium visible para ver los selectores reales
    once    = "--once"    in args or headed  # --inspect implica una sola ejecución

    if dry_run:
        log.info("Modo DRY-RUN activo: el router NO será modificado.")
    if headed:
        log.info("Modo INSPECT activo: Chromium se abrirá en modo visible.")

    # Estado persistente entre ciclos — leer del router al arrancar
    state: dict[str, int | None] = {"current_24": None, "current_5": None}
    if not dry_run:
        state["current_24"], state["current_5"] = read_current_channels()

    if once:
        log.info("Modo single-shot.")
        run_optimization_cycle(state, dry_run=dry_run, headed=headed)
    else:
        log.info(
            "Modo daemon iniciado. Intervalo de escaneo: %d s. "
            "Ctrl+C para detener. Flags: --once | --dry-run | --inspect",
            SCAN_INTERVAL_SECONDS,
        )
        while True:
            try:
                run_optimization_cycle(state, dry_run=dry_run, headed=headed)
            except KeyboardInterrupt:
                log.info("Detenido por el usuario.")
                break
            except Exception as exc:
                log.error("Error en el ciclo de optimización: %s", exc)
            log.info("Próximo escaneo en %d s…", SCAN_INTERVAL_SECONDS)
            try:
                time.sleep(SCAN_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                log.info("Detenido por el usuario.")
                break
