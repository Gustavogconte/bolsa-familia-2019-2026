# Resumo da coleta

Gerado em 2026-11-15T19:01:36-03:00 a partir de `data/metadados/manifesto_coleta.jsonl`.

## Volume total

| Métrica | Valor |
|---|---|
| Arquivos baixados (base × período) | 94 |
| Tabelas convertidas (base × período × tabela) | 94 |
| Linhas em Parquet | 1.647.824.350 |
| Linhas malformadas (fora do Parquet) | 0 |
| Tamanho baixado (ZIP) | 29,4 GB |
| Tamanho dos CSV descompactados | 183,4 GB |
| Tamanho em Parquet (zstd) | 15,9 GB |
| Redução CSV → Parquet | 91,3% (11,6× menor) |

## Por base

| Base | Períodos | Primeiro | Último | Linhas | ZIP | CSV | Parquet | Redução CSV→Parquet | Indisp. | Erros |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| Bolsa Família - Pagamentos | 35 | 201901 | 202111 | 494.674.172 | 9,1 GB | 55,6 GB | 5,0 GB | 91,0% | 0 | 0 |
| Auxílio Brasil | 16 | 202111 | 202302 | 293.956.157 | 5,2 GB | 32,8 GB | 2,8 GB | 91,4% | 0 | 0 |
| Novo Bolsa Família | 43 | 202303 | 202609 | 859.194.021 | 15,2 GB | 94,9 GB | 8,0 GB | 91,5% | 0 | 0 |

## Lacunas nas séries publicadas

Meses que faltam entre o primeiro e o último arquivo listado no Portal (`data/metadados/periodos_disponiveis.csv`).

- Nenhuma lacuna nas bases selecionadas.

## Mudanças de layout entre períodos

| Base | Tabela | Versão | A partir de | Colunas novas | Colunas retiradas |
|---|---|---:|---|---|---|
| bolsa_familia_pagamentos | bolsa_familia_pagamentos | 2 | 202111 | - | - |

## Arquivos indisponíveis ou com erro

Nenhum.

## CSV × Parquet: tamanho e tempo de leitura

| Base | Tabela | Período | CSV | Parquet | pandas CSV (s) | pandas Parquet (s) | DuckDB CSV (s) | DuckDB Parquet (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| bolsa_familia_pagamentos | bolsa_familia_pagamentos | 202003 | 1,5 GB | 132,4 MB | - | - | 4.77 | 0.049 |
| auxilio_brasil | auxilio_brasil | 202112 | 1,6 GB | 144,2 MB | - | - | 3.651 | 0.157 |
| novo_bolsa_familia | novo_bolsa_familia | 202511 | 2,0 GB | 167,7 MB | - | - | 5.75 | 0.316 |

