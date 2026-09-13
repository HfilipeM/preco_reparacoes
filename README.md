# Painel de Preços

Ferramenta interna para consultar rapidamente preços de reparação, sem teres de abrir o Excel sempre que precisas de um preço.

## Como está organizado

```
priceboard/
├── index.html              ← site público (GitHub Pages) - só consulta
├── historico.html          ← página pública de histórico
├── assets/                 ← estilo e lógica partilhada (pesquisa, filtros)
│   ├── style.css
│   ├── shared.js
│   ├── app.js
│   └── historico.js
├── data/
│   ├── precos.json         ← dados que o site público lê (publicados no GitHub)
│   └── historico.json      ← histórico que o site público lê
├── admin/                  ← painel de administração — CORRE SÓ NA TUA MÁQUINA,
│   │                          nunca é publicado/acedido pela internet
│   ├── admin.html
│   ├── admin.js
│   └── server.py           ← pequeno servidor local (Flask)
├── scripts/
│   └── iservices_sync.py   ← o scraper em si
├── start_admin.bat / .sh   ← arrancar o painel de administração
├── publish.bat / .sh       ← publicar as alterações no site público
└── requirements.txt
```

## Porque está dividido em "público" + "admin"

O GitHub Pages só serve páginas estáticas (HTML/CSS/JS) — não corre código no
servidor. Por isso:

- O **site público** (`index.html`) só consulta e pesquisa. Não tem botões de
  editar/apagar, porque não haveria onde gravar essas alterações.
- O **painel de administração** (pasta `admin/`) corre localmente no teu PC
  através de um pequeno servidor Python. Aqui sim tens editar, apagar,
  adicionar, e o botão de atualizar preços com barra de progresso.
- Depois de mexeres em algo no admin, corres `publish.bat` (ou `.sh`) para
  enviar essas alterações para o GitHub — só nesse momento é que o site
  público as passa a mostrar.

Isto significa: nunca precisas de guardar nenhuma password ou chave de acesso
dentro do site público, mesmo sendo um repositório público no GitHub.

## Configuração inicial (uma vez)

1. **Instalar Python** (3.9 ou mais recente), se ainda não tiveres.
2. Instalar as dependências:
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Criar um repositório no GitHub, colocar todos estes ficheiros lá dentro,
   e ativar o **GitHub Pages** (Settings → Pages → Branch: main → pasta `/root`).
4. O site público fica acessível em algo como:
   `https://<o-teu-utilizador>.github.io/<nome-do-repositorio>/`

## Uso do dia a dia

### Ver/pesquisar preços
Abre o link do GitHub Pages. Pesquisa por marca, modelo, serviço ou
qualidade — os resultados aparecem instantaneamente à medida que escreves.

### Editar, apagar ou adicionar reparações
1. Corre `start_admin.bat` (Windows) ou `./start_admin.sh` (Mac/Linux).
2. O browser abre sozinho em `http://localhost:5050`.
3. Faz as alterações que precisares (tudo pede confirmação antes de gravar).
4. Corre `publish.bat` / `./publish.sh` para publicar no site público.

### Atualizar preços a partir do site da iServices (scraping)
No painel de administração local, clica em **"Atualizar preços"** no
canto superior direito. Confirma, e acompanha a barra de progresso — demora
tipicamente 5 a 15 minutos consoante o tamanho do catálogo. No final, corre
`publish.bat` para os novos preços aparecerem no site público.

**Recomendação de frequência:** já que os preços de reparação não mudam com
muita regularidade, correr isto **uma vez por mês** é mais que suficiente.
Podes correr mais vezes se quiseres, sem problema.

## Sobre os dados

Os dados vivem numa base de dados SQLite local (`data/iservices.db`, nunca
publicada — está no `.gitignore`), e são exportados para `data/precos.json`
e `data/historico.json` sempre que algo muda. Só estes dois `.json` é que
são publicados no GitHub — são pequenos, rápidos de carregar, e fáceis de
inspecionar num editor de texto se precisares de confirmar algo manualmente.

## Se quiseres integrar isto na consola da loja mais tarde

A base de dados SQLite (`data/iservices.db`) e os ficheiros `precos.json`/
`historico.json` já estão prontos a ser lidos por qualquer sistema (a
consola da loja incluída) — basta apontar para o mesmo ficheiro ou expor
os mesmos dados via uma API própria da consola.
