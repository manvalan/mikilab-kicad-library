# API della libreria MIKILAB

Riferimento delle interfacce programmabili per gestire la libreria
(aggiungere, aggiornare, rimuovere e cercare componenti) e degli script
da riga di comando in `scripts/`.

Ci sono tre livelli. Usano tutti lo **stesso nucleo** (`import_component.py`),
quindi validazione, deduplicazione e rigenerazione delle lib-table sono
identiche:

| Livello | File | Per chi |
|---|---|---|
| API Python tipizzata | `scripts/mikilab_lib.py` | app/script Python che importano il modulo |
| CLI JSON | `scripts/mikilab_cli.py` | app non-Python (Swift, C++, Node, …) via subprocess |
| Script CLI "umani" | `scripts/add_component.py`, `import_*.py`, … | uso manuale da terminale |

---

## 1. API Python — `mikilab_lib`

### Import

```python
import sys
sys.path.insert(0, "/Users/michelebigi/Development/mikylab_kikad_library/scripts")
import mikilab_lib as mikilab
```

### Funzioni

#### `add_component(name, symbol, footprint=None, model=None, category=None) -> ComponentResult`

Aggiunge un nuovo componente.

| Parametro | Tipo | Note |
|---|---|---|
| `name` | `str` | Nome del componente, usato come nome base di simbolo e footprint. **Non** deve iniziare con `MIKILAB`: il prefisso viene aggiunto in automatico ai nickname. |
| `symbol` | `str \| Path` | File `.kicad_sym` sorgente (obbligatorio). |
| `footprint` | `str \| Path \| None` | File `.kicad_mod` sorgente (opzionale). |
| `model` | `str \| Path \| None` | Modello 3D `.step/.stp/.wrl/.wrz` (opzionale, **richiede** `footprint`). |
| `category` | `str \| None` | Una delle categorie (§5). Se omessa viene dedotta dal nome; se nessuna regola corrisponde, finisce in `other`. |

Solleva `MikilabError` se esiste già un componente con lo stesso nome.

```python
r = mikilab.add_component(
    name="TPS7A2018PDBVR",
    symbol="~/Downloads/TPS7A2018PDBVR.kicad_sym",
    footprint="~/Downloads/SOT95P280X145-5N.kicad_mod",
    model="~/Downloads/TPS7A2018PDBVR.step",
    category="power",
)
print(r.symbol_path, r.footprint_status)
```

#### `update_component(name, symbol, footprint=None, model=None, category=None) -> ComponentResult`

Upsert: sostituisce sul posto simbolo, footprint e modello di un
componente esistente. Se il componente non esiste, lo crea, come
`add_component`. Non sposta un componente tra categorie: per
ricategorizzarlo usa `remove_component` seguito da `add_component`.

#### `remove_component(name) -> ComponentResult`

Rimuove il simbolo, la libreria footprint (`.pretty`) e i modelli 3D
del componente. Solleva `MikilabError` se il componente non esiste.

#### `get_component(name) -> ComponentInfo | None`

Cerca un componente per nome esatto. Restituisce `None` se non lo trova.

#### `list_components(category=None) -> list[ComponentInfo]`

Elenca tutte le librerie simbolo. Si può filtrare per categoria; una
categoria sconosciuta solleva `MikilabError`.

#### `find_components(query, category=None) -> list[ComponentInfo]`

Ricerca per sottostringa sul nome, senza distinguere maiuscole e
minuscole. Si può filtrare per categoria.

```python
for c in mikilab.find_components("esp32-c6", category="microcontrollers"):
    print(c.sym_nickname, c.footprint_files)
```

### Tipi restituiti

#### `ComponentResult` (operazioni che modificano la libreria)

| Campo | Tipo | Significato |
|---|---|---|
| `name` | `str` | Nome del componente |
| `category` | `str` | Categoria effettiva (anche se dedotta) |
| `action` | `str` | `"add"`, `"update"` o `"remove"` |
| `symbol_path` | `str \| None` | Percorso relativo alla root della libreria |
| `footprint_path` | `str \| None` | Percorso del `.kicad_mod` importato |
| `footprint_status` | `str \| None` | `NEW`, `UPDATED`, `DUPLICATE` (riusato un footprint identico già presente) o `RENAMED_COLLISION` (stesso nome, contenuto diverso: importato con nome distinto) |
| `model_path` | `str \| None` | Percorso del modello 3D |
| `messages` | `list[str]` | Log leggibile di cosa è stato fatto |

#### `ComponentInfo` (query)

