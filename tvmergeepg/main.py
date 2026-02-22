import argparse
import requests
import gzip
import lzma
import xml.etree.ElementTree as ET
import re
import os
from io import BytesIO

def normalize_name(name):
    """Normaliza o nome do canal para comparação (minúsculas, sem espaços extras)."""
    if not name:
        return ""
    # Remover espaços extras, converter para minúsculas e remover caracteres não alfanuméricos básicos
    return re.sub(r'[^a-z0-9]', '', name.lower())

def download_stream(url):
    """Baixa o conteúdo de uma URL e retorna um stream de bytes descompactado se necessário."""
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '')
        
        # Verificar se é .gz
        if url.endswith('.gz') or 'gzip' in content_type:
            return gzip.GzipFile(fileobj=BytesIO(response.content))
        
        # Verificar se é .xz
        if url.endswith('.xz') or 'xz' in content_type:
            return lzma.LZMAFile(BytesIO(response.content))
            
        return BytesIO(response.content)
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
            if current_channel:
                channels.append(current_channel)
                
            current_channel = {
                'info': line_strip,
                'metadata': [],
                'tvg-id': "",
                'tvg-name': "",
                'name': "",
                'url': ""
            }
            
            # Extrair tvg-id
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line_strip)
            if tvg_id_match:
                current_channel['tvg-id'] = tvg_id_match.group(1)
            
            # Extrair tvg-name
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', line_strip)
            if tvg_name_match:
                current_channel['tvg-name'] = tvg_name_match.group(1)
            
            # Extrair o nome do canal
            name_match = re.search(r',([^,]+)$', line_strip)
            if name_match:
                current_channel['name'] = name_match.group(1).strip()
                
        elif line_strip.startswith('#') and not line_strip.startswith('#EXTM3U') and current_channel:
            current_channel['metadata'].append(line_strip)
            
        elif (line_strip.startswith('http') or line_strip.startswith('rtmp') or line_strip.startswith('mms')) and current_channel:
            current_channel['url'] = line_strip
            channels.append(current_channel)
            current_channel = None
            
    return channels, epg_urls

def get_epg_data_streaming(stream):
    """Extrai IDs e display-names de canais de um stream EPG XML usando iterparse para economizar memória."""
    if not stream:
        return {}, set()
    
    name_to_id = {}
    all_ids = set()
    
    try:
        # Usar iterparse para processar o XML elemento por elemento
        context = ET.iterparse(stream, events=('start', 'end'))
        
        current_channel_id = None
        
        for event, elem in context:
            if event == 'start' and elem.tag == 'channel':
                current_channel_id = elem.get('id')
                if current_channel_id:
                    all_ids.add(current_channel_id)
                    # Mapear o ID normalizado para o ID original
                    name_to_id[normalize_name(current_channel_id)] = current_channel_id
            
            elif event == 'end' and elem.tag == 'display-name' and current_channel_id:
                if elem.text:
                    # Mapear o nome normalizado para o ID original
                    name_to_id[normalize_name(elem.text)] = current_channel_id
            
            elif event == 'end' and elem.tag == 'channel':
                elem.clear()
                current_channel_id = None
            
            elif event == 'end' and elem.tag == 'programme':
                elem.clear()
                
        return name_to_id, all_ids
    except Exception as e:
        print(f"Erro ao processar XML via streaming: {e}")
        return name_to_id, all_ids

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
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            channels, epg_urls = parse_m3u(response.text)
            all_channels.extend(channels)
            all_epg_urls.update(epg_urls)

    print(f"Total de canais encontrados: {len(all_channels)}")
    print(f"Total de URLs de EPG encontradas: {len(all_epg_urls)}")

    for epg_url in all_epg_urls:
        print(f"Processando EPG: {epg_url}")
        stream = download_stream(epg_url)
        if stream:
            name_to_id, ids = get_epg_data_streaming(stream)
            print(f"  -> Encontrados {len(ids)} canais e {len(name_to_id)} nomes neste EPG.")
            global_name_to_id.update(name_to_id)
            global_epg_ids.update(ids)
            if hasattr(stream, 'close'):
                stream.close()

    print(f"Total de IDs de canais únicos no EPG: {len(global_epg_ids)}")

    # Ajustar tvg-id se necessário
    updated_count = 0
    for channel in all_channels:
        current_id = channel.get('tvg-id', "")
        tvg_name = channel.get('tvg-name', "")
        name = channel.get('name', "")
        
        # Se o tvg-id atual não for válido no EPG, tentamos encontrar pelo nome normalizado
        if not current_id or current_id not in global_epg_ids:
            # Tentar encontrar pelo tvg-name normalizado primeiro, depois pelo nome do canal normalizado
            found_id = global_name_to_id.get(normalize_name(tvg_name)) or global_name_to_id.get(normalize_name(name))
            
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
            for meta in channel.get('metadata', []):
                f.write(f"{meta}\n")
            f.write(f"{channel['url']}\n")

    print(f"Arquivo salvo em: {args.output}")

if __name__ == "__main__":
    main()
