# Bolsa Família, Auxílio Brasil e Novo Bolsa Família (2019 a 2026) - Portal da Transparência

Projeto da disciplina Introdução a Big Data e Data Analytics, Etapa 1 (coleta, análise exploratória, limpeza,
visualizações e relatório). O recorte são as três fases do programa federal de transferência de renda, Bolsa
Família, Auxílio Brasil e Novo Bolsa Família, de jan/2019 a set/2026, com 1,65 bilhão de pagamentos baixados do
Portal da Transparência do Governo Federal (CGU). A coleta é automatizada e reprodutível e grava os dados em Parquet,
junto com os metadados da coleta, o dicionário de dados e a segunda fonte para integração (população do IBGE, tabela
de municípios SIAFI ↔ IBGE e malhas para o mapa). O relatório está em `reports/relatorio_etapa1.docx` (e `.pdf`).

O Portal publica o Bolsa Família desde jan/2013, mas a coleta começa em jan/2019, que é o início do recorte. Os meses
publicados de cada base e os links dos dicionários estão em [`docs/fontes.md`](docs/fontes.md).

## Estrutura

```
data/
  bronze/        Parquet bruto (todas as colunas como texto), particionado por ano e UF  ← recriado pelo script
  silver/        base tratada, um registro por pagamento, por ano e mês  ← recriada por src/limpeza.py
  gold/          município × mês e Brasil × mês, com população e taxa    ← recriada por src/limpeza.py
  metadados/     tabelas da coleta (vão para o Git): manifesto, arquivos, resumo, layouts, benchmark…
  _eda/          resumos mensais da análise exploratória (recriados por src/eda.py)
  _tmp/          temporários da conversão (apagados ao final)
docs/            fontes.md (fontes, licenças, períodos), dicionario_oficial.csv/.md (antes da coleta),
                 dicionario_dados.csv/.md (gerado depois da coleta, com tipos e domínios observados)
notebooks/       01_coleta.ipynb (coleta), 02_eda.ipynb (análise exploratória) e 03_visualizacoes.ipynb
                 (gráficos finais e respostas às 5 perguntas)
reports/         relatório, ficha da proposta, log de limpeza e figuras; coleta_resumo.md é gerado pelo script
src/             coleta.py (ponto de entrada), eda.py (resumos da EDA), limpeza.py (silver, gold e log) e
                 módulos auxiliares
```

## Dados

Os dados não vão junto com o repositório porque são grandes. A silver (base tratada) tem 11,9 GB, em 93 arquivos
Parquet (um por mês) de 90 a 154 MB, e o GitHub recusa arquivos acima de 100 MB (83 dos 93 passam disso), então ela
está no Google Drive, no arquivo `projeto_completo.zip` (12,7 GB):
<https://drive.google.com/drive/folders/1fjHL2YkfokT7_xsPKeI6F22XmaVdsBL7>. O zip tem o projeto inteiro, com o mesmo
código deste repositório, e os dados que os notebooks 02 e 03 leem: a silver, a gold, os resumos da EDA, as
referências do IBGE com as malhas e três meses da bronze. Depois de extrair o zip, esses dois notebooks rodam sem
precisar da coleta. Quem clonar o repositório pode usar só a pasta `data/` do zip, copiando para a pasta do projeto.

