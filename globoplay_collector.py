import logging
import os
import shutil
import json
import time
import traceback
import subprocess
from threading import Thread
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urlparse, urlunparse
from datetime import datetime
import pytz  # Adicionando pytz para manipulação de fusos horários

# ================= CONFIG =================
OUTPUT_DIR = os.path.expanduser("~/Desktop/globoplay_output")
M3U_FILE = os.path.join(OUTPUT_DIR, "globoplay.m3u")

AGORA_NA_TV_URL = "https://globoplay.globo.com/agora-na-tv/"
GLOBO_INTERNACIONAL_URL = "https://globoplay.globo.com/v/7832875/"
GLOBONEWS_URL = "https://globoplay.globo.com/globonews/ao-vivo/7339101/"

SELENIUM_PROFILE = "/root/selenium-profile"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",  # Ajuste para o formato de hora desejado
    handlers=[
        logging.StreamHandler()  # Apenas exibe no terminal, sem gravar em arquivo
    ]
)

# ================= AUX =================
def close_chrome():
    os.system("pkill -9 -f 'chrome|chromium' 2>/dev/null")
    time.sleep(2)

def find_chrome():
    return shutil.which("google-chrome") or shutil.which("chromium")

# ================= DRIVER =================
def setup_driver():
    close_chrome()
    chrome_path = find_chrome()

    if not chrome_path:
        logging.info("✗ Chrome não encontrado")
        return None

    options = Options()
    options.binary_location = chrome_path

    options.add_argument(f"--user-data-dir={SELENIUM_PROFILE}")
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    options.add_argument("--mute-audio")

    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        logging.info("✔ Chrome iniciado com perfil logado")
        return driver
    except Exception as e:
        logging.error(f"✗ Erro ao iniciar Chrome: {e}")
        logging.error(traceback.format_exc())
        return None

# ================= LOGIN CHECK =================
def is_login_required(driver):
    try:
        page_text = driver.page_source.lower()

        if "faça seu login" in page_text:
            return True
        if "conteúdos exclusivos" in page_text:
            return True
        if "restritos a assinantes" in page_text:
            return True

        return False
    except:
        return False

# ================= M3U8 =================
def normalize(url):
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))

def extract_m3u8(driver):
    try:
        logs = driver.get_log("performance")
        urls = []

        for entry in logs:
            msg = json.loads(entry["message"])["message"]

            if msg.get("method") != "Network.requestWillBeSent":
                continue

            url = msg.get("params", {}).get("request", {}).get("url", "")

            if ".m3u8" not in url:
                continue
            if "video.globo.com" not in url:
                continue
            if "youboranqs" in url:
                continue
            if "/live/" not in url:
                continue

            clean = normalize(url)

            if clean not in urls:
                urls.append(clean)

        return urls[-1] if urls else None
    except Exception as e:
        logging.error(f"✗ Erro extraindo m3u8: {e}")
        return None

# ================= CAPTURA =================
def capture_channel(driver, name, url, max_wait=60):
    logging.info(f"Acessando {name}")

    driver.get_log("performance")
    driver.get(url)
    time.sleep(5)

    # 🔴 Se exigir login, pula imediatamente
    if is_login_required(driver):
        logging.info(f"✗ {name} exige login. Pulando para o próximo.")
        return None

    # Força play
    driver.execute_script("""
        let video = document.querySelector('video');
        if (video) {
            video.muted = true;
            video.play().catch(()=>{});
        }
    """)

    elapsed = 0
    interval = 5

    while elapsed < max_wait:

        # 🔴 Verifica novamente durante espera
        if is_login_required(driver):
            logging.info(f"✗ {name} bloqueado por login durante carregamento.")
            return None

        m3u8 = extract_m3u8(driver)

        if m3u8:
            logging.info(f"✔ Stream capturado: {name}")
            return m3u8

        time.sleep(interval)
        elapsed += interval
        logging.info(f"Aguardando stream... {elapsed}/{max_wait}s")

    logging.info(f"✗ Falha ao capturar: {name}")
    return None

# ================= DESCUBRA BBB =================
def discover_bbb(driver):
    logging.info("Abrindo Agora na TV...")
    driver.get(AGORA_NA_TV_URL)
    time.sleep(10) # Espera maior para carregar os canais
    
    # Rola para baixo para garantir o carregamento de todos os canais
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3)

    # Procura por todos os cards de canais
    # O seletor abaixo tenta capturar o link que contém o título do canal BBB
    # Na página "Agora na TV", os canais BBB costumam ter "Big Brother Brasil" no aria-label ou texto próximo
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/v/'], a[href*='/ao-vivo/']")
    channels = []
    seen = set()

    for a in links:
        href = a.get_attribute("href")
        if not href or href in seen:
            continue

        # Tenta extrair o título exato que aparece na interface (ex: "Acompanhe a Casa", "Segue o Líder")
        # Geralmente está dentro de um elemento de texto dentro do link ou no aria-label formatado
        aria_label = a.get_attribute("aria-label") or ""
        
        # Se o canal for do BBB, processamos
        if "big brother brasil" in aria_label.lower() or "bbb" in aria_label.lower():
            seen.add(href)
            
            # Limpeza do nome para pegar apenas a parte relevante (ex: "Acompanhe a Casa")
            # O aria-label costuma ser algo como "Canal Big Brother Brasil, Acompanhe a Casa, Ao vivo..."
            name = "BBB ao vivo"
            if "," in aria_label:
                parts = [p.strip() for p in aria_label.split(",")]
                # O segundo elemento geralmente é o nome da câmera/canal específico
                if len(parts) > 1:
                    name = parts[1]
            
            # Caso não consiga pelo aria-label, tenta buscar o texto visível dentro do elemento
            if name == "BBB ao vivo" or "Big Brother Brasil" in name:
                try:
                    # Tenta encontrar o texto do título dentro do card (ex: p ou span com o nome)
                    inner_text = a.text.split('\n')[-1] # Geralmente o nome do canal é a última linha de texto no card
                    if inner_text and len(inner_text) > 3:
                        name = inner_text
                except:
                    pass

            channels.append((name.strip(), href))

    logging.info(f"✓ Canais BBB encontrados: {len(channels)}")
    for n, h in channels:
        logging.info(f"  - {n}: {h}")
        
    return channels

