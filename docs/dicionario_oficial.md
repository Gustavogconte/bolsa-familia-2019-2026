# Dicionário de dados oficial (Portal da Transparência)

Campos de cada base segundo os dicionários do Portal (links em `docs/fontes.md`), com descrição em português, tipo esperado e domínio. A coluna *nome padronizado* é o snake_case sem acentos que a limpeza vai usar; como os cabeçalhos dos CSV nem sempre repetem o nome do dicionário (ex.: "Ano/Mês Competência" aparece como "MÊS COMPETÊNCIA"), ela mostra o nome mais provável no arquivo. Depois da coleta, `python src/coleta.py dicionario` confere cada campo contra os arquivos reais e gera `docs/dicionario_dados.md` com os nomes definitivos, o tipo inferido e o domínio observado.


## Bolsa Família - Pagamentos

> O Portal publica a série desde jan/2013, mas o recorte começa em jan/2019. O arquivo de nov/2021 repete os pagamentos do Auxílio Brasil e traz os rótulos de competência e referência trocados (ver a EDA).

| Campo (dicionário oficial) | Nome padronizado | Descrição | Tipo esperado | Domínio |
|---|---|---|---|---|
| Ano/Mês Competência | `mes_competencia` | Ano e mês da folha de pagamento. | inteiro AAAAMM (ano-mês) |  |
| Ano/Mês Referência | `mes_referencia` | Ano e mês a que a parcela se refere (pode ser anterior à folha: pagamento retroativo). | inteiro AAAAMM (ano-mês) |  |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | 27 siglas: AC, AL, AM, AP, BA, CE, DF, ES, GO, MA, MG, MS, MT, PA, PB, PE, PI, PR, RJ, RN, RO, RR, RS, SC, SE, SP, TO |
| Código Município SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | códigos da tabela de municípios do SIAFI; converter para IBGE com data/bronze/referencias/municipios_siafi_ibge |
| Nome Município SIAFI | `nome_municipio` | Nome do município do beneficiário. | texto |  |
| CPF Beneficiário | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | formato ***.NNN.NNN-** (só os 6 dígitos centrais aparecem); pode vir vazio |
| NIS Beneficiário | `nis_favorecido` | NIS do beneficiário (número criado pela Caixa quando a pessoa entra em programa social ou no mercado formal). | código (11 dígitos) | Número de Identificação Social; pode vir vazio |
| Nome Beneficiário | `nome_favorecido` | Nome do beneficiário. | texto | nome de pessoa (dado pessoal) |
| Valor Parcela | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | esperado ≥ 0; valores negativos ou muito altos indicam ajuste/devolução ou erro |

## Auxílio Brasil

> Substituiu o Bolsa Família entre nov/2021 e fev/2023. No dicionário, MÊS COMPETÊNCIA e MÊS REFERÊNCIA têm significados trocados em relação ao Bolsa Família e ao Novo Bolsa Família, mas os dados não seguem essa troca: a competência é o mês do arquivo, como nas outras bases (conferido na EDA).

| Campo (dicionário oficial) | Nome padronizado | Descrição | Tipo esperado | Domínio |
|---|---|---|---|---|
| MÊS COMPETÊNCIA | `mes_competencia` | Ano e mês a que a parcela se refere. Atenção: no dicionário do Auxílio Brasil o sentido de competência e referência é o inverso do usado no Bolsa Família e no Novo Bolsa Família. | inteiro AAAAMM (ano-mês) |  |
| MÊS REFERÊNCIA | `mes_referencia` | Mês da folha de pagamento. | inteiro AAAAMM (ano-mês) |  |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | 27 siglas: AC, AL, AM, AP, BA, CE, DF, ES, GO, MA, MG, MS, MT, PA, PB, PE, PI, PR, RJ, RN, RO, RR, RS, SC, SE, SP, TO |
| Código Município SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | códigos da tabela de municípios do SIAFI; converter para IBGE com data/bronze/referencias/municipios_siafi_ibge |
| Nome Município | `nome_municipio` | Nome do município do beneficiário. | texto |  |
| CPF FAVORECIDO | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | formato ***.NNN.NNN-** (só os 6 dígitos centrais aparecem); pode vir vazio |
| NIS FAVORECIDO | `nis_favorecido` | NIS do beneficiário, quando existe. | código (11 dígitos) | Número de Identificação Social; pode vir vazio |
| NOME FAVORECIDO | `nome_favorecido` | Nome do beneficiário. | texto | nome de pessoa (dado pessoal) |
| DATA SAQUE | `data_saque` | Data em que o saque foi feito. | data DD/MM/AAAA | datas dentro do período do arquivo |
| VALOR PARCELA | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | esperado ≥ 0; valores negativos ou muito altos indicam ajuste/devolução ou erro |

## Novo Bolsa Família

| Campo (dicionário oficial) | Nome padronizado | Descrição | Tipo esperado | Domínio |
|---|---|---|---|---|
| MÊS COMPETÊNCIA | `mes_competencia` | Ano e mês da folha de pagamento. | inteiro AAAAMM (ano-mês) |  |
| MÊS REFERÊNCIA | `mes_referencia` | Ano e mês a que a parcela se refere. | inteiro AAAAMM (ano-mês) |  |
| UF | `uf` | Sigla da UF do beneficiário. | texto, sigla da UF (2 letras) | 27 siglas: AC, AL, AM, AP, BA, CE, DF, ES, GO, MA, MG, MS, MT, PA, PB, PE, PI, PR, RJ, RN, RO, RR, RS, SC, SE, SP, TO |
| Código Município SIAFI | `codigo_municipio_siafi` | Código do município do beneficiário no SIAFI (Sistema Integrado de Administração Financeira). | código SIAFI do município (4 dígitos, texto com zeros à esquerda) | códigos da tabela de municípios do SIAFI; converter para IBGE com data/bronze/referencias/municipios_siafi_ibge |
| Nome Município | `nome_municipio` | Nome do município do beneficiário. | texto |  |
| CPF FAVORECIDO | `cpf_favorecido` | CPF do beneficiário, quando existe. | texto (CPF mascarado) | formato ***.NNN.NNN-** (só os 6 dígitos centrais aparecem); pode vir vazio |
| NIS FAVORECIDO | `nis_favorecido` | NIS do beneficiário, quando existe. | código (11 dígitos) | Número de Identificação Social; pode vir vazio |
| NOME FAVORECIDO | `nome_favorecido` | Nome do beneficiário. | texto | nome de pessoa (dado pessoal) |
| DATA DISPONIBILIZAÇÃO | `data_disponibilizacao` | Data em que a parcela foi disponibilizada. | data DD/MM/AAAA | datas dentro do período do arquivo |
| VALOR PARCELA | `valor_parcela` | Valor da parcela do benefício. | decimal em R$ (vírgula decimal) | esperado ≥ 0; valores negativos ou muito altos indicam ajuste/devolução ou erro |
