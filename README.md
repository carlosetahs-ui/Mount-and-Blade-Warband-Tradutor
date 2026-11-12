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

- 🔒 **Proteção dos códigos da engine:** Mascara e valida as variáveis do Warband (`{playername}`, `{s0}`, `{reg1}`, etc.) antes de aceitar uma tradução.
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
- **Formatações de Número e Porcentagem:** `%d`, `%s`, `%d%%`, `%.2f`, `%08d`, `%lld` e outros formatos printf com flags, largura e precisão. Argumentos sem índice mantêm sua ordem, inclusive entre condições e texto externo. Argumentos explicitamente indexados, como `%2$s`, podem mudar de posição quando largura e precisão também usam índices explícitos.
- **Condicionais e Flexões de Gênero:** `{sir/madam}`, `{his/her}`, `{reg1?men:man}` têm apenas os ramos textuais traduzidos, mantendo a estrutura lógica original.
- **Expressões aninhadas:** um scanner iterativo protege o bloco completo, sem o antigo limite de três níveis de chaves. O texto interno de expressões complexas permanece no original; o texto externo continua traduzível.
- **IDs e estrutura do CSV:** o prefixo antes do primeiro `|` nunca passa pelo motor, pelo glossário ou pela revisão. A escrita compara IDs, quantidade e ordem das linhas com o original.

### 2. Os 3 Planos de Execução:
1. **Plano A (Tokenização Integral):** Os códigos viram tokens neutros numerados (`⟦0⟧`, `⟦1⟧`, etc.) e a frase é enviada inteira ao motor para preservar o contexto.
2. **Plano B (Detecção e Rotação):** Se o motor suprimir ou duplicar tokens, alterar a ordem dos argumentos ou danificar condições e cores, a tradução é descartada e enviada a outro provedor configurado.
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
```

### 3. Instalação das Bibliotecas
Instale apenas as dependências do backend que você pretende usar:

#### Para usar o Argos Translate (100% Offline):
```bash
pip install argostranslate
```
*(O modelo de dados inglês-português é baixado automaticamente pelo script na primeira execução).*

#### Para usar o Translators (Motores Web: Bing, Google, etc.):
```bash
pip install translators
```

#### Para usar o Ollama (Inteligência Artificial Local):
1. Instale o Ollama em [ollama.com](https://ollama.com/).
2. Baixe o modelo recomendado no terminal:
```bash
ollama pull qwen2.5:7b
```

---

## ⚙️ Configuração (`config.toml`)

Copie o modelo de exemplo:
```bash
# Windows
copy config.example.toml config.toml

# Linux / Mac
cp config.example.toml config.toml
```

Edite o arquivo `config.toml`:
```toml
[mod]
# Caminho da pasta do mod (use aspas simples no Windows)
path = 'C:\Program Files (x86)\Steam\steamapps\common\MountBlade Warband\Modules\NomeDoMod'

source_lang = "en"        # Pasta de origem: languages/en
target_lang = "pt"        # Pasta gerada: languages/pt
output = "saida"          # Pasta onde os arquivos traduzidos serão gravados
install = false           # Se true, escreve direto na pasta do mod
keep_existing = true      # Mantém frases que você já traduziu na pasta de saída
checkpoint_every = 25     # Salva o CSV a cada N linhas processadas; Ctrl+C também salva
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

