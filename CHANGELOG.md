# 📋 Changelog

Todas as alterações relevantes do projeto serão documentadas neste arquivo.

O versionamento segue como referência o padrão Semantic Versioning.

## [1.0.0] - 2026-11-25

### ✨ Adicionado

- Interface gráfica desenvolvida com Streamlit.
- Processamento de documentos PDF.
- Reconhecimento óptico de caracteres.
- Integração com Tesseract OCR.
- Persistência de dados utilizando SQLite.
- Indexação documental.
- Pesquisa textual.
- Pesquisa aproximada.
- Pesquisa por termos relacionados.
- Atualização incremental do acervo.
- Instalador para Windows.
- Atalho para execução da aplicação.

### ⚙️ Infraestrutura e DevOps

- Automação de build utilizando PowerShell.
- Compilação do Tesseract 5.5.4.
- Gerenciamento de dependências com vcpkg.
- Configuração de MSVC e CMake.
- Empacotamento utilizando PyInstaller.
- Geração do instalador com Inno Setup.

### 🐛 Correções

- Ajustes na configuração Tcl/Tk.
- Correção de inconsistências no bundle OCR.
- Correção da inclusão dos avisos de licenciamento.
- Ajustes na cadeia de dependências nativas.

### 🔐 Segurança e integridade

- Verificação SHA-256.
- Auditoria de bibliotecas DLL.
- Preservação dos bancos originais.
- Separação entre dados reais e sintéticos.
- Inclusão dos avisos de licenciamento.

### 🧪 Validações

- Processamento OCR com dados sintéticos.
- Integridade dos bancos de dados.
- Geração do instalador.
- Instalação em outro computador.
- Funcionamento em ambiente independente.
- Atualização de novos acervos.

### 📊 Resultados iniciais

- 82 documentos.
- 683 páginas indexadas.
- Instalador versão 1.0.0.
- Atualização incremental validada.

## [Unreleased]

### 🚀 Planejado

- Formalização dos testes de recuperação.
- Automação de backup.
- Ampliação da cobertura de testes.
- Aprimoramento do reconhecimento de unidades.
- Implementação de CI/CD.
- Avaliação de busca semântica.
- Geração de relatórios técnicos.
