# tvmergeepg

`tvmergeepg` é uma ferramenta de linha de comando (CLI) em Python que permite unir múltiplas listas M3U e ajustar automaticamente os `tvg-id` dos canais com base em arquivos EPG (Guia Eletrônico de Programação) no formato XML ou XML.gz. Isso garante que os canais em sua lista M3U tenham os `tvg-id` corretos para exibir a programação EPG.

## Funcionalidades

- **Unir listas M3U:** Combine várias listas M3U em um único arquivo de saída.
- **Extrair URLs de EPG:** Identifica e baixa automaticamente os arquivos EPG referenciados nas listas M3U (tanto `x-tvg-url` quanto `url-tvg`).
- **Processar EPG XML/XML.gz:** Suporte para arquivos EPG compactados (`.gz`) e não compactados.
- **Ajustar `tvg-id`:** Altera o `tvg-id` de um canal na lista M3U para corresponder a um `channel id` encontrado nos arquivos EPG, usando o nome do canal como referência quando o `tvg-id` original está ausente ou incorreto.

## Instalação

Você pode instalar `tvmergeepg` usando `pip`:

```bash
pip install tvmergeepg
```

## Como usar

Após a instalação, você pode usar o comando `tvmergeepg` diretamente no seu terminal.

### Uso Básico

Para unir uma ou mais listas M3U e gerar um arquivo de saída `merged.m3u`:

```bash
tvmergeepg <url_da_lista_m3u_1> <url_da_lista_m3u_2> ... -o merged.m3u
```

**Exemplo:**

```bash
tvmergeepg https://github.com/punkstarbr/STR-YT/raw/refs/heads/main/CANAIS%20LOCAIS.m3u -o minha_lista_final.m3u
```

### Opções

- `-o`, `--output`: Especifica o nome do arquivo de saída. O padrão é `merged.m3u`.

## Como funciona o ajuste de `tvg-id`

1. O `tvmergeepg` primeiro analisa todas as listas M3U fornecidas para extrair informações dos canais (nome, `tvg-id` existente, URL do stream) e todas as URLs de EPG referenciadas.
2. Em seguida, ele baixa e processa todos os arquivos EPG (XML ou XML.gz) para criar um conjunto de `channel id`s válidos.
3. Para cada canal na lista M3U, se o `tvg-id` atual estiver vazio, ausente ou não for encontrado nos arquivos EPG, o script tentará usar o **nome do canal** (extraído da linha `#EXTINF`) para encontrar uma correspondência nos `channel id`s do EPG.
4. Se uma correspondência for encontrada, o `tvg-id` do canal na lista M3U será atualizado para o `channel id` correspondente do EPG.

## Contribuição

Contribuições são bem-vindas! Sinta-se à vontade para abrir issues ou pull requests no repositório do GitHub.

## Licença

Este projeto está licenciado sob a licença MIT. Veja o arquivo `LICENSE` para mais detalhes.
