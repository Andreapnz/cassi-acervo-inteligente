# 📜 Licenciamento e Conformidade

## 1. Objetivo

Registrar as atividades de avaliação das dependências e das condições de distribuição da aplicação.

## 2. Componentes de terceiros

A aplicação utiliza bibliotecas e ferramentas de terceiros relacionadas ao desenvolvimento, processamento documental, OCR, compilação e distribuição.

Cada componente deve ser avaliado conforme sua versão e licença aplicável.

## 3. Auditoria do OCR

Durante a preparação da distribuição, foi identificada uma dependência relacionada à biblioteca JBIG no pacote inicial do Tesseract.

Como estratégia de mitigação, foi realizada uma nova compilação utilizando vcpkg.

## 4. Validações realizadas

- Compilação do Tesseract 5.5.4.
- Inspeção de bibliotecas DLL.
- Verificação de executáveis e modelos.
- Verificação de indicadores da dependência JBIG.
- Inclusão de avisos de licenciamento.
- Preparação do código-fonte correspondente.

## 5. Obrigações de distribuição

A redistribuição deve considerar:

- Licenças das bibliotecas utilizadas.
- Obrigações aplicáveis à AGPL-3.0.
- Disponibilização do código-fonte correspondente.
- Avisos de direitos autorais.
- Condições de redistribuição dos componentes MSVC.
- Condições de utilização do Inno Setup.

## 6. Confidencialidade

Os documentos empresariais e bancos de dados reais não fazem parte do repositório demonstrativo.

As obrigações de licenciamento de software devem ser tratadas separadamente da proteção dos dados empresariais.

## 7. Status

A auditoria técnica do bundle OCR foi realizada.

A confirmação definitiva das condições de redistribuição permanece como ponto de acompanhamento.

Este documento não constitui parecer jurídico.