A bronze inteira (15,9 GB) não está no Drive e é recriada pelos scripts, na ordem de [Como reproduzir](#como-reproduzir),
o que leva algumas horas: na coleta deste trabalho, baixar e converter os 94 arquivos (29,4 GB) somou cerca de 3,5
horas, e a limpeza, que recria a silver e a gold a partir da bronze, leva cerca de 1 hora. O notebook 01 só lê os
metadados da coleta (`data/metadados`), que vão para o repositório, menos a última célula, que conta as linhas da
bronze inteira. Os três notebooks já vão executados, com as saídas.

## Como reproduzir

Requisitos: Python 3.10 ou mais novo e cerca de 35 GB livres em disco (ver [Volume](#volume-e-espaço-em-disco)).

**Windows (PowerShell)**, na pasta do projeto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se o PowerShell bloquear o `Activate.ps1`, dispense a ativação e chame o Python do ambiente direto:
`.venv\Scripts\python -m pip install -r requirements.txt` e `.venv\Scripts\python src\coleta.py listar`.

**Linux/macOS**: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

Depois, na ordem:

```bash
python src/coleta.py listar                  # bases do recorte, meses publicados hoje e lacunas
python src/coleta.py estimar                 # quanto vai baixar (só consulta o tamanho, não baixa)
python src/coleta.py referencias             # segunda fonte: IBGE + tabela SIAFI ↔ IBGE + malhas
python src/coleta.py baixar --remover-nomes  # as três bases, de jan/2019 ao último mês publicado
python src/coleta.py benchmark               # CSV × Parquet (tamanho e tempo de leitura)
python src/coleta.py dicionario              # docs/dicionario_dados.csv e .md (oficial × observado)
python src/coleta.py resumo                  # metadados e reports/coleta_resumo.md
```

Ou tudo de uma vez: `python src/coleta.py tudo --remover-nomes`.

**Análise exploratória (Etapa 2).** Com o Bolsa Família, o Auxílio Brasil e o Novo Bolsa Família na bronze:

```bash
python src/eda.py                            # resumos mês a mês em data/_eda/ (1 a 3 h na 1ª vez)
python src/eda.py --so mensal --periodos 202111 --refazer   # recalcula só um recorte
jupyter nbconvert --to notebook --execute --inplace notebooks/02_eda.ipynb
```

Rodar `src/eda.py` de novo só refaz o que mudou. Cada resumo guarda o número e o tamanho dos arquivos da bronze de
que saiu (`data/_eda/_fontes.json`). Meses com menos de 27 UFs na bronze ficam de fora, com aviso no log.

Em cada arquivo, a competência é a coluna que vale o mês do arquivo. No arquivo de nov/2021 do Bolsa Família os
rótulos vêm trocados, e o script troca as duas colunas, também com aviso no log.

O notebook só lê os resumos (e uma amostra de 1 milhão de linhas), então roda em poucos minutos.

**Limpeza (Etapa 3) e visualizações (Etapa 4).** Depois da EDA:

```bash
python src/limpeza.py                        # silver, gold e log de limpeza (cerca de 1 h; retoma)
jupyter nbconvert --to notebook --execute --inplace notebooks/03_visualizacoes.ipynb
```

A silver (`data/silver/pagamentos/ano=AAAA/mes=MM/`) tem um registro por pagamento, com nomes em snake_case, tipos
certos, o município do IBGE e as marcas `retroativo`, `cpf_ausente`, `chave_repetida` e `valor_extremo`. A gold
(`data/gold/`) tem município × mês e Brasil × mês, com famílias (NIS distintos), valores, população e a taxa por
100 mil habitantes. Cada decisão de limpeza, com as linhas afetadas, está em `reports/log_limpeza.md`. O notebook 03
salva as figuras em `reports/figuras/`, que entram no relatório.

**Retomada.** Rodar o mesmo comando de novo continua de onde parou: o que já foi convertido é pulado, um
download interrompido (queda de rede, Ctrl+C, computador desligado) continua do byte em que parou, e o que deu
erro é tentado de novo. Pode interromper e retomar quantas vezes precisar.

### Opções úteis de `baixar`

| Opção | Para quê |
|---|---|
| `--bases auxilio_brasil,novo_bolsa_familia` | escolher bases (padrão: as três; nomes em `listar`) |
| `--inicio 2023 --fim 202312` | recorte de meses (`AAAA` ou `AAAAMM`), sempre a partir de jan/2019 |
| `--ufs PR,SC` | grava só essas UFs (o download continua nacional); economiza disco |
| `--remover-nomes` | não grava a coluna com o nome do favorecido (LGPD); a coleta do projeto usou essa opção |
| `--manter-zip` | guarda os ZIPs em `data/_zips/` (por padrão são apagados depois da conversão) |
| `--simultaneos 2` | downloads em paralelo (padrão 2; não exagere com o servidor público) |
| `--memoria 4GB` | limite de memória da conversão (padrão: metade da RAM) |
| `--min-livre-gb 5` | para a coleta antes de deixar o disco com menos que isso livre |
| `--forcar` | reprocessa o que já foi convertido |

## Volume e espaço em disco

A coleta do recorte tem 94 arquivos mensais (35 do Bolsa Família, 16 do Auxílio Brasil e 43 do Novo Bolsa Família) e
1.647.824.350 linhas. Os ZIPs somam 29,4 GB, que descompactados dariam 183,4 GB de CSV, e em Parquet a bronze ficou
com 15,9 GB (8,7% do CSV). Cada mês tem um CSV de 1,5 a 2,4 GB, com 13 a 22 milhões de linhas.

O script apaga o ZIP e o CSV temporário de cada mês depois de converter, então durante a coleta o disco só precisa
ter espaço para a bronze e mais um CSV descompactado por vez. Somando a bronze, a silver (11,9 GB) e a gold, o projeto
ocupa cerca de 28 GB no fim. Se o disco não comportar tudo, dá para usar `--inicio/--fim` ou `--ufs`, e o script para
sozinho antes de lotar o disco. O `estimar` mostra o tamanho exato antes de baixar.

## O que o script faz com cada arquivo

1. **Descobre os períodos** lendo a página de download da base (o Portal lista os arquivos em
   `arquivos.push({ano, mes, dia})`) e fica só com os meses a partir de jan/2019. Se a página mudar, usa a lista
   conferida em 04/11/2026 (`src/catalogo.py`).
2. **Baixa com repetição e retomada**: até 6 tentativas com espera crescente (5 s, 15 s, 45 s…); retoma
   downloads parciais (HTTP `Range`, com `If-Range` para não emendar versões diferentes do arquivo); valida o
   ZIP (tamanho e índice) e recusa páginas HTML de erro; 404 vira "indisponível" e não é tentado de novo
   (use `--repetir-indisponiveis`). Registra URL, URL final, data, tamanho e SHA-256.
3. **Converte**: detecta a codificação (latin-1/Windows-1252 ou UTF-8) e o separador, corrige nomes de
   arquivo com acento dentro do ZIP, regrava o CSV em UTF-8 e o DuckDB grava **Parquet zstd**:
   `data/bronze/<base>/<base>/ano=AAAA/uf_sigla=UF/<período>.parquet`. Todas as colunas ficam como texto: a
   camada bronze é o dado bruto, e a conversão de tipos é feita na limpeza. UF vazia ou inválida vai para
   `uf_sigla=sem_uf`. Cada linha ganha `_periodo_arquivo` e `_arquivo_origem`.
4. **Não perde linhas em silêncio**: linhas malformadas (aspas sem fechar, campos a mais ou a menos) ficam fora
   do Parquet, mas são contadas por arquivo e uma amostra vai para `data/metadados/rejeitadas/`; o script também
   compara linhas lidas com linhas físicas, conta bytes nulos e caracteres irrecuperáveis.

## Como ler os dados

A bronze guarda o dado bruto, e o DuckDB lê todos os meses de uma base de uma vez:

```python
import duckdb
nbf = "data/bronze/novo_bolsa_familia/novo_bolsa_familia/**/*.parquet"
duckdb.sql(f"""
    SELECT ano, uf_sigla, count(*) AS parcelas
    FROM read_parquet('{nbf}', hive_partitioning = true, union_by_name = true)
    GROUP BY ALL ORDER BY ALL
""").df()
```

Com pandas, leia um recorte para não estourar a memória:

```python
import pandas as pd
df = pd.read_parquet("data/bronze/novo_bolsa_familia/novo_bolsa_familia",
                     filters=[("ano", "=", 2025), ("uf_sigla", "=", "PR")])
```

Para a análise, a silver (`data/silver/pagamentos/`) já tem as três fases juntas e os tipos certos, e a gold
(`data/gold/municipio_mes.parquet`) cabe inteira na memória.

## Metadados gerados (respostas para o relatório)

| Arquivo | Conteúdo |
|---|---|
| `data/metadados/manifesto_coleta.jsonl` | registro de cada arquivo baixado (fonte de verdade da retomada) |
| `data/metadados/coleta_arquivos.csv` | uma linha por base × período × tabela: data de download, URL, URL final, tentativas, tamanho do ZIP, SHA-256, codificação, nº de colunas e linhas, linhas rejeitadas, tamanho CSV e Parquet, tempos |
| `data/metadados/coleta_resumo_bases.csv` | totais por base: períodos, linhas, ZIP, CSV, Parquet e **redução CSV → Parquet** |
| `data/metadados/layouts_por_periodo.csv` | versões do layout de cada tabela e em que período cada coluna entrou ou saiu |
| `data/metadados/periodos_disponiveis.csv` | o que o Portal publica hoje, com lacunas |
| `data/metadados/estimativa_volume.csv` | tamanho de cada arquivo antes de baixar |
| `data/metadados/benchmark_formatos.csv` | CSV × Parquet: tamanho e tempo de leitura em pandas e DuckDB |
| `data/metadados/referencias.csv` | downloads da segunda fonte (URL, data, linhas, tamanho, SHA-256) |
| `reports/coleta_resumo.md` | tudo acima em tabelas prontas para o relatório |

## Dificuldades da fonte

O tamanho foi a maior dificuldade. Cada mês tem um CSV de 1,5 a 2,4 GB, com 13 a 22 milhões de linhas, e o recorte
inteiro tem 1,65 bilhão, então a conversão lê o CSV em streaming com o DuckDB, sem carregar o arquivo na memória, e a
EDA e a limpeza trabalham um mês por vez. Na limpeza cada mês roda num processo separado, porque o Windows só devolve
a memória quando o processo termina e, com um processo só, cada mês ficava mais lento que o anterior.

O servidor de downloads tem limite de pedidos. Depois de 60 a 160 consultas seguidas ele passa a responder HTTP 405 a
tudo por alguns minutos (observado em 04/11/2026 com 4 consultas em paralelo), então o `estimar` faz 2 consultas por
vez, com pausa, e repete com espera crescente em 405, 429 e 5xx, e o `baixar` trata esses códigos como falha
temporária. O `listar`, o `estimar` e o `baixar` com `--bases` atualizam só as linhas das bases escolhidas em
`periodos_disponiveis.csv` e `estimativa_volume.csv`.

O programa mudou de nome duas vezes e o layout mudou em um arquivo. As três bases usam as mesmas 9 colunas, mas o
arquivo de nov/2021 do Bolsa Família traz `MÊS REFERÊNCIA` antes de `MÊS COMPETÊNCIA`, com os rótulos trocados, e
nele é a coluna chamada "referência" que vale sempre o mês do arquivo. O dicionário oficial do Auxílio Brasil descreve
essa mesma troca, mas os arquivos do Auxílio Brasil não seguem ela (conferido na EDA, seção 2.3), e o
`layouts_por_periodo.csv` mostra as mudanças arquivo a arquivo.

Novembro de 2021 aparece nas duas primeiras bases, porque o arquivo do Bolsa Família desse mês repete os pagamentos
do Auxílio Brasil, e a limpeza conta esse mês uma vez só (`reports/log_limpeza.md`). O Auxílio Brasil sozinho tem só
16 meses (nov/2021 a fev/2023), então o recorte junta as três fases do programa, que somam 93 meses sem nenhum
faltando.

As três bases identificam o município pelo código SIAFI, e não pelo código do IBGE. A ligação com o IBGE usa a tabela
do projeto kelvins/municipios-brasileiros, que não é oficial, então a limpeza confere a correspondência (os 5.571
códigos SIAFI combinaram com um código IBGE e a UF bate). Os arquivos vêm em Windows-1252 (latin-1), com `;` e campos
entre aspas, e no recorte nenhuma linha veio malformada, mas o script conta e separa as que aparecerem.

O IBGE não tem estimativa municipal para 2022 e 2023 na tabela 6579. O Censo 2022 (tabela 4709) é a população contada
e fica cerca de 5% abaixo das estimativas dos anos vizinhos, então como denominador ele criaria um salto falso nas
taxas. Por isso a limpeza interpola 2022 e 2023 entre as estimativas de 2021 e 2024 e usa o Censo só como conferência.

## Privacidade (LGPD)

Os arquivos trazem nome, NIS e CPF parcialmente mascarado de milhões de pessoas. São dados públicos, mas o trabalho
não precisa dos nomes, então a coleta usa `--remover-nomes` e a silver não guarda o CPF (só a marca `cpf_ausente`).
Nenhuma linha individual vai para o repositório: a silver e os três meses da bronze do zip, que têm o NIS das
famílias, ficam só no Google Drive. O dicionário gerado não mostra exemplos de valores dessas colunas.

## Fontes

Portal da Transparência do Governo Federal, da Controladoria-Geral da União (CGU), IBGE (SIDRA 6579 e 4709, Localidades
e malhas 2025) e kelvins/municipios-brasileiros (licença MIT). Detalhes, termos de uso e links em
[`docs/fontes.md`](docs/fontes.md).
