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
    return re.sub(r'[^a-z0-9]', '', name.lower())

def download_stream(url):
    """Baixa o conteúdo de uma URL e retorna um stream de bytes descompactado se necessário."""
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '')
        
        if url.endswith('.gz') or 'gzip' in content_type:
            return gzip.GzipFile(fileobj=BytesIO(response.content))
        
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
    
    epg_matches = re.findall(r'(?:x-tvg-url|url-tvg)="([^"]+)"', content)
    for match in epg_matches:
        urls = [u.strip() for u in match.split(',') if u.strip()]
        epg_urls.extend(urls)
    
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
            
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line_strip)
            if tvg_id_match:
                current_channel['tvg-id'] = tvg_id_match.group(1)
            
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', line_strip)
            if tvg_name_match:
                current_channel['tvg-name'] = tvg_name_match.group(1)
            
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

def process_epg_streaming(stream, target_ids, output_xml_root=None):
    """Extrai IDs e display-names de canais de um stream EPG XML e opcionalmente filtra para um novo XML."""
    if not stream:
        return {}, set()
    
    name_to_id = {}
    all_ids = set()
    
    try:
        context = ET.iterparse(stream, events=('start', 'end'))
        current_channel_id = None
        
        for event, elem in context:
            if event == 'start' and elem.tag == 'channel':
                current_channel_id = elem.get('id')
                if current_channel_id:
                    all_ids.add(current_channel_id)
                    name_to_id[normalize_name(current_channel_id)] = current_channel_id
                    
                    # Se estivermos filtrando e o ID estiver nos alvos, adicionamos ao novo XML
                    if output_xml_root is not None and current_channel_id in target_ids:
                        output_xml_root.append(elem)
            
            elif event == 'end' and elem.tag == 'display-name' and current_channel_id:
                if elem.text:
                    name_to_id[normalize_name(elem.text)] = current_channel_id
            
            elif event == 'end' and elem.tag == 'programme':
                prog_channel = elem.get('channel')
                if output_xml_root is not None and prog_channel in target_ids:
                    output_xml_root.append(elem)
                else:
                    elem.clear()
            
            elif event == 'end' and elem.tag == 'channel':
                if output_xml_root is None or elem.get('id') not in target_ids:
                    elem.clear()
                current_channel_id = None
                
        return name_to_id, all_ids
    except Exception as e:
        print(f"Erro ao processar XML via streaming: {e}")
        return name_to_id, all_ids

def main():
    parser = argparse.ArgumentParser(description="Unir listas M3U e ajustar tvg-id com base em EPG.")
    parser.add_argument("urls", nargs='+', help="URLs das listas M3U para unir.")
    parser.add_argument("-o", "--output", default="merged.m3u", help="Nome do arquivo de saída M3U.")
    parser.add_argument("-e", "--epg-output", help="Nome do arquivo de saída EPG (.xml.gz).")
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

    # Primeiro passo: coletar todos os nomes e IDs disponíveis nos EPGs
    for epg_url in all_epg_urls:
        print(f"Processando EPG (Mapeamento): {epg_url}")
        stream = download_stream(epg_url)
        if stream:
            name_to_id, ids = process_epg_streaming(stream, set())
            global_name_to_id.update(name_to_id)
            global_epg_ids.update(ids)
            if hasattr(stream, 'close'):
                stream.close()

    # Segundo passo: ajustar tvg-id na lista M3U
    updated_count = 0
    final_target_ids = set()
    invalid_ids = {"", "N/A", "Undefined", "None", "null"}

    for channel in all_channels:
        current_id = channel.get('tvg-id', "")
        tvg_name = channel.get('tvg-name', "")
        name = channel.get('name', "")
        
        if current_id in invalid_ids or current_id not in global_epg_ids:
            found_id = global_name_to_id.get(normalize_name(tvg_name)) or global_name_to_id.get(normalize_name(name))
            if found_id:
                if f'tvg-id="{current_id}"' in channel['info']:
                    channel['info'] = channel['info'].replace(f'tvg-id="{current_id}"', f'tvg-id="{found_id}"')
                else:
                    channel['info'] = channel['info'].replace('#EXTINF:-1', f'#EXTINF:-1 tvg-id="{found_id}"')
                channel['tvg-id'] = found_id
                updated_count += 1
        
        if channel['tvg-id'] and channel['tvg-id'] not in invalid_ids:
            final_target_ids.add(channel['tvg-id'])

    print(f"Total de tvg-ids atualizados: {updated_count}")

    # Terceiro passo: se solicitado, gerar o EPG filtrado
    if args.epg_output:
        print(f"Gerando EPG filtrado para {len(final_target_ids)} canais...")
        new_epg_root = ET.Element("tv", {"generator-info-name": "tvmergeepg"})
        
        for epg_url in all_epg_urls:
            print(f"Filtrando EPG: {epg_url}")
            stream = download_stream(epg_url)
            if stream:
                process_epg_streaming(stream, final_target_ids, new_epg_root)
                if hasattr(stream, 'close'):
                    stream.close()
        
        # Salvar como .xml.gz
        with gzip.open(args.epg_output, 'wb') as f:
            tree = ET.ElementTree(new_epg_root)
            tree.write(f, encoding='utf-8', xml_declaration=True)
        print(f"EPG filtrado salvo em: {args.epg_output}")

    # Gerar o arquivo M3U final
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        if all_epg_urls:
            f.write(f'#EXTM3U x-tvg-url="{",".join(all_epg_urls)}"\n')
        
        for channel in all_channels:
            f.write(f"{channel['info']}\n")
            for meta in channel.get('metadata', []):
                f.write(f"{meta}\n")
            f.write(f"{channel['url']}\n")

    print(f"Arquivo M3U salvo em: {args.output}")

if __name__ == "__main__":
    main()
