# Dicionário de dados

Gerado por `python src/coleta.py dicionario`: descrição e tipo esperado vêm do dicionário oficial do Portal (`docs/dicionario_oficial.csv`); tipo inferido, % de vazios e domínio observado vêm de uma amostra dos arquivos em `data/bronze`. Na camada bronze tudo está como texto; a conversão de tipos é feita na limpeza.


## Bolsa Família - Pagamentos — `bolsa_familia_pagamentos`

| Coluna no arquivo | Nome padronizado | Descrição | Tipo esperado | Tipo inferido | Domínio | % vazios |
|---|---|---|---|---|---|---:|
| MÊS COMPETÊNCIA | `mes_competencia` | Ano e mês da folha de pagamento. | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202105 a 202110 | 0.0 |
| MÊS REFERÊNCIA | `mes_referencia` | Ano e mês a que a parcela se refere (pode ser anterior à folha: pagamento retroativo). | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202105 a 202110 | 0.0 |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | categórica (texto) | BA (12,7%); SP (11,5%); PE (8,2%); MG (7,8%); CE (7,5%); RJ (6,6%); MA (6,6%); PA (6,6%); PB (3,6%); PI (3,2%); AL (2,8%); AM (2,8%) | 0.0 |
| CÓDIGO MUNICÍPIO SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | código numérico (4 dígitos) | ex.: 0001 | 0.0 |
| NOME MUNICÍPIO | `nome_municipio` | Nome do município do beneficiário. | texto | texto livre | comprimento 3 a 38 | 0.0 |
| CPF FAVORECIDO | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | texto (CPF mascarado) | formato ***.NNN.NNN-** | 2.17 |
| NIS FAVORECIDO | `nis_favorecido` | NIS do beneficiário (número criado pela Caixa quando a pessoa entra em programa social ou no mercado formal). | código (11 dígitos) | código numérico (11 dígitos) | Número de Identificação Social; pode vir vazio | 0.0 |
| VALOR PARCELA | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | decimal (vírgula decimal) | 41 a 1.220 | 0.0 |
| — | `nome_beneficiario` | Nome do beneficiário. | texto | (não encontrado nos arquivos) | nome de pessoa (dado pessoal) |  |

## Auxílio Brasil — `auxilio_brasil`

| Coluna no arquivo | Nome padronizado | Descrição | Tipo esperado | Tipo inferido | Domínio | % vazios |
|---|---|---|---|---|---|---:|
| MÊS COMPETÊNCIA | `mes_competencia` | Ano e mês a que a parcela se refere. Atenção: no dicionário do Auxílio Brasil o sentido de competência e referência é o inverso do usado no Bolsa Família e no Novo Bolsa Família. | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202209 a 202302 | 0.0 |
| MÊS REFERÊNCIA | `mes_referencia` | Mês da folha de pagamento. | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202205 a 202302 | 0.0 |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | categórica (texto) | BA (12,2%); SP (12,1%); RJ (8,7%); PE (8,0%); MG (7,7%); CE (7,1%); PA (6,2%); MA (5,6%); PB (3,3%); PI (3,0%); AM (2,9%); RS (2,9%) | 0.0 |
| CÓDIGO MUNICÍPIO SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | código numérico (4 dígitos) | ex.: 0001 | 0.0 |
| NOME MUNICÍPIO | `nome_municipio` | Nome do município do beneficiário. | texto | texto livre | comprimento 3 a 40 | 0.0 |
| CPF FAVORECIDO | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | texto (CPF mascarado) | formato ***.NNN.NNN-** | 12.86 |
| NIS FAVORECIDO | `nis_favorecido` | NIS do beneficiário, quando existe. | código (11 dígitos) | código numérico (11 dígitos) | Número de Identificação Social; pode vir vazio | 0.0 |
| VALOR PARCELA | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | decimal (vírgula decimal) | 65 a 2.101 | 0.0 |
| — | `nome_favorecido` | Nome do beneficiário. | texto | (não encontrado nos arquivos) | nome de pessoa (dado pessoal) |  |
| — | `data_saque` | Data em que o saque foi feito. | data DD/MM/AAAA | (não encontrado nos arquivos) | datas dentro do período do arquivo |  |

## Novo Bolsa Família — `novo_bolsa_familia`

| Coluna no arquivo | Nome padronizado | Descrição | Tipo esperado | Tipo inferido | Domínio | % vazios |
|---|---|---|---|---|---|---:|
| MÊS COMPETÊNCIA | `mes_competencia` | Ano e mês da folha de pagamento. | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202306 a 202402 | 0.0 |
| MÊS REFERÊNCIA | `mes_referencia` | Ano e mês a que a parcela se refere. | inteiro AAAAMM (ano-mês) | ano-mês AAAAMM | 202303 a 202402 | 0.0 |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | categórica (texto) | SP (12,4%); BA (11,9%); RJ (8,3%); PE (7,8%); MG (7,7%); CE (7,0%); PA (6,3%); MA (5,7%); PB (3,3%); AM (3,0%); RS (3,0%); PI (2,9%) | 0.0 |
| CÓDIGO MUNICÍPIO SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | código numérico (4 dígitos) | ex.: 0001 | 0.0 |
| NOME MUNICÍPIO | `nome_municipio` | Nome do município do beneficiário. | texto | texto livre | comprimento 3 a 40 | 0.0 |
| CPF FAVORECIDO | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | texto (CPF mascarado) | formato ***.NNN.NNN-** | 15.92 |
| NIS FAVORECIDO | `nis_favorecido` | NIS do beneficiário, quando existe. | código (11 dígitos) | código numérico (11 dígitos) | Número de Identificação Social; pode vir vazio | 0.0 |
| VALOR PARCELA | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | decimal (vírgula decimal) | 25 a 3.914 | 0.0 |
| — | `nome_favorecido` | Nome do beneficiário. | texto | (não encontrado nos arquivos) | nome de pessoa (dado pessoal) |  |
| — | `data_disponibilizacao` | Data em que a parcela foi disponibilizada. | data DD/MM/AAAA | (não encontrado nos arquivos) | datas dentro do período do arquivo |  |
