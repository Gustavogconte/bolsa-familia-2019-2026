# Fontes, licenças e documentação

Conferido em 04/11/2026. O comando `python src/coleta.py listar` relê a lista de arquivos do Portal a cada execução
e grava o que encontrou em `data/metadados/periodos_disponiveis.csv` (com a coluna `novos_desde_catalogo` para o que
foi publicado depois dessa conferência).

## Fonte principal: Portal da Transparência do Governo Federal (CGU)

- Página de dados abertos: <https://portaldatransparencia.gov.br/download-de-dados>
- Termos de uso: <https://portaldatransparencia.gov.br/termos-de-uso>. O Portal é de acesso livre e os termos
  permitem usar os dados "da forma que melhor lhe convier" (seção 3); não há uma licença nomeada (como CC ou
  ODbL). Ao publicar o trabalho, cite a fonte: *Portal da Transparência do Governo Federal — Controladoria-Geral
  da União (CGU)*, com a data de download registrada em `data/metadados/coleta_arquivos.csv`.
- Padrão de download: `https://portaldatransparencia.gov.br/download-de-dados/<slug>/<AAAAMM>`. O endereço
  redireciona para um ZIP no servidor `dadosabertos-download.cgu.gov.br`; a URL final de cada arquivo também fica
  registrada nos metadados.
- Formato: um ZIP por mês com um CSV separado por `;`, campos entre aspas, em latin-1/Windows-1252. A codificação
  real de cada arquivo é detectada e registrada (`encoding` em `coleta_arquivos.csv`).

### Bases do recorte

O recorte são as três fases do programa federal de transferência de renda, de jan/2019 ao último mês publicado. O
Portal publica o Bolsa Família desde jan/2013, mas a coleta começa em jan/2019.

| Base (`--bases`) | Nome no Portal | Meses no recorte | Primeiro | Último | Município identificado por | Dicionário |
|---|---|---:|---|---|---|---|
| `bolsa_familia_pagamentos` | Bolsa Família - Pagamentos | 35 | 201901 | 202111 | código SIAFI | [link](https://portaldatransparencia.gov.br/pagina-interna/603397-dicionario-de-dados-bolsa-familia-pagamentos) |
| `auxilio_brasil` | Auxílio Brasil | 16 | 202111 | 202302 | código SIAFI | [link](https://portaldatransparencia.gov.br/pagina-interna/605516-dicionario-de-dados-auxilio-brasil-saques) |
| `novo_bolsa_familia` | Novo Bolsa Família | 43 | 202303 | 202609 | código SIAFI | [link](https://portaldatransparencia.gov.br/pagina-interna/605543-dicionario-de-dados-novo-bolsa-familia) |

Nenhuma das três bases tem mês faltando no recorte.

Observações que afetam a análise (detalhes no README, seção *Dificuldades da fonte*):

- **Auxílio Brasil**: no dicionário oficial, `MÊS COMPETÊNCIA` é o mês a que a parcela se refere e
  `MÊS REFERÊNCIA` o mês da folha — o inverso do Bolsa Família e do Novo Bolsa Família. Os dados, porém, seguem a
  mesma convenção das outras bases: a competência é o mês do arquivo em 100% das linhas e, quando a referência é
  diferente, ela é anterior (pagamento retroativo). A troca descrita no dicionário aparece só no arquivo de nov/2021
  do **Bolsa Família**, cujo cabeçalho traz `MÊS REFERÊNCIA` antes de `MÊS COMPETÊNCIA`: nele, a coluna chamada
  "referência" é a que vale o mês do arquivo. A análise usa a regra dos dados nas três fases
  (`notebooks/02_eda.ipynb`, seção 2.3).
- **Nov/2021**: o mês aparece nas duas primeiras bases, porque o arquivo do Bolsa Família repete os pagamentos do
  Auxílio Brasil. A limpeza conta esse mês uma vez só (`reports/log_limpeza.md`).

## Segunda fonte (integração)

| Fonte | O que é | Para que serve | Endereço |
|---|---|---|---|
| IBGE/SIDRA, tabela 6579 | População residente estimada, por município e UF. Anos disponíveis em 28/08/2026: 2001–2006, 2008–2009, 2011–2021 e 2024–2026 | Denominador das taxas por 100 mil habitantes | <https://sidra.ibge.gov.br/tabela/6579> |
| IBGE/SIDRA, tabela 4709 | População residente do Censo 2022, por município e UF | Conferência da população de 2022 (não há estimativa municipal para 2022 nem para 2023; a limpeza interpola esses anos entre 2021 e 2024) | <https://sidra.ibge.gov.br/tabela/4709> |
| IBGE Localidades (API) | Municípios com UF, região, meso/microrregião e regiões geográficas | Variáveis derivadas (região etc.) | <https://servicodados.ibge.gov.br/api/v1/localidades/municipios> |
| IBGE, malhas municipais 2025 | Shapefiles de UF (14 MB) e municípios (226 MB) | Mapa coroplético | <https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2025/Brasil/> |
| kelvins/municipios-brasileiros (GitHub, licença MIT) | Código IBGE e código SIAFI de cada município | Ligar as bases do Portal (código SIAFI) ao IBGE (código de 7 dígitos) | <https://github.com/kelvins/municipios-brasileiros> |

A tabela SIAFI ↔ IBGE não é oficial, então a limpeza confere a correspondência: todos os 5.571 códigos SIAFI das
três bases combinaram com um código IBGE e a UF bate. Se a API de Localidades do IBGE estiver fora do ar, UF e região
também saem do próprio código IBGE: os 2 primeiros dígitos são a UF e o 1º dígito é a região (1 Norte, 2 Nordeste,
3 Sudeste, 4 Sul, 5 Centro-Oeste).

Os dados do IBGE são públicos; cite o IBGE como fonte, com a tabela e a data de acesso
(`data/metadados/referencias.csv`).

## Dicionários de dados

O dicionário oficial de cada base está linkado na tabela acima. O arquivo `docs/dicionario_oficial.csv` reúne esses
dicionários em português (descrição, tipo esperado e domínio de cada campo) e `python src/coleta.py dicionario` o
combina com o perfil observado nos arquivos baixados, gerando `docs/dicionario_dados.csv` e
`docs/dicionario_dados.md`.