| Campo | Tipo | Esempio |
|---|---|---|
| `name` | `str` | `"VBPW34S"` |
| `category` | `str` | `"other"` |
| `symbol_path` | `str` | `"symbols/other/VBPW34S.kicad_sym"` |
| `sym_nickname` | `str` | `"MIKILAB_VBPW34S"` |
| `footprint_path` | `str \| None` | `"footprints/other/VBPW34S.pretty"` |
| `fp_nickname` | `str \| None` | `"MIKILAB_VBPW34S"` |
| `footprint_files` | `list[str]` | `["XDCR_VBPW34S"]` |
| `model_paths` | `list[str]` | `["3dmodels/other/VBPW34S.step"]` |

In uno schematico il simbolo si referenzia come
`<sym_nickname>:<nome simbolo>`, mentre un footprint come
`<fp_nickname>:<footprint_files[i]>`.

> Un `ComponentInfo` corrisponde a **un file** `.kicad_sym`. Le
> librerie "di categoria" (es. `LED.kicad_sym`, `ti.kicad_sym`)
> contengono molti simboli ma compaiono come una sola voce.

### Errori

Ogni errore previsto solleva `mikilab.MikilabError`: nome vuoto o con
prefisso `MIKILAB`, file sorgente mancante, `model` senza `footprint`,
categoria sconosciuta, componente già esistente o inesistente. Il
messaggio (`str(e)`) si può mostrare all'utente così com'è.

```python
try:
    mikilab.add_component(name="FOO", symbol="foo.kicad_sym")
except mikilab.MikilabError as e:
    print(f"Import fallito: {e}")
```

### Effetti collaterali

Ogni chiamata che modifica (`add`, `update`, `remove`):

1. copia o rimuove i file in `symbols/`, `footprints/` e `3dmodels/`;
2. aggiorna la proprietà `Footprint` del simbolo, che diventa
   `MIKILAB_<lib>:<footprint>`;
3. collega il modello 3D al footprint tramite `${MIKILAB}/3dmodels/...`;
4. rigenera `sym-lib-table`, `fp-lib-table`, `sym-lib-table.global` e
   `fp-lib-table.global`;
5. aggiunge righe a `MANIFEST.csv`.

**Non** tocca le tabelle globali di KiCad in
`~/Library/Preferences/kicad/10.0/`. Per vedere il nuovo componente in
KiCad, dopo l'import lancia `sync_global_tables.py` (§3.4).

---

## 2. CLI JSON — `mikilab_cli.py`

Espone le stesse operazioni dell'API Python per i programmi che la
invocano come sottoprocesso.

```
python3 scripts/mikilab_cli.py add    --name N --symbol PATH [--footprint PATH] [--model PATH] [--category CAT]
python3 scripts/mikilab_cli.py update --name N --symbol PATH [--footprint PATH] [--model PATH] [--category CAT]
python3 scripts/mikilab_cli.py remove --name N
python3 scripts/mikilab_cli.py get    --name N
python3 scripts/mikilab_cli.py list   [--category CAT]
python3 scripts/mikilab_cli.py find   --query TEXT [--category CAT]
```

### Contratto

- Su stdout esce **un solo** oggetto JSON; eventuale diagnostica va su
  stderr.
- In caso di successo l'output è `{"ok": true, "data": ...}`: `data`
  contiene un `ComponentResult`/`ComponentInfo` serializzato, oppure un
  array per `list`/`find`.
- In caso di errore l'output è `{"ok": false, "error": "messaggio"}`.
- Exit code `0` se e solo se `ok` è `true`. Se lo stdout è vuoto,
  il processo è andato in crash prima di stampare.

Esempio:

```
$ python3 scripts/mikilab_cli.py get --name VBPW34S
{
  "ok": true,
  "data": {
    "name": "VBPW34S",
    "category": "other",
    "symbol_path": "symbols/other/VBPW34S.kicad_sym",
    "sym_nickname": "MIKILAB_VBPW34S",
    "footprint_path": "footprints/other/VBPW34S.pretty",
    "fp_nickname": "MIKILAB_VBPW34S",
    "footprint_files": ["XDCR_VBPW34S"],
    "model_paths": ["3dmodels/other/VBPW34S.step"]
  }
}

$ python3 scripts/mikilab_cli.py get --name NOPE ; echo $?
{
  "ok": false,
  "error": "no component named 'NOPE' found"
}
1
```

### Da Swift

```swift
struct Reply<T: Decodable>: Decodable { let ok: Bool; let data: T?; let error: String? }
struct ComponentInfo: Decodable {
    let name, category, symbol_path, sym_nickname: String
    let footprint_path, fp_nickname: String?
    let footprint_files, model_paths: [String]
}

let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
p.arguments = [libRoot + "/scripts/mikilab_cli.py", "find", "--query", "esp32"]
let pipe = Pipe(); p.standardOutput = pipe
try p.run(); p.waitUntilExit()
let reply = try JSONDecoder().decode(Reply<[ComponentInfo]>.self,
                                     from: pipe.fileHandleForReading.readDataToEndOfFile())
```

