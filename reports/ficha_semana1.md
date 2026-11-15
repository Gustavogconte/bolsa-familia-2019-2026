# Ficha da proposta (semana 1)

## Tema

Do Bolsa Família ao Novo Bolsa Família: cobertura e valor da transferência de renda federal por município, de 2019
a 2026.

Entre 2019 e 2026 o principal programa federal de transferência de renda mudou de nome e de regras duas vezes,
passando do Bolsa Família (até outubro de 2021) para o Auxílio Brasil (novembro de 2021 a fevereiro de 2023) e depois
para o Novo Bolsa Família (a partir de março de 2023), e ainda teve a pandemia no meio. O projeto acompanha, município
a município, quantas famílias o programa atendeu e quanto pagou em cada fase.

## Fonte

| Fonte | O que fornece | Acesso e licença |
|---|---|---|
| Portal da Transparência do Governo Federal (CGU), bases Bolsa Família - Pagamentos, Auxílio Brasil e Novo Bolsa Família | Um registro por pagamento, com mês de competência e de referência, UF, código SIAFI e nome do município, NIS e CPF mascarado do favorecido e valor da parcela | Download aberto em <https://portaldatransparencia.gov.br/download-de-dados>, e os termos de uso permitem o uso livre citando a fonte |
| IBGE / SIDRA (tabelas 6579 e 4709) | População estimada por município (2019 a 2021 e 2024 a 2026) e população do Censo 2022 | API pública do SIDRA, dados abertos |
| IBGE Localidades e Malhas Territoriais 2025 | Região, UF e geometria dos municípios para o mapa | Dados abertos do IBGE |
| kelvins/municipios-brasileiros | Correspondência entre o código SIAFI (Portal) e o código IBGE de 7 dígitos | GitHub, licença MIT |

Cada mês vem num ZIP com um CSV separado por ponto e vírgula, em latin-1, e a documentação de cada base (dicionário
oficial e layout) está em `docs/fontes.md`.

## Recorte

O período vai de janeiro de 2019 a setembro de 2026 (93 meses, 7 anos e 9 meses), porque setembro de 2026 é o último
mês publicado na data da coleta, e cobre as 27 UFs e os 5.570 municípios. As três fases do programa entram numa série
única, e novembro de 2021, que aparece nas duas primeiras bases com os mesmos pagamentos (a EDA mostrou que o arquivo
do Bolsa Família repete o do Auxílio Brasil), entra uma vez só, deduplicado por NIS e mês de referência.

A unidade de análise é o pagamento (linha da base) na camada silver e o município por mês na camada gold. Ficam fora
do recorte os saques do Bolsa Família, que não existem nas fases seguintes, e os outros benefícios do Portal.

## Perguntas de pesquisa

1\. Como evoluiu o número de famílias atendidas por 100 mil habitantes entre 2019 e 2026, e o que mudou em cada
transição (do Bolsa Família para o Auxílio Brasil em novembro de 2021 e do Auxílio Brasil para o Novo Bolsa Família
em março de 2023)?

2\. Quanto mudou o valor médio pago por família em cada fase do programa?

3\. Quais UFs e regiões têm a maior cobertura por habitante, e a diferença entre elas aumentou ou diminuiu ao longo do
período?

4\. Municípios pequenos têm cobertura proporcionalmente maior que os grandes? Qual a relação entre o porte
(população) e a taxa de famílias atendidas?

5\. Onde a cobertura mais cresceu entre 2019 e 2022, antes do Novo Bolsa Família, e esse crescimento se manteve
depois da criação dele?

## Estimativa de volume

Os números abaixo foram medidos na coleta (`data/metadados/coleta_arquivos.csv`).

| Base | Meses | Linhas | ZIP | CSV | Parquet |
|---|---|---:|---:|---:|---:|
| Bolsa Família - Pagamentos | jan/2019 a nov/2021 (35) | 494.674.172 | 9,1 GB | 55,6 GB | 5,0 GB |
| Auxílio Brasil | nov/2021 a fev/2023 (16) | 293.956.157 | 5,2 GB | 32,8 GB | 2,8 GB |
| Novo Bolsa Família | mar/2023 a set/2026 (43) | 859.194.021 | 15,2 GB | 94,9 GB | 8,1 GB |
| Total | 93 meses, 94 arquivos | 1.647.824.350 | 29,4 GB | 183,4 GB | 15,9 GB |

O recorte passa com folga dos mínimos da disciplina, com mais de 5 milhões de linhas, mais de 3 anos de cobertura e a
integração com o IBGE (população para as taxas e malha municipal para o mapa).

## Limitações já conhecidas

As bases não trazem idade, sexo nem a composição da família, então a análise fica no nível de município, mês e valor.
O IBGE não publicou estimativa municipal de população para 2022 e 2023, e o Censo 2022 (população contada) fica cerca
de 5% abaixo das estimativas dos anos vizinhos, então os dois anos são interpolados entre as estimativas de 2021 e
2024, como está no log de limpeza.

Os valores do Portal são nominais e, na base do Auxílio Brasil, o valor médio por família fica abaixo do piso legal da
época (R$ 400 e depois R$ 600), o que indica que parte da transferência não está nesse arquivo, então a comparação de
valores entre as fases leva essa ressalva e a correção pela inflação (IPCA) fica para a Parte 2. Os nomes dos
favorecidos foram retirados na coleta e o CPF, que já vem mascarado da fonte, não entra na base tratada (LGPD).
