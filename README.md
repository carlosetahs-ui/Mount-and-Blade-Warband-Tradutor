# 🛡️ Mount & Blade: Warband – Mod Translator (`mb_tradutor`)

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Mount & Blade](https://img.shields.io/badge/Mount%20%26%20Blade-Warband%20%7C%20WSE2-darkred.svg)](https://store.steampowered.com/app/48700/Mount__Blade_Warband/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Backends](https://img.shields.io/badge/Backends-Argos%20%7C%20Ollama%20%7C%20Translators-green.svg)](#-motores-de-tradução-backends)

Ferramenta automatizada em Python desenvolvida especificamente para a tradução em lote de modificações de **Mount & Blade: Warband** e **Warband Script Enhancer 2 (WSE2)**.

O tradutor processa a estrutura nativa de localização (`Modules/<Mod>/languages/en/*.csv`), isola e blinda todas as variáveis e sintaxes da engine do jogo, traduz os textos através de múltiplos motores (offline ou web) e compila os arquivos resultantes diretamente na pasta `languages/pt/`.

---

## 📑 Sumário

- [Destaques do Projeto](#-destaques-do-projeto)
- [Blindagem de Códigos (3 Camadas de Defesa)](#-blindagem-de-códigos-3-camadas-de-defesa)
- [Motores de Tradução (Backends)](#-motores-de-tradução-backends)
- [Instalação e Requisitos](#-instalação-e-requisitos)
- [Configuração (`config.toml`)](#-configuração-configtoml)
- [Comandos e Modo de Uso](#-comandos-e-modo-de-uso)
- [Entendendo a Barra de Progresso](#-entendendo-a-barra-de-progresso)
- [Como Ativar a Tradução no Jogo](#-como-ativar-a-tradução-no-jogo)
- [Solução de Problemas Comuns (Troubleshooting)](#-solução-de-problemas-comuns-troubleshooting)
  - [Caracteres acentuados cortados ou invisíveis (Fontes customizadas)](#1-caracteres-acentuados-cortados-ou-invisíveis-fontes-customizadas)
  - [Erro de caminho no Windows (`FileNotFoundError`)](#2-erro-de-caminho-no-windows-filenotfounderror)
  - [Mod sem pasta languages/en](#3-mod-sem-pasta-languagesen)
- [Estrutura do Repositório](#-estrutura-do-repositório)
- [Testes Automatizados](#-testes-automatizados)
- [Licença e Contribuição](#-licença-e-contribuição)

---

## ✨ Destaques do Projeto

- 🔒 **Blindagem Total da Engine:** Garante que nenhuma variável do Warband (`{playername}`, `{s0}`, `{reg1}`, etc.) seja apagada ou corrompida durante a tradução.
- 💾 **Cache SQLite Persistente:** Gravação em tempo real a cada frase concluída (`cache.sqlite`). Você pode pausar com `Ctrl + C` e retomar sem retraduzir nada.
- 🔄 **Reaproveitamento entre Backends:** Comece traduzindo com um motor e continue com outro sem perder o progresso já salvo (`reuse_cache_from = "auto"`).
- 📖 **Glossário e Preservação de Nomes:**
  - `glossario.tsv`: Força traduções específicas de vocabulário militar e termos medievais.
  - `nomes.txt`: Impede a tradução de nomes próprios de facções, cidades, castelos e lordes.
  - Comando `python mbtr.py glossary`: Faz a varredura automática do mod e sugere nomes para proteção.
- 🛡️ **Anti-Ban Inteligente:** No backend web, inclui pausas humanas aleatórias, intervalos longos e rotação automática de motor ao detectar limites (`429/403`).
- ⚡ **Gravação Atômica:** Escrita segura em arquivo temporário antes da substituição final, evitando corrupção de arquivos em quedas de energia.

---

## 🛡️ Blindagem de Códigos (3 Camadas de Defesa)

Arquivos de localização do Warband seguem o padrão rígido `id|texto`. Qualquer erro em chaves ou tags de script pode fechar o jogo (*CTD*) ou corromper diálogos. O tradutor envolve cada frase em um sistema de tripla proteção:

### 1. Elementos Protegidos:
- **Variáveis de Script e Registradores:** `{playername}`, `{s0}`, `{s1}`, `{reg1}`, `{reg12}`, etc.
- **Quebras de Linha do Warband:** `^` (símbolo nativo da engine para quebra de linha em diálogos) e `\n`.
- **Tags de Cores do WSE2 / Warband:** `<c=0x...>` e `</c>`.
- **Formatações de Número e Porcentagem:** `%d`, `%s`, `%d%%`.
- **Condicionais e Flexões de Gênero:** `{sir/madam}`, `{his/her}`, `{reg1?men:man}` têm apenas os ramos textuais traduzidos, mantendo a estrutura lógica original.

### 2. Os 3 Planos de Execução:
1. **Plano A (Tokenização Integral):** Os códigos viram tokens neutros numerados (`⟦0⟧`, `⟦1⟧`, etc.) e a frase é enviada inteira ao motor para preservar o contexto.
2. **Plano B (Detecção e Rotação):** Se o motor suprimir ou duplicar tokens, a tradução é descartada e enviada a outro provedor configurado.
3. **Plano C (Tradução Cirúrgica de Fragmentos):** Em casos complexos, a frase é fatiada entre os códigos, traduzindo unicamente o texto puro e remontando a estrutura exata com as variáveis intactas.

---

## 🧠 Motores de Tradução (Backends)

Configure o motor desejado na chave `backend` do `config.toml`:

| Backend | Tipo | Requisitos de Hardware | Conexão | Ideal para... |
| :--- | :---: | :---: | :---: | :--- |
| **`argos`** | Redes Neurais Locais | Leve (roda em qualquer CPU moderna) | **100% Offline** | Mods gigantes, traduções longas e contínuas sem risco de bloqueio. |
| **`ollama`** | LLMs (Qwen, Llama, Mistral) | Requer GPU/CPU dedicada | **100% Offline** | Maior refinamento estilístico, fidelidade semântica e tom medieval. |
| **`translators`** | Motores Web (Bing, Alibaba, Google, Yandex) | Qualquer computador | **Online** | Traduções rápidas com rotação automática entre servidores web. |

---

## 📦 Instalação e Requisitos

### 1. Pré-requisitos
- **Python 3.11 ou superior** ([python.org](https://www.python.org/downloads/))
  > ⚠️ **No Windows:** Marque a opção **"Add python.exe to PATH"** durante a instalação.

### 2. Clonar ou Baixar o Projeto
```bash
git clone https://github.com/seu-usuario/mb_tradutor.git
cd mb_tradutor


3. Instalação das Bibliotecas
Instale apenas as dependências do backend que você pretende usar:

Para usar o Argos Translate (100% Offline):
pip install argostranslate
(O modelo de dados inglês-português é baixado automaticamente pelo script na primeira execução).

Para usar o Translators (Motores Web: Bing, Google, etc.):
pip install translators

Para usar o Ollama (Inteligência Artificial Local):
Instale o Ollama em ollama.com.
Baixe o modelo recomendado no terminal:
ollama pull qwen2.5:7b


⚙️ Configuração (config.toml)
Copie o modelo de exemplo:
# Windows
copy config.example.toml config.toml

# Linux / Mac
cp config.example.toml config.toml

Edite o arquivo config.toml:
[mod]
# Caminho da pasta do mod (use aspas simples no Windows)
path = 'C:\Program Files (x86)\Steam\steamapps\common\MountBlade Warband\Modules\NomeDoMod'

source_lang = "en"        # Pasta de origem: languages/en
target_lang = "pt"        # Pasta gerada: languages/pt
output = "saida"          # Pasta onde os arquivos traduzidos serão gravados
install = false           # Se true, escreve direto na pasta do mod
keep_existing = true      # Mantém frases que você já traduziu na pasta de saída
translate_gender_choices = true  # Traduz escolhas como {sir/madam}
skip_files = []           # Arquivos a ignorar, ex: ["item_modifiers.csv"]
skip_ids = []             # Regex de IDs para pular
context = "Mod medieval para Mount & Blade: Warband. Tom épico e imersivo."

[glossary]
file = "glossario.tsv"          # Termos fixos: original<TAB>tradução
do_not_translate = "nomes.txt"  # Nomes que não devem mudar (um por linha)

[engine]
backend = "argos"         # Escolha: "argos" | "translators" | "ollama"
target_code = "pt"

[engine.argos]
auto_install = true       # Baixa o pacote en->pt na primeira vez se não existir

[engine.translators]
providers = ["bing", "alibaba", "yandex", "google"]
max_per_minute = 20
max_per_hour = 600
pause = [1.5, 4.0]        # Pausa aleatória entre requisições (segundos)
cooldown = 600            # Espera 10 min caso um motor seja temporariamente bloqueado

[engine.ollama]
model = "qwen2.5:7b"
url = "http://localhost:11434"
temperature = 0.2
timeout = 180

[cache]
file = "cache.sqlite"
commit_every = 1          # Salva frase por frase em tempo real
reuse_cache_from = "auto" # Aproveita o cache mesmo se trocar de motor


💡 Dica para caminhos no Windows:
Se a pasta do mod tiver caracteres especiais ou espaços complexos, você pode simplesmente copiar a pasta languages do mod para dentro do diretório do tradutor e definir path = '.' no config.toml.


🚀 Comandos e Modo de Uso
1. Diagnóstico do Mod (scan)
Analisa a quantidade de arquivos, linhas totais, variáveis protegidas e tempo estimado:
python mbtr.py scan --config config.toml

2. Gerar Sugestões de Nomes para o Glossário (glossary)
Extrai automaticamente nomes de facções, tropas, castelos e itens para você alimentar o nomes.txt:
python mbtr.py glossary --config config.toml

3. Teste Rápido com Limite (--limit)
Traduz apenas as primeiras 30 linhas pendentes para validar o funcionamento do motor:
python mbtr.py translate --config config.toml --limit 30

4. Traduzir um Arquivo Específico (--file)
Foca a execução em um único arquivo de interesse (ex: diálogos):
python mbtr.py translate --config config.toml --file dialogs.csv

5. Tradução Completa
Inicia a tradução de todos os arquivos:
python mbtr.py translate --config config.toml
(Pode ser interrompido com Ctrl + C a qualquer instante sem perda de progresso).

6. Verificação de Integridade (check)
Varre os arquivos gerados para garantir que nenhuma linha nem código foi esquecido ou corrompido:
python mbtr.py check --config config.toml


📊 Entendendo a Barra de Progresso
Durante a execução, o terminal exibe o status detalhado em tempo real:
300/771  ok=234 cache=8 B=0 C=75 falhas=0  [argos:en-pt]
300/771: Linhas processadas / Total de linhas do arquivo atual.
ok=234: Frases traduzidas com sucesso no Plano A.
cache=8: Frases recuperadas instantaneamente do banco de dados local (cache.sqlite).
B=0: Casos em que o Plano B (troca de motor por token danificado) foi acionado.
C=75: Casos em que o Plano C (tradução cirúrgica de fragmentos) garantiu a integridade das tags.
falhas=0: Linhas irrecuperáveis (mantidas no original em inglês para não causar travamentos no jogo).
[argos:en-pt]: Motor ativo e par de idiomas configurado.


🎮 Como Ativar a Tradução no Jogo
Instale os arquivos no mod:
Copie os arquivos gerados em saida/<Nome_Do_Mod>/languages/pt para:
Mount&Blade Warband/Modules/<Nome_Do_Mod>/languages/pt/
Habilite a pasta base do idioma:
O Warband exige uma pasta correspondente na raiz do jogo para listar o idioma nas opções.
Crie uma pasta vazia (ou copie a pasta en) em:
Mount&Blade Warband/languages/pt/
Selecione o idioma:
No inicializador do Warband (Launcher), vá em Configure > Game > Language e selecione pt.
Ou altere diretamente em Documentos/Mount&Blade Warband/language.txt escrevendo pt.


🔧 Solução de Problemas Comuns (Troubleshooting)
1. Caracteres acentuados cortados ou invisíveis (Fontes customizadas)
Muitos mods de fantasia ou medievais (Perisno, Prophesy of Pendor, etc.) incluem fontes temáticas próprias (font.dds) que não possuem os glifos acentuados da língua portuguesa (ã, é, ç, etc.).
Solução Segura (Fallback para a Fonte Padrão):
Vá até a pasta do mod: Modules/<Nome_Do_Mod>/.
Na subpasta Data, renomeie font_data.xml para font_data.xml.bak.
Na subpasta Textures, renomeie font.dds para font.dds.bak.
O mod passará a carregar a fonte original do Warband, garantindo exibição perfeita de todos os acentos sem qualquer risco de bugs ou crashes.


2. Erro de caminho no Windows (FileNotFoundError)
Se o terminal apresentar erro ao tentar abrir arquivos dentro de pastas que contenham o caractere & (ex: M&B) ou acentos:
Mova o script para uma pasta com caminho simples (ex: C:\mb_tradutor); ou
Copie a pasta languages do mod para dentro do tradutor e use path = '.' no config.toml.


3. Mod sem pasta `languages/en`

Muitos mods distribuem apenas os arquivos `.txt` compilados na raiz e não trazem a pasta `languages/en` com os arquivos `.csv`. Se este for o seu caso, existem três formas de obter os arquivos:

Método 1: Extração nativa pelo WSE2 / Warband (Recomendado — Mais Rápido e Preciso)
O próprio motor do jogo possui uma ferramenta embutida que lê os arquivos compilados do mod e gera todos os `.csv` originais em inglês em segundos, sem precisar de programas externos:

1. **Ative o Modo de Edição (Edit Mode):**
   - **Com WSE2:** No jogo, abra `Esc` ➔ `Opções` ➔ marque `Modo de Edição`.
   - **Ou pelo Launcher:** Clique em `Configurar` (Configure) ➔ aba `Avançado` ➔ marque `Ativar Modo de Edição`.
2. **Inicie o mod em Modo Janela:**
   - Abra o mod desejado e pressione `Alt + Enter` para alternar para modo janela (caso esteja em tela cheia).
3. **Exporte as strings:**
   - Na barra de menu superior da janela do jogo, selecione:
     ```text
     View ➔ Create Language Template ➔ Default
     ```
   - O jogo congelará por alguns segundos enquanto extrai todas as falas, tropas, itens e quests.
4. **Localize e use os arquivos:**
   - Abra a pasta raiz da instalação do Warband/WSE2. Uma nova pasta chamada `new_language` terá sido criada.
   - Dentro dela estarão todos os `.csv` (`dialogs.csv`, `game_strings.csv`, `troops.csv`, etc.).
   - Mova ou copie esses arquivos para `languages/en` (dentro da pasta do mod ou na pasta do tradutor) e execute o script normalmente.

Método 2: Ferramentas da Comunidade
- Utilize programas clássicos como o **Warband Translation Tool** ou **Mouse&Brain**, que fazem o dump a partir dos arquivos `.txt` da pasta do mod.

Método 3: Module System (Para Desenvolvedores de Mods)
- Caso você tenha acesso ao código-fonte em Python do mod (Module System), ative a exportação de strings de localização nas variáveis do compilador antes de gerar os arquivos.


📁 Estrutura do Repositório
mb_tradutor/
├── mbtr.py                 # Interface CLI e orquestrador principal
├── mbloc.py                # Leitura/escrita de CSV e regex de proteção de códigos
├── translator.py           # Classes dos backends (Argos, Ollama, Translators) e Cache
├── config.example.toml     # Modelo de configuração comentado
├── glossario.tsv           # Dicionário de termos forçados
├── nomes.txt               # Lista de nomes próprios mantidos no original
├── tests/                  # Testes unitários de preservação sintática
│   └── test_mbtr.py
└── README.md


🧪 Testes Automatizados
Para rodar a bateria de testes e garantir que o ambiente está operando corretamente:
python -m unittest discover -s tests