### Da C++ (nlohmann::json)

```cpp
FILE* f = popen("python3 scripts/mikilab_cli.py list --category power", "r");
std::string out; char buf[4096];
while (size_t n = fread(buf, 1, sizeof buf, f)) out.append(buf, n);
int status = pclose(f);
auto j = nlohmann::json::parse(out);
if (!j["ok"]) throw std::runtime_error(j["error"].get<std::string>());
for (auto& c : j["data"]) std::cout << c["sym_nickname"] << "\n";
```

---

## 3. Script da riga di comando

Si lanciano tutti dalla root della libreria con `python3 scripts/<nome>.py`.
Ogni script accetta `--help`, tranne `sync_global_tables.py`.

### 3.1 Import

| Script | Uso |
|---|---|
| `add_component.py` / `import_component.py` | Import singolo: `--name N --symbol S [--footprint F] [--model M] [--category C] [--update] [--remove]` |
| `import_batch.py` | `--source DIR [--category C]`: una sottocartella per componente (nome cartella = nome componente), esattamente un `.kicad_sym` ciascuna |
| `import_snapeda.py` | `--zip FILE.zip [--name N] [--category C] [--update]`: zip "Download KiCad" di SnapEDA |
| `import_ultralibrarian.py` | `--zip FILE.zip [--name N] [--category C] [--update]`: zip KiCad di UltraLibrarian |

Regole comuni:
- senza `--update` l'import si rifiuta di sovrascrivere un componente
  esistente;
- i footprint sono deduplicati per contenuto (SHA256), non per nome;
- in caso di stesso nome con contenuto diverso, il footprint viene
  importato con un nome distinto e marcato `RENAMED_COLLISION`.

### 3.2 Generatori

| Script | Uso |
|---|---|
| `generate_castellated_edge.py` | `--pins N --pitch MM [--pad-dia 1.0] [--drill 0.5] [--category other]`: footprint a fila singola di pad castellati sul bordo scheda |

### 3.3 Verifica e report

| Script | Uso |
|---|---|
| `check_library.py` | Controllo completo: struttura, sintassi, duplicati, riferimenti 3D (inclusi i modelli standard `${KICAD10_3DMODEL_DIR}`, verificati sull'installazione locale di KiCad), lib-table, riferimenti simbolo→footprint. Exit code `0` = `RESULT: OK` |
| `find_missing_3d_models.py` | `[--category C] [--csv OUT.csv]`: componenti con footprint ma senza modello 3D, con link di ricerca SnapEDA, Octopart e UltraLibrarian |

### 3.4 Tabelle globali di KiCad

| Script | Uso |
|---|---|
| `generate_global_tables.py` | `[--env-var MIKILAB]`: rigenera solo `*.global` (con `${MIKILAB}` al posto di `${KIPRJMOD}`) |
| `sync_global_tables.py` | Rigenera `*.global` e aggiunge le voci `MIKILAB_*` mancanti alle tabelle globali di KiCad (`~/Library/Preferences/kicad/10.0/`), con backup automatico. **KiCad deve essere chiuso del tutto**, altrimenti alla chiusura sovrascrive le tabelle. Se lo trova aperto, si ferma |

---

## 4. Flusso tipico: aggiungere un componente e usarlo in KiCad

```bash
# 1. import (qui da SnapEDA)
python3 scripts/import_snapeda.py --zip ~/Downloads/VBPW34S.zip --category other

# 2. verifica
python3 scripts/check_library.py

# 3. chiudi KiCad, poi registra le nuove librerie a livello globale
python3 scripts/sync_global_tables.py

# 4. riapri KiCad: MIKILAB_VBPW34S:VBPW34S è disponibile in ogni progetto
```

Se salti il passo 3, il componente è nella libreria ma **non** compare
in KiCad: è il problema più comune dopo un import.

---

## 5. Categorie

`analog`, `audio`, `display`, `fpga_cpld`, `interface`, `logic`,
`mechanical`, `memory`, `microcontrollers`, `other`, `power`, `rf`.

Senza `category`, la categoria viene dedotta dal nome per parole chiave
(`lib_common.CATEGORY_RULES`). Per esempio `esp32` finisce in
`microcontrollers`, `tps7a` in `power`, `lcd` in `display`. Se nessuna
parola chiave corrisponde, la categoria è `other`.

## 6. Convenzioni dei percorsi

- Le lib-table locali usano `${KIPRJMOD}`, quelle `*.global` usano
  `${MIKILAB}`.
- I modelli 3D della libreria usano `${MIKILAB}/3dmodels/<cat>/<Nome>.step`.
- I modelli 3D standard di KiCad usano `${KICAD10_3DMODEL_DIR}/...`.
- Non ci sono mai percorsi assoluti: `check_library.py` li segnala come
  errore.
