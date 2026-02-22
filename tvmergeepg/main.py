import argparse
import requests
import gzip
import lzma
import xml.etree.ElementTree as ET
import re
import os
from io import BytesIO

def download_content(url):
    """Baixa o conteúdo de uma URL, lidando com arquivos compactados .gz e .xz."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Verificar se é .gz
        if url.endswith('.gz') or response.headers.get('Content-Type') == 'application/x-gzip':
            with gzip.GzipFile(fileobj=BytesIO(response.content)) as f:
                return f.read().decode('utf-8', errors='ignore')
        
        # Verificar se é .xz
        if url.endswith('.xz') or response.headers.get('Content-Type') == 'application/x-xz':
            with lzma.open(BytesIO(response.content)) as f:
                return f.read().decode('utf-8', errors='ignore')
                
        return response.text
    except Exception as e:
        print(f"Erro ao baixar {url}: {e}")
        return None

def parse_m3u(content):
    """Analisa o conteúdo M3U e extrai canais e URLs de EPG, preservando metadados."""
    channels = []
    epg_urls = []
    
    # Extrair URLs de EPG do cabeçalho #EXTM3U (x-tvg-url ou url-tvg)
    epg_matches = re.findall(r'(?:x-tvg-url|url-tvg)="([^"]+)"', content)
    for match in epg_matches:
        urls = [u.strip() for u in match.split(',') if u.strip()]
        epg_urls.extend(urls)
    
    # Extrair canais
    lines = content.splitlines()
    current_channel = None
    
    for line in lines:
        line_strip = line.strip()
        if not line_strip:
            continue
            
        if line_strip.startswith('#EXTINF:'):
            # Se já tínhamos um canal pendente sem URL, salvamos (embora incomum)
            if current_channel:
                channels.append(current_channel)
                
            current_channel = {
                'info': line_strip,
                'metadata': [], # Para armazenar #EXTVLCOPT e outras tags
                'tvg-id': "",
                'name': "",
                'url': ""
            }
            
            # Extrair tvg-id
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line_strip)
            if tvg_id_match:
                current_channel['tvg-id'] = tvg_id_match.group(1)
            
            # Extrair o nome do canal
            name_match = re.search(r',([^,]+)$', line_strip)
            if name_match:
                current_channel['name'] = name_match.group(1).strip()
                
        elif line_strip.startswith('#') and not line_strip.startswith('#EXTM3U') and current_channel:
            # Capturar metadados como #EXTVLCOPT
            current_channel['metadata'].append(line_strip)
            
        elif (line_strip.startswith('http') or line_strip.startswith('rtmp') or line_strip.startswith('mms')) and current_channel:
            current_channel['url'] = line_strip
            channels.append(current_channel)
            current_channel = None
            
    return channels, epg_urls

def get_epg_data(epg_content):
    """Extrai IDs e display-names de canais de um conteúdo EPG XML."""
    if not epg_content:
        return {}, set()
    try:
        if '<?xml' in epg_content:
            epg_content = epg_content[epg_content.find('<?xml'):]
            
        root = ET.fromstring(epg_content)
        name_to_id = {}
        all_ids = set()
        
        for channel in root.findall('channel'):
            channel_id = channel.get('id')
            if not channel_id:
                continue
            all_ids.add(channel_id)
            name_to_id[channel_id.lower()] = channel_id
            
            for display_name in channel.findall('display-name'):
                if display_name.text:
                    name_to_id[display_name.text.lower()] = channel_id
                    
        return name_to_id, all_ids
    except Exception as e:
        print(f"Erro ao processar XML: {e}")
        return {}, set()

def main():
    parser = argparse.ArgumentParser(description="Unir listas M3U e ajustar tvg-id com base em EPG.")
    parser.add_argument("urls", nargs='+', help="URLs das listas M3U para unir.")
    parser.add_argument("-o", "--output", default="merged.m3u", help="Nome do arquivo de saída.")
    args = parser.parse_args()

    all_channels = []
    all_epg_urls = set()
    global_name_to_id = {}
    global_epg_ids = set()

    for url in args.urls:
        print(f"Processando lista: {url}")
        content = download_content(url)
        if content:
            channels, epg_urls = parse_m3u(content)
            all_channels.extend(channels)
            all_epg_urls.update(epg_urls)

    print(f"Total de canais encontrados: {len(all_channels)}")
    print(f"Total de URLs de EPG encontradas: {len(all_epg_urls)}")

    for epg_url in all_epg_urls:
        print(f"Processando EPG: {epg_url}")
        epg_content = download_content(epg_url)
        if epg_content:
            name_to_id, ids = get_epg_data(epg_content)
            print(f"  -> Encontrados {len(ids)} canais e {len(name_to_id)} nomes neste EPG.")
            global_name_to_id.update(name_to_id)
            global_epg_ids.update(ids)

    print(f"Total de IDs de canais únicos no EPG: {len(global_epg_ids)}")

    # Ajustar tvg-id se necessário
    updated_count = 0
    for channel in all_channels:
        current_id = channel.get('tvg-id', "")
        name = channel.get('name', "")
        
        if not current_id or current_id not in global_epg_ids:
            found_id = global_name_to_id.get(name.lower())
            if found_id:
                if f'tvg-id="{current_id}"' in channel['info']:
                    channel['info'] = channel['info'].replace(f'tvg-id="{current_id}"', f'tvg-id="{found_id}"')
                else:
                    channel['info'] = channel['info'].replace('#EXTINF:-1', f'#EXTINF:-1 tvg-id="{found_id}"')
                channel['tvg-id'] = found_id
                updated_count += 1

    print(f"Total de tvg-ids atualizados: {updated_count}")

    # Gerar o arquivo final
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        if all_epg_urls:
            f.write(f'#EXTM3U x-tvg-url="{",".join(all_epg_urls)}"\n')
        
        for channel in all_channels:
            f.write(f"{channel['info']}\n")
            # Escrever metadados extras como #EXTVLCOPT
            for meta in channel.get('metadata', []):
                f.write(f"{meta}\n")
            f.write(f"{channel['url']}\n")

    print(f"Arquivo salvo em: {args.output}")

if __name__ == "__main__":
    main()