[revision]
ptbr_fix = true                    # Corrige PT-PT para PT-BR (vocabulário, gramática)
replacements_file = "substituicoes.txt"  # Arquivo com substituições personalizadas
backends = ["argos"]               # Aplica revisão apenas nestes backends (vazio = todos)
```

> 💡 **Dica para caminhos no Windows:**  
> Se a pasta do mod tiver caracteres especiais ou espaços complexos, você pode simplesmente copiar a pasta `languages` do mod para dentro do diretório do tradutor e definir `path = '.'` no `config.toml`.

### Validação, integridade e retomada

- Todos os caminhos relativos, inclusive `mod.path`, são resolvidos a partir da pasta do arquivo de configuração. O TOML pode estar em UTF-8 com ou sem BOM.
- Opções inválidas, expressões de `skip_ids` incorretas, arquivos não encontrados e tentativas de usar a própria pasta de origem como saída produzem uma mensagem de erro antes da tradução.
- A revisão de português atua sobre o texto com os códigos e termos do glossário protegidos. As escolhas isoladas, como `{lord/lady}`, também são traduzidas, e o glossário é aplicado dentro delas. Condições aninhadas com chaves internas continuam preservadas no original.
- O glossário só atua nos trechos de texto fora dos códigos e tokens literais. Uma entrada que atravesse uma variável ou expressão não pode substituir esse bloco. Condições repetidas do mesmo seletor conservam a ordem de suas ocorrências e a estrutura individual de cada ramo; seus códigos não podem ser redistribuídos entre ocorrências.
- Candidatas só entram no cache depois da validação estrutural e da revisão correspondentes. Respostas antigas que alterem a ordem dos argumentos são ignoradas e consultadas novamente no motor. Delimitadores reservados introduzidos nos ramos traduzidos são normalizados somente no texto, preservando os códigos.
- Arquivos com NUL ou codificação UTF-16/UTF-32 são recusados; use UTF-8 ou Windows-1252. CRs isolados também são apontados, em vez de desaparecerem durante a leitura. O comando `check` detecta IDs fora da ordem original.
- Textos maiores que `max_chars` são divididos em trechos que respeitam o limite do serviço web, mesmo quando não contêm variáveis do jogo.
- Com `keep_existing = true`, traduções existentes são verificadas antes da reutilização. Linhas com códigos danificados são refeitas. Ao pressionar `Ctrl+C`, o CSV em andamento e o cache são salvos; execute o mesmo comando para continuar.
- Antes de iniciar o motor, todo o lote selecionado é lido e verificado quanto a IDs vazios ou repetidos. Um erro num CSV posterior é informado antes de baixar modelos ou substituir traduções anteriores.
- O cache registra a origem das linhas gravadas. Se o mod mudar o texto de um ID, ou o idioma de destino mudar, a próxima execução refaz a tradução correspondente. Falhas parciais em escolhas como `{lord/lady}` também são tentadas novamente. Mantenha o mesmo arquivo de cache para conservar esse histórico.
- Nas linhas geradas a partir desta versão, o histórico também identifica a saída e as regras usadas. Alterar o glossário, o conteúdo das substituições, o contexto ou a revisão atualiza as traduções automáticas. Com `--limit`, traduções posteriores continuam no arquivo com o histórico anterior até serem atualizadas em outra execução.
- Com `keep_existing = true`, uma edição manual válida é preservada nas mudanças de glossário ou revisão, desde que sua origem e idioma continuem iguais. Traduções importadas e históricos antigos sem identificação da saída também são preservados. Use `keep_existing = false` para refazer explicitamente essas linhas. A migração do cache mantém as entradas anteriores.
- A ordem das tags de cor é validada, inclusive dentro de escolhas e condições. Se o motor inverter abertura e fechamento, os planos alternativos tentam recuperar a tradução.
- `max_retries` limita as tentativas extras de cada pedido, incluindo bloqueios 429/403. Se todos os serviços estiverem em cooldown, o tradutor aguarda um deles ficar disponível antes da próxima tentativa.
- O cache separa os pares de idiomas e mantém a compatibilidade com o cache anterior de inglês para português. Use `reuse_cache_from = "none"` para consultar apenas o motor atual.
- No Ollama, o destino padrão acompanha `engine.target_code` (ou `mod.target_lang` quando o código não foi informado). `target_name` permite definir um nome de idioma explícito e é considerado na identificação do cache. Com `reuse_cache_from = "none"`, servidores Ollama diferentes têm caches separados; nesse modo, entradas Ollama antigas sem identificação de servidor deixam de ser consultadas. O modo `auto` continua permitindo reaproveitamento entre motores.
- As aspas da resposta Ollama são preservadas, inclusive quando não é possível distinguir pontuação do diálogo de aspas externas acrescentadas pelo modelo. Respostas de cache produzidas pela antiga limpeza de aspas deixam de ser reutilizadas nesse backend.
- O Argos verifica se há uma tradução instalada na direção solicitada, incluindo caminhos por idiomas intermediários. Quando falta um modelo utilizável, o erro é informado na inicialização. Com `auto_install = false`, nenhum catálogo ou pacote é baixado.
- Entradas de glossário sem TAB ou `=` geram avisos com arquivo e número da linha. Linhas em branco e comentários são ignorados.
- O arquivo de substituições também avisa sobre linhas malformadas e originais vazios. Um valor de destino vazio continua permitido para remover uma palavra.

## 🚀 Comandos e Modo de Uso

Analise arquivos, linhas, variáveis e tempo estimado, sem chamar um motor de tradução:

```bash
python mbtr.py scan --config config.toml
```

Gere sugestões de nomes de facções, tropas, castelos e itens para revisar e incorporar ao glossário:

```bash
python mbtr.py glossary --config config.toml
```

Teste o motor com até 30 linhas pendentes. `--limit` aceita apenas inteiros positivos:

```bash
python mbtr.py translate --config config.toml --limit 30
```

Traduza um arquivo específico. `--file` também funciona com `scan`, `check` e `glossary`:

```bash
python mbtr.py translate --config config.toml --file dialogs.csv
```

Traduza todos os arquivos selecionados:

```bash
python mbtr.py translate --config config.toml
```

Verifique arquivos e IDs ausentes, IDs extras ou repetidos, traduções vazias, códigos alterados e condições corrompidas:

```bash
python mbtr.py check --config config.toml
python mbtr.py check --config config.toml --file dialogs.csv
```

O relatório também consulta o histórico disponível, sem criar ou modificar o cache, para apontar origem ou idioma alterados, falhas parciais e regras desatualizadas em saídas automáticas. Esses casos retornam erro. Sem histórico, a verificação continua avaliando a estrutura dos arquivos.

O relatório conta linhas inalteradas com texto possivelmente pendente, desconsiderando IDs ignorados e nomes totalmente protegidos pelo glossário. Texto idêntico ao original não comprova qual idioma está sendo usado.

Os comandos retornam `0` em caso de sucesso e `1` em caso de erro. `translate` também retorna `1` quando alguma tradução falha e mantém o original; uma interrupção por `Ctrl+C` retorna `130`. Argumentos inválidos retornam `2`.

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


## 📝 Sistema de Revisão Pós-Tradução
O tradutor inclui um sistema automático de revisão que corrige inconsistências comuns após a tradução:

Correções Automáticas PT-PT → PT-BR:
Vocabulário: partilhar → compartilhar, equipa → equipe, facto → fato, ecrã → tela, etc.
Grafias antigas pré-acordo ortográfico: acção → ação, direcção → direção, protecção → proteção, etc.
Gerúndios: está a fazer → está fazendo
Pronomes enclíticos: impressionam-me → me impressionam (no meio da frase)

A conversão de gerúndios usa uma lista de verbos conhecidos. Palavras ambíguas ou desconhecidas permanecem intactas para evitar alterações como “está a mulher” → “está mulhendo”. As correções PT-BR só se aplicam ao destino português (`pt` ou `pt-BR`, incluindo `pt_BR`); as substituições personalizadas continuam disponíveis em outros idiomas nos backends selecionados.

Substituições Personalizadas:
Crie um arquivo substituicoes.txt com o formato errado = certo (ou tabulação) para adicionar suas próprias correções:
errado1 = correto1
errado2 = correto2
Este arquivo é aplicado após a tradução e antes das correções automáticas PT-PT → PT-BR.

Configuração por Backend:
Por padrão, a revisão é aplicada apenas ao backend Argos, que é o que mais mistura PT-PT com PT-BR. Você pode configurar quais backends devem receber a revisão:

[revision]
ptbr_fix = true                    # Ativa/Desativa correções PT-PT → PT-BR
replacements_file = "substituicoes.txt"  # Arquivo de substituições personalizadas
backends = ["argos"]               # Aplica revisão apenas nestes backends (vazio = todos)

Opções de backends:
- ["argos"] - Apenas Argos (recomendado)
- ["translators"] - Apenas motores web
- ["ollama"] - Apenas Ollama
- [] - Aplica em todos os backends
- ["argos", "translators"] - Aplica em múltiplos backends


## 🔧 Solução de Problemas Comuns (Troubleshooting)
1. Caracteres acentuados cortados ou invisíveis (Fontes customizadas)
Muitos mods de fantasia ou medievais (Perisno, Prophesy of Pendor, etc.) incluem fontes temáticas próprias (font.dds) que não possuem os glifos acentuados da língua portuguesa (ã, é, ç, etc.).
Solução Segura (Fallback para a Fonte Padrão):
Vá até a pasta do mod: Modules/<Nome_Do_Mod>/.
Na subpasta Data, renomeie font_data.xml para font_data.xml.bak.
Na subpasta Textures, renomeie font.dds para font.dds.bak.
O mod passará a carregar a fonte original do Warband, garantindo exibição perfeita de todos os acentos sem qualquer risco de bugs ou crashes.


2. Erro de caminho no Windows (FileNotFoundError)
Confira se `mod.path` aponta para a pasta que contém `languages/en`, e não para o executável do jogo ou para a própria pasta `en`. Espaços, acentos e `&` são aceitos nos caminhos. No TOML, use aspas simples para caminhos do Windows, como `path = 'D:\Jogos\Mount & Blade\Modules\Meu Mod'`.

Se usar um caminho relativo, ele parte da pasta de `config.toml`, mesmo quando o comando é executado de outro diretório. Também é possível copiar a pasta `languages` para junto da configuração e usar `path = '.'`.


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
├── revisao.py              # Sistema de revisão pós-tradução (PT-PT → PT-BR)
├── config.example.toml     # Modelo de configuração comentado
├── glossario.tsv           # Dicionário de termos forçados
├── nomes.txt               # Lista de nomes próprios mantidos no original
├── substituicoes.txt       # Substituições personalizadas de pós-tradução
├── tests/                  # Testes unitários de preservação sintática
│   └── test_mbtr.py
└── README.md


🧪 Testes Automatizados
Para rodar a bateria de testes:

```bash
python -m unittest discover -s tests
```

Os testes usam motores simulados e mods temporários, sem acessar serviços de tradução. Cobrem IDs e formato CSV, proteção de códigos e glossário, expressões profundamente aninhadas, ordem de printf e cores, condições repetidas, limites e rotação dos provedores, instalação do Argos, idiomas e aspas do Ollama, migração do cache, preservação de edições manuais, atualização de glossário e revisão, configuração, interrupção, atualização do mod, retomada de falhas parciais e gravação atômica. A qualidade linguística de um motor real deve ser conferida com uma tradução curta e revisão no jogo.
