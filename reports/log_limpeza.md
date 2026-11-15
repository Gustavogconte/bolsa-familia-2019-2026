# Log de limpeza (Etapa 3)

Gerado por `src/limpeza.py`. Bronze: 1.647.824.350 linhas e silver: 1.633.465.096 linhas.

| problema | decisão | justificativa | linhas afetadas |
|---|---|---|---:|
| Colunas com nomes do CSV (maiúsculas e acentos) e todas gravadas como texto na bronze | Renomear para snake_case e converter os tipos: meses para data, NIS para inteiro, valor para decimal, marcas para booleano | Permite calcular direto sobre a base, e na amostra da EDA a memória caiu 88% | 1.633.465.096 |
| Arquivo de nov/2021 do Bolsa Família com os rótulos de competência e referência trocados | Trocar as duas colunas nesse arquivo: a competência é a coluna que vale o mês do arquivo | É assim nos outros 93 arquivos, e o dicionário do Auxílio Brasil descreve essa mesma troca | 14.508.113 (em 1.812 os meses mudam) |
| Nov/2021 publicado nas duas bases (o arquivo do Bolsa Família repete os pagamentos do Auxílio Brasil) | Manter o arquivo do Auxílio Brasil e, do Bolsa Família, só as linhas de família e mês de referência que não estão nele (mesmo NIS e mês de referência) | É o mesmo pagamento publicado duas vezes, então somar dobraria o mês. O valor ficou fora da chave porque em 4,9 mil famílias ele muda de um arquivo para o outro, quase sempre de R$ 1 a R$ 35 a mais no do Bolsa Família | 14.355.279 removidas |
| Duplicados exatos (a mesma linha inteira mais de uma vez) | Remover, mantendo uma | Mesma parcela registrada duas vezes, e nenhum campo diferencia as cópias | 3.975 removidas |
| NIS ausente | Manter a linha com NIS nulo, fora da contagem de famílias | Não dá para atribuir a família, mas o valor foi pago e entra nos totais | 15.601 |
| CPF ausente (o CPF vem mascarado, ***.123.456-**) | Tirar a coluna de CPF da silver e manter a marca cpf_ausente | O CPF mascarado não é chave nem variável de análise, e tirar a coluna diminui os dados pessoais guardados (LGPD) | 190.307.921 |
| Mesmo NIS e mesmo mês de referência mais de uma vez no mês | Manter e marcar chave_repetida | Pode ser complemento legítimo, e as famílias são contadas por NIS distinto | 1.208.763 |
| Pagamento retroativo (referência anterior ao mês pago) | Manter, marcar retroativo e guardar meses_atraso | É pagamento real, e a gold separa o valor do mês do valor retroativo | 8.202.217 |
| Valores extremos da parcela | Manter e marcar valor_extremo quando o valor é menor que R$ 20 ou de R$ 3.000 para cima | Regra explícita, porque são raros, plausíveis (ajustes e acumulados) e não mudam a mediana | 17.495 |
| Município identificado pelo código SIAFI, não pelo código IBGE | Trocar pelo código IBGE e trazer nome, UF e região do IBGE (tabela SIAFI ↔ IBGE) | O código IBGE liga a base à população e à malha do mapa | 0 sem correspondência |
| Nome do município diferente do IBGE (grafia ou mudança de nome, ex.: Embu → Embu das Artes) | Usar o nome do IBGE | Mantém cada município com um nome só em toda a série | 8.671.021 |
| Valor da parcela ilegível | Viraria nulo | Nenhuma linha fora do formato 1.234,56 | 0 |
| Mesmo NIS pago em dois municípios no mesmo mês | Manter, e na gold a família conta nos dois municípios | Caso raro, registrado assim pelo Portal | 2.645 NIS |
| Parcela do Auxílio Brasil abaixo do piso legal (R$ 400 de dez/2021 a jul/2022 e R$ 600 depois) | Manter e declarar a limitação no relatório | Parte da transferência (benefício extraordinário) parece não estar nesta base | 248.712.499 |
| População municipal de 2022 e 2023 sem estimativa do IBGE | Interpolar em linha reta entre as estimativas de 2021 e 2024, e o Censo 2022 fica só como conferência | O Censo é contagem e fica ~5% abaixo das estimativas: criaria um salto falso na taxa | 11.142 município × ano |
| Município × mês da gold sem população | Nenhum tratamento foi necessário | Todos os códigos IBGE da base estão nas estimativas | 0 |
