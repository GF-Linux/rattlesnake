# ratt db — arquivos nas colunas (0.12.0)

## Por quê

A pessoa guarda dados de laboratório: cromatogramas Sanger (`.ab1`, binário, 100–500 KB) e textos (`.txt`, FASTA).
Hoje a tela só aceita o que se digita; guardar um arquivo exige o psql com `\set` + `decode(…, 'base64')`. O ciclo dá
o caminho visual: carregar um arquivo num campo, ver a linha como `‹arquivo · 213 KB›` e salvar o arquivo de volta.

## Objetivos

1. Nos formulários **inserir** (`i`) e **alterar** (`m`, mudança "valor"), as colunas `bytea` e as de texto ganham o
   botão **arquivo…**, que abre um seletor de arquivo e põe o conteúdo como valor.
2. Na grade (tela Dados e aba dados da tela do banco), `bytea` aparece como `‹arquivo · 213 KB›`, sem trazer os bytes.
3. Na tela Dados, **`s`** salva no disco o valor da célula sob o cursor (`bytea` ou texto).
4. Todo SQL mostrado na tela (painel, prévia dos formulários, confirmações) abrevia literais longos; o `.sql` exportado
   (`w`) e o que vai ao banco levam o valor inteiro.

## Não-objetivos

- Guardar o nome original do arquivo em outra coluna automaticamente (a pessoa cria uma coluna `nome_arquivo` se quiser).
- Large objects (`lo_import`), arquivos no servidor (`pg_read_binary_file`), json/jsonb, listas.
- Abrir/visualizar o arquivo dentro do ratt.

## Anti-objetivos

- Despejar centenas de KB de hex na tela (painel, grade, amostra "antes → depois", toasts).
- Ler a tabela inteira de `bytea` só para mostrar uma página.
- Mudar o formato do rascunho em disco (um ratt 0.11 tem que continuar lendo o rascunho).

## Abordagens consideradas

- **A (escolhida): o conteúdo vai no rascunho.** O valor é texto como qualquer outro: `bytea` como `\x` + hex, texto
  como o próprio texto. O rascunho é uma foto: mexer no arquivo depois não muda nada. Formato do rascunho inalterado.
  Custo: rascunho em disco cresce (hex = 2× o arquivo) — limitado pelo teto de tamanho.
- B: o rascunho guarda só caminho + hash e lê o arquivo ao aplicar. Rascunho leve, mas `comandos()` passaria a depender
  de disco, o arquivo pode sumir ou mudar entre o ensaio e o aplicar, e o formato do rascunho mudaria.
- C: guardar só o caminho numa coluna de texto. Já é possível hoje (é digitar); não resolve o pedido.

## Decisões (minhas — a pessoa delegou)

1. **Quais colunas:** tipo normal `bytea` → qualquer arquivo; família `texto` (`filtro.familia == "texto"`) → só
   arquivo de texto UTF-8 (BOM UTF-8 removido; byte NUL ou UTF-8 inválido → recusa com a dica "use uma coluna bytea").
   Coluna desativada (identidade ALWAYS, gerada), booleana ou chave estrangeira: sem botão.
2. **Teto:** 10 MB por arquivo (10 × 1024 × 1024 bytes). Acima: recusa, com a dica de guardar o caminho numa coluna de
   texto. Motivo: o rascunho é JSON regravado a cada operação e o hex dobra o tamanho.
3. **Valor:** `bytea` → `"\\x" + dados.hex()`; texto → o texto decodificado. Entra em `Inserir.valores` /
   `Mudanca(tipo="valor")` como hoje; `filtro.literal` já cita (`E'\\x…'`), e o PostgreSQL lê o hex como `bytea`.
4. **O campo depois de carregar:** o `Input` mostra o rótulo `‹amostra1_F.ab1 · 213 KB›`; o conteúdo fica guardado no
   campo (mesmo padrão do `escolhido` da chave estrangeira): editar o texto do `Input` descarta o arquivo; `limpar()`
   (e "pôr e inserir outra") também.
5. **Seletor de arquivo:** modal com um `Input` de caminho e uma `DirectoryTree`; começa na última pasta usada nesta
   execução (na memória, nunca em disco), senão em `~`. Clicar/enter num arquivo da árvore preenche o caminho;
   **carregar** lê e devolve; erro aparece no próprio modal. Cabe em 80×24.
6. **Grade:** as leituras de página (`catalogo.pagina` e `catalogo.dados`) pedem `octet_length(col)` no lugar de cada
   coluna `bytea` e devolvem esse número embrulhado em `TamanhoArquivo(int)`; `tela._celula` o mostra como
   `‹arquivo · 213 KB›` (cinza). O tipo da coluna continua `bytea`. O SELECT didático do painel continua `SELECT *`.
7. **Tamanho legível:** `< 1024` → `"N B"`; `< 1024²` → `"N KB"` (arredondado, mínimo 1); senão `"N,N MB"` com uma
   casa (vírgula em pt, ponto em en).
8. **Salvar (`s`):** célula sob o cursor; coluna `bytea` ou família texto (outras → aviso). `bytea`: o valor é relido do
   banco pela chave primária da linha; tabela sem chave primária → recusa ("sem chave primária: não sei achar esta
   linha de novo"). Texto: o valor que a grade já tem. ∅ → aviso "vazio, nada para salvar". Modal com o caminho
   pré-preenchido `~/<tabela>_<valores da chave unidos por _>_<coluna><ext>`; se o arquivo existe, pergunta antes
   de sobrescrever (foco no cancelar); a pasta precisa existir. Fim: toast "salvo em <caminho> (213 KB)".
9. **Extensão sugerida** pelos primeiros bytes: `ABIF` → `.ab1`, `%PDF` → `.pdf`, `\x89PNG` → `.png`, `\xff\xd8\xff`
   → `.jpg`, `\x1f\x8b` → `.gz`, `PK\x03\x04` → `.zip`; senão `.bin`. Texto → `.txt`.
10. **SQL para ver:** uma função abrevia todo literal entre aspas simples (também `E'…'`) cujo conteúdo passa de 120
    caracteres: fica o começo (40 caracteres) + `…'` + ` /* arquivo · 213 KB */` (conteúdo `\x` + hex: tamanho = hex/2)
    ou ` /* texto · 12 KB */`. Precisa respeitar identificadores entre aspas duplas (um `'` dentro de `"a'b"` não abre
    literal) e o `''` / `\'` de escape. Vale no painel (`painel.py`), em `formularios.sql_bonito`, na confirmação
    (`formularios.py` ~790) e na amostra "antes → depois" (`_mostra` mostra `bytes` como `‹arquivo · N›` e corta texto
    longo).
11. **Versão** 0.12.0; CHANGELOG e README (seção da tela Dados) contam o botão, o `s` e o teto.

## O que fica para quem constrói

Nomes internos, a disposição exata do modal, as mensagens (sempre em `idioma.py`, pt e en), onde mora o código novo
(sugestão: um módulo `arquivos.py` para ler/rotular/abreviar, sem Textual).
