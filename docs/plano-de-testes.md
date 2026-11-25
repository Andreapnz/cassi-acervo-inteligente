# 🧪 Plano de Testes

## 1. Objetivo

Validar os requisitos funcionais, a integração entre componentes, a integridade dos dados e a distribuição da aplicação.

## 2. Escopo

- Processamento documental.
- Reconhecimento óptico.
- Pesquisa.
- Persistência.
- Atualização de acervos.
- Empacotamento.
- Instalação.
- Recuperação operacional.

## 3. Estratégia

| Tipo | Objetivo |
|---|---|
| Funcional | Validar funcionalidades |
| Integração | Validar comunicação entre componentes |
| Sistema | Validar a aplicação completa |
| Integridade | Verificar preservação dos dados |
| Instalação | Validar a distribuição |
| Recuperação | Validar retomada operacional |
| Regressão | Verificar impactos de alterações |

## 4. Casos de teste

### CT01 — Processar PDF digital

**Prioridade:** Alta

**Pré-condição:** PDF digital disponível.

**Passos:**

1. Adicionar o documento ao acervo.
2. Executar a atualização do índice.
3. Pesquisar uma expressão presente no documento.

**Resultado esperado:** Documento indexado e localizado na pesquisa.

### CT02 — Processar PDF digitalizado

**Prioridade:** Alta

**Pré-condição:** PDF digitalizado disponível.

**Passos:**

1. Adicionar o documento ao acervo.
2. Executar o processamento.
3. Pesquisar um texto reconhecível.

**Resultado esperado:** Conteúdo recuperado por OCR.

### CT03 — Processar PDF misto

**Prioridade:** Alta

**Pré-condição:** PDF contendo texto e páginas digitalizadas.

**Passos:**

1. Adicionar o documento.
2. Atualizar o índice.
3. Pesquisar informações das diferentes páginas.

**Resultado esperado:** Conteúdo recuperado por extração direta e OCR.

### CT04 — Pesquisa aproximada

**Prioridade:** Alta

**Pré-condição:** Acervo indexado.

**Passos:**

1. Informar uma expressão com variação textual.
2. Habilitar correspondências aproximadas.
3. Executar a pesquisa.

**Resultado esperado:** Apresentação de correspondências relevantes.

### CT05 — Integridade do banco

**Prioridade:** Crítica

**Passos:**

1. Registrar o estado inicial do banco.
2. Executar a operação prevista.
3. Verificar a integridade.
4. Comparar os resultados esperados.

**Resultado esperado:** Banco consistente e sem alterações indevidas.

### CT06 — Integridade do instalador

**Prioridade:** Crítica

**Passos:**

1. Calcular o SHA-256 do instalador.
2. Comparar com o hash oficial.

**Resultado esperado:** Hashes correspondentes.

### CT07 — Instalação independente

**Prioridade:** Crítica

**Passos:**

1. Copiar o instalador para outro computador.
2. Executar a instalação.
3. Abrir a aplicação.
4. Validar o funcionamento.

**Resultado esperado:** Aplicação instalada e operacional.

### CT08 — Atualização de novos acervos

**Prioridade:** Alta

**Pré-condição:** Aplicação instalada e operacional.

**Passos:**

1. Adicionar um novo PDF.
2. Abrir a aplicação.
3. Atualizar o índice.
4. Aguardar o processamento.
5. Pesquisar informações do novo documento.

**Resultado esperado:** Novo acervo disponível sem reinstalação.

### CT09 — Recuperação após reinicialização

**Prioridade:** Alta

**Passos:**

1. Fechar a aplicação.
2. Reiniciar o computador.
3. Abrir a aplicação.
4. Verificar os dados.
5. Executar uma pesquisa.

**Resultado esperado:** Aplicação operacional e dados preservados.

### CT10 — Funcionamento offline

**Prioridade:** Alta

**Passos:**

1. Desconectar a internet.
2. Abrir a aplicação.
3. Executar uma pesquisa.
4. Consultar um documento local.

**Resultado esperado:** Funcionalidades locais disponíveis.

## 5. Critérios de encerramento

- Cenários críticos executados.
- Resultados registrados.
- Defeitos críticos tratados.
- Evidências preservadas.
- Pendências documentadas.