# ================= GERAR NOME DE ARQUIVO =================
def generate_filename(channel_name):
    now = datetime.now(pytz.timezone("America/Sao_Paulo"))

    date_part = now.strftime("%m%d")
    time_part = now.strftime("%H%M%S")
    year = str(now.year)

    channel_prefix = "SBTVD"
    # Remove caracteres especiais e espaços para o nome do arquivo
    event_name = channel_name.upper().replace(" ", "_")

    return f"{date_part}_{time_part}_{channel_prefix}_{event_name}_{year}.mp4"

# ================= SALVAR M3U =================
def save_m3u(globo_m3u8, globonews_m3u8, bbb_list):
    if not globo_m3u8 and not globonews_m3u8 and not bbb_list:
        logging.info("Nenhum canal para salvar")
        return

    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        if globo_m3u8:
            f.write('#EXTINF:-1 group-title="GLOBO AO VIVO",Globo Internacional\n')
            f.write(globo_m3u8 + "\n")

        if globonews_m3u8:
            f.write('#EXTINF:-1 group-title="GLOBO AO VIVO",GloboNews\n')
            f.write(globonews_m3u8 + "\n")

        for name, url in bbb_list:
            f.write(f'#EXTINF:-1 group-title="Reality Show\'s Live",{name}\n')
            f.write(url + "\n")

    logging.info(f"✔ M3U salvo: {M3U_FILE}")

# ================= GRAVAÇÃO =================
def record_stream(m3u8_url, output_file, duration=240):
    logging.info(f"Iniciando gravação para {m3u8_url}...")

    command = [
        "ffmpeg",
        "-i", m3u8_url,
        "-t", str(duration),
        "-c", "copy",
        "-f", "mp4",
        "-y",
        output_file
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600
        )

        if result.returncode != 0:
            logging.error(f"Erro na gravação: {result.stderr}")
        else:
            logging.info(f"Gravação concluída com sucesso: {m3u8_url}")

    except subprocess.TimeoutExpired:
        logging.error("Erro: timeout no ffmpeg.")
    except Exception as e:
        logging.error(f"Erro ao executar ffmpeg: {str(e)}")

def record_all_streams(bbb_streams, globo_m3u8, globonews_m3u8):
    threads = []

    if globo_m3u8:
        globo_output = os.path.join(
            OUTPUT_DIR,
            generate_filename("Globo Internacional")
        )
        thread = Thread(target=record_stream, args=(globo_m3u8, globo_output))
        threads.append(thread)
        thread.start()

    if globonews_m3u8:
        globonews_output = os.path.join(
            OUTPUT_DIR,
            generate_filename("GloboNews")
        )
        thread = Thread(target=record_stream, args=(globonews_m3u8, globonews_output))
        threads.append(thread)
        thread.start()

    for name, m3u8_url in bbb_streams:
        output_file = os.path.join(OUTPUT_DIR, generate_filename(name))
        thread = Thread(target=record_stream, args=(m3u8_url, output_file))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    logging.info("✔ Todos os streams foram gravados com sucesso!")

# ================= MAIN =================
def main():
    logging.info("="*60)
    logging.info("INICIANDO COLETOR GLOBOPLAY LINUX")
    logging.info("="*60)

    while True:  # Loop contínuo
        driver = setup_driver()
        if not driver:
            logging.info("Erro ao iniciar o driver. Tentando novamente...")
            time.sleep(10)
            continue

        globo_m3u8 = capture_channel(driver, "Globo Internacional", GLOBO_INTERNACIONAL_URL)
        globonews_m3u8 = capture_channel(driver, "GloboNews", GLOBONEWS_URL)
        bbb_channels = discover_bbb(driver)

        bbb_streams = []
        for name, url in bbb_channels:
            m3u8 = capture_channel(driver, name, url)
            if m3u8:
                bbb_streams.append((name, m3u8))

        save_m3u(globo_m3u8, globonews_m3u8, bbb_streams)

        logging.info("Fechando o navegador...")
        driver.quit()

        record_all_streams(bbb_streams, globo_m3u8, globonews_m3u8)

        logging.info("Aguardando antes da próxima execução...")
        time.sleep(60 * 1)  # Aguardar 1 minutos antes de repetir o processo

if __name__ == "__main__":
    main()
