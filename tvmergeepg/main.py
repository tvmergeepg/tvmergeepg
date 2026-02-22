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
    """Analisa o conteúdo M3U e extrai canais e URLs de EPG."""
    channels = []
    epg_urls = []
    
    # Extrair URLs de EPG do cabeçalho #EXTM3U (x-tvg-url ou url-tvg)
    # Suporta múltiplas URLs separadas por vírgula em uma única tag
    epg_matches = re.findall(r'(?:x-tvg-url|url-tvg)="([^"]+)"', content)
    for match in epg_matches:
        # Dividir por vírgula e limpar espaços
        urls = [u.strip() for u in match.split(',') if u.strip()]
        epg_urls.extend(urls)
    
    # Extrair canais
    lines = content.splitlines()
    current_channel = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('#EXTINF:'):
            current_channel = {'info': line}
            # Tentar extrair tvg-id e nome do canal
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line)
            if tvg_id_match:
                current_channel['tvg-id'] = tvg_id_match.group(1)
            else:
                current_channel['tvg-id'] = ""
            
            # Extrair o nome do canal (texto após a última vírgula)
            name_match = re.search(r',([^,]+)$', line)
            if name_match:
                current_channel['name'] = name_match.group(1).strip()
            else:
                current_channel['name'] = ""
        elif (line.startswith('http') or line.startswith('rtmp') or line.startswith('mms')) and current_channel:
            current_channel['url'] = line
            channels.append(current_channel)
            current_channel = None
            
    return channels, epg_urls

def get_epg_channels(epg_content):
    """Extrai IDs de canais de um conteúdo EPG XML."""
    if not epg_content:
        return set()
    try:
        # Remover declaração XML se houver lixo antes dela
        if '<?xml' in epg_content:
            epg_content = epg_content[epg_content.find('<?xml'):]
            
        root = ET.fromstring(epg_content)
        return {channel.get('id') for channel in root.findall('channel') if channel.get('id')}
    except Exception as e:
        print(f"Erro ao processar XML: {e}")
        return set()

def main():
    parser = argparse.ArgumentParser(description="Unir listas M3U e ajustar tvg-id com base em EPG.")
    parser.add_argument("urls", nargs='+', help="URLs das listas M3U para unir.")
    parser.add_argument("-o", "--output", default="merged.m3u", help="Nome do arquivo de saída.")
    args = parser.parse_args()

    all_channels = []
    all_epg_urls = set()
    epg_channel_ids = set()

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
            ids = get_epg_channels(epg_content)
            print(f"  -> Encontrados {len(ids)} canais neste EPG.")
            epg_channel_ids.update(ids)

    print(f"Total de IDs de canais únicos no EPG: {len(epg_channel_ids)}")

    # Ajustar tvg-id se necessário
    updated_count = 0
    for channel in all_channels:
        current_id = channel.get('tvg-id', "")
        name = channel.get('name', "")
        
        # Se o tvg-id atual não estiver no EPG, mas o nome estiver, atualizamos
        if (not current_id or current_id not in epg_channel_ids) and name in epg_channel_ids:
            if f'tvg-id="{current_id}"' in channel['info']:
                channel['info'] = channel['info'].replace(f'tvg-id="{current_id}"', f'tvg-id="{name}"')
            else:
                # Se não houver tvg-id, insere após #EXTINF:-1
                channel['info'] = channel['info'].replace('#EXTINF:-1', f'#EXTINF:-1 tvg-id="{name}"')
            channel['tvg-id'] = name
            updated_count += 1

    print(f"Total de tvg-ids atualizados: {updated_count}")

    # Gerar o arquivo final
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        if all_epg_urls:
            f.write(f'#EXTM3U x-tvg-url="{",".join(all_epg_urls)}"\n')
        
        for channel in all_channels:
            f.write(f"{channel['info']}\n")
            f.write(f"{channel['url']}\n")

    print(f"Arquivo salvo em: {args.output}")

if __name__ == "__main__":
    main()
