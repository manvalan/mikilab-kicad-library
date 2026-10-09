# Guida rapida all'uso della libreria MIKILAB

> Questa guida è il complemento pratico, in italiano, al `README.md` alla
> radice del repository (che resta il riferimento tecnico completo, in
> inglese, con anche la provenienza dettagliata di ogni componente).
> Qui trovi solo "cosa fare" per i compiti di tutti i giorni.

## Cos'è

Una libreria KiCad personale e autonoma: simboli, footprint e modelli 3D
per i progetti hardware di MIKILAB. Non dipende da nessun'altra cartella
sul disco — puoi spostarla, zipparla o sincronizzarla su un'altra
macchina e continua a funzionare.

```
mikylab_kikad_library/
├── sym-lib-table / fp-lib-table   # registrano tutte le librerie
├── symbols/<categoria>/<Nome>.kicad_sym
├── footprints/<categoria>/<Nome>.pretty/<Nome>.kicad_mod
├── 3dmodels/<categoria>/<Nome>.step
├── scripts/                        # tutti gli strumenti descritti qui sotto
└── MANIFEST.csv                    # log di provenienza, una riga per file
```

Categorie: `analog`, `audio`, `display`, `fpga_cpld`, `interface`,
`logic`, `mechanical`, `memory`, `microcontrollers`, `other`, `power`,
`rf`.

## 1. Installare la libreria in KiCad

**Un solo progetto** — se il tuo `.kicad_pro` sta dentro
`mikylab_kikad_library/` (o copi `sym-lib-table`/`fp-lib-table` nella
cartella del progetto), KiCad li trova da solo grazie a `${KIPRJMOD}`.
Nessuna configurazione extra.

**Tutti i progetti** (consigliato per una libreria personale):

1. KiCad → Preferences → Configure Paths… → aggiungi `MIKILAB` che punta
   a `/Users/michelebigi/Development/mikylab_kikad_library`.
2. Rigenera le tabelle "globali" ogni volta che aggiungi componenti:
   ```
   python3 scripts/generate_global_tables.py
   ```
3. Copia le righe `(lib ...)` di `sym-lib-table.global` e
   `fp-lib-table.global` dentro le tabelle globali reali di KiCad
   (`~/Library/Preferences/kicad/10.0/sym-lib-table` e `fp-lib-table`),
   prima della parentesi finale. Vedi README §1 per lo script Python
   pronto che lo fa automaticamente (fai un backup prima).
4. Riavvia KiCad.

## 2. Usare simboli e footprint

- Schematico: cerca `MIKILAB_<Libreria>:<Simbolo>`, es.
  `MIKILAB_TPS7A2012PDBVR:TPS7A2012PDBVR`.
- PCB / footprint assignment: `MIKILAB_<Libreria>:<Footprint>`, es.
  `MIKILAB_SOT95P280X145_5N:SOT95P280X145-5N`.

Se un footprint non mostra il corpo 3D, è uno dei footprint importati da
fornitori il cui file STEP originale non è mai stato disponibile: il
riferimento morto è stato rimosso e l'originale è annotato in
`docs/removed_3d_model_refs.csv`. Pad, courtyard e serigrafia restano
corretti, manca solo la resa 3D.

## 3. Aggiungere un componente nuovo

**Un componente singolo** (hai già i file `.kicad_sym`/`.kicad_mod`/`.step`):

```
python3 scripts/add_component.py \
    --name TPS7A2018PDBVR \
    --symbol /percorso/TPS7A2018PDBVR.kicad_sym \
    --footprint /percorso/SOT95P280X145-5N.kicad_mod \
    --model /percorso/TPS7A2018PDBVR.step \
    --category power
```

`--footprint` e `--model` sono opzionali. `--category` è opzionale,
viene auto-rilevata dal nome (altrimenti finisce in `other`).

**Da uno zip SnapEDA** ("Download KiCad"):
```
python3 scripts/import_snapeda.py --zip ~/Downloads/PARTNUMBER.zip [--category ...]
```

**Da uno zip UltraLibrarian**:
```
python3 scripts/import_ultralibrarian.py --zip ~/Downloads/PARTNUMBER.zip [--category ...]
```

**Da JLCPCB/LCSC (EasyEDA)**, dal codice LCSC (serve `pip install easyeda2kicad`, nessun account):
```
python3 scripts/import_easyeda.py --lcsc C45044 [--name ...] [--category ...]
```

**Da una lista JSON di componenti** (scarica + importa + valida, vedi §3.1).

**Molti componenti insieme**: una sottocartella per componente dentro
una directory, poi:
```
python3 scripts/import_batch.py --source /percorso/batch_dir [--category power]
```

Tutti gli importer: rifiutano di sovrascrivere un componente esistente
(a meno di `--update`), deduplicano i footprint per contenuto (non per
nome file), rigenerano le lib-table da zero e appendono una riga a
`MANIFEST.csv` per ogni file toccato. Gli importer SnapEDA, UltraLibrarian
ed EasyEDA verificano inoltre che ogni pin del simbolo abbia un pad
corrispondente nel footprint e, se no, non importano nulla (forzabile con
`--allow-pin-mismatch`).

### 3.1 Scaricare e importare una lista JSON

```
python3 scripts/fetch_components.py --list test/componenti-librerie-cad.json --dry-run   # piano
python3 scripts/fetch_components.py --list test/componenti-librerie-cad.json             # esegue
```

Formato lista: come `test/componenti-librerie-cad.json` (`componenti[]`
con `ref`, `mpn`, `funzione`, `lcsc`, link `snapeda`/`ultralibrarian`);
campi opzionali per componente: `nome`, `categoria`, `salta: true`.
Le righe con lo stesso MPN vengono importate una volta sola.

Per ogni componente le sorgenti sono provate nell'ordine di
`source_order` finché una dà una parte **completa (simbolo + footprint)
con pin e pad corrispondenti**; altrimenti si passa alla successiva:

- `snapeda` / `ultralibrarian`: usa lo zip se c'è già in `download_dir`
  (anche scaricato a mano: basta che il nome file contenga l'MPN),
  altrimenti lo scarica col browser (Playwright, opzionale:
  `python3 -m pip install playwright && python3 -m playwright install chromium`).
  Il browser è visibile: se un passaggio automatico non riesce (captcha,
  scelta formato) lo completi a mano e il download viene catturato.
  Senza Playwright si apre la pagina nel browser di sistema e lo script
  attende lo zip in `download_dir`;
- `easyeda`: converte il codice `lcsc` con `easyeda2kicad`.

I componenti già in libreria vengono saltati (`--update` per
reimportarli). Alla fine: lib-table rigenerate, controllo orientamento
dei modelli 3D con un render isometrico per parte in `<lista>.renders/`
(controlla lì il pin 1: una rotazione di 180° non è rilevabile in
automatico), `check_library.py`, e report in `<lista>.report.json`.

**Credenziali**: gli account SnapEDA e UltraLibrarian stanno nel
**Portachiavi di macOS**, non in un file:
```
python3 scripts/credentials_keychain.py set snapeda          # chiede username e password
python3 scripts/credentials_keychain.py set ultralibrarian
python3 scripts/credentials_keychain.py status
```
La password si digita nel prompt di `security` di macOS: non compare a
schermo, non passa dalla riga di comando e non viene scritta altrove.

`credentials.json`, nella radice della libreria, resta per le impostazioni:
sorgenti, cartelle, `componentvault.shared_dir`. È in `.gitignore` (lo
script si rifiuta di partire se risulta tracciato da git). Si crea dal
modello vuoto:
```
cp credentials.example.json credentials.json && chmod 600 credentials.json
```
Se un account non è né nel Portachiavi né nel file, il login si fa a mano
nella finestra del browser. Una password scritta in chiaro nel file
funziona ancora, ma lo script avvisa di spostarla nel Portachiavi.
Schema: `docs/credentials.schema.json`. In alternativa al percorso
predefinito: `MIKILAB_CREDENTIALS=/percorso/file.json` o `--credentials`.

### 3.2 Richieste dalle app (ComponentVault iPad/Mac)

Non c'è server. ComponentVault e questo Mac condividono una cartella, ad
esempio in iCloud Drive, che si sceglie nell'app in Impostazioni → Cartella
condivisa. L'app scrive le richieste in `kicad/jobs/<uuid>/request.json`.
Questo Mac le esegue con il worker:
```
python3 scripts/fetch_worker.py            # resta in ascolto (Ctrl-C per fermare)
python3 scripts/fetch_worker.py --once     # evade le richieste in coda ed esce
python3 scripts/fetch_worker.py --shared-dir ~/Library/Mobile\ Documents/com~apple~CloudDocs/ComponentVault
```
La cartella va indicata in `credentials.json`, nella sezione `componentvault`
(`shared_dir`), oppure con `--shared-dir`. Le app non usano mai
`credentials.json`, e le password dei fornitori non entrano nella cartella.

Per ogni richiesta il worker:
1. importa con `fetch_components.py`;
2. scrive l'esito in `status.json`;
3. mette accanto alla richiesta, per ogni componente, uno zip con i file
   KiCad e il render 3D.

Le modifiche alla libreria non vengono committate: rivedile e fai commit
come al solito.

Nella stessa cartella il worker scrive anche:
- l'**indice della libreria** (`kicad/library_index.json`, da
  `scripts/library_index.py`), all'avvio e dopo ogni richiesta;
- un heartbeat (`kicad/worker.json`), così l'app mostra "Mac con KiCad attivo".

ComponentVault usa l'indice anche offline, dalla copia locale:
- nella sezione "Libreria KiCad";
- nel progetto BOM (Libreria KiCad → "Scarica i mancanti").

Per scrivere solo l'indice: `python3 scripts/fetch_worker.py --index-only`.

Con iCloud Drive conviene che la cartella resti scaricata sul Mac (Finder →
tasto destro → "Mantieni scaricato"): il worker legge i file non ancora
scaricati, ma aspetta il download di ciascuno.

## 4. Aggiornare un componente esistente

Aggiungi `--update` allo stesso comando di import (qualunque sorgente:
file locali, SnapEDA, UltraLibrarian) per sostituire in-place
symbol/footprint/modello di un componente già presente, invece di
ottenere l'errore "esiste già":

```
python3 scripts/import_ultralibrarian.py --zip ~/Downloads/PARTNUMBER.zip --name NOME_ESISTENTE --update
```

`--update` non cambia la categoria di un componente — per ricategorizzare
serve `remove` + `add` nella nuova categoria (vedi §6).

## 5. Cercare / consultare componenti

Da riga di comando, senza scrivere Python:
```
python3 scripts/mikilab_cli.py list   [--category power]
python3 scripts/mikilab_cli.py find   --query tps22 [--category power]
python3 scripts/mikilab_cli.py get    --name TPS7A2018PDBVR
```
Ogni comando stampa un JSON `{"ok": true, "data": ...}` su stdout.

Da Python, per integrare in un altro programma:
```python
import sys
sys.path.insert(0, "/Users/michelebigi/Development/mikylab_kikad_library/scripts")
import mikilab_lib as mikilab

mikilab.list_components(category="power")
mikilab.find_components("tps22")
mikilab.get_component("TPS7A2018PDBVR")
```

## 6. Rimuovere / rinominare / ricategorizzare un componente

```
python3 scripts/mikilab_cli.py remove --name NOME
```
oppure `mikilab.remove_component(name="NOME")` in Python. Per spostare un
componente in un'altra categoria: `remove` seguito da un nuovo `add`
(`--update`) nella categoria corretta.

## 7. Controllare l'integrità della libreria

Da lanciare dopo ogni import/modifica manuale, e comunque periodicamente:
```
python3 scripts/check_library.py
```
Verifica: struttura delle cartelle, sintassi di simboli/footprint,
duplicati reali (per hash, non per nome), validità e portabilità dei
riferimenti ai modelli 3D, sintassi e coerenza di
`sym-lib-table`/`fp-lib-table`, e che ogni simbolo MIKILAB punti a un
footprint esistente (i modelli 3D standard `${KICAD10_3DMODEL_DIR}`
vengono verificati contro l'installazione locale di KiCad). Esce con
`RESULT: OK` se non ci sono errori; ogni warning è un nuovo problema da
guardare.

Per un elenco mirato dei componenti senza modello 3D (con link rapidi a
SnapEDA/Octopart/UltraLibrarian per cercarlo):
```
python3 scripts/find_missing_3d_models.py
```

## 8. Verificare che non ci siano percorsi assoluti o dipendenze esterne

La libreria non deve mai contenere percorsi assoluti né dipendere da
`kicad-personal-library`. Puoi verificarlo in qualsiasi momento con:
```
grep -R "/Users/$(whoami)" --include="*.kicad_mod" --include="*.kicad_sym" .
grep -R "kicad-personal-library" .
```
Entrambi i comandi devono restituire nessun risultato (a parte, per il
secondo, le menzioni storiche nel README).

## Riferimento rapido comandi

| Cosa vuoi fare | Comando |
|---|---|
| Installare/aggiornare in tutti i progetti | `python3 scripts/generate_global_tables.py` |
| Aggiungere un componente (file locali) | `python3 scripts/add_component.py --name ... --symbol ... [--footprint ...] [--model ...] [--category ...]` |
| Aggiungere da zip SnapEDA | `python3 scripts/import_snapeda.py --zip ...` |
| Aggiungere da zip UltraLibrarian | `python3 scripts/import_ultralibrarian.py --zip ...` |
| Aggiungere da JLCPCB/LCSC (EasyEDA) | `python3 scripts/import_easyeda.py --lcsc C...` |
| Scaricare e importare una lista JSON | `python3 scripts/fetch_components.py --list ... [--dry-run]` |
| Aggiungere molti componenti | `python3 scripts/import_batch.py --source ...` |
| Aggiornare un componente esistente | aggiungi `--update` al comando di import |
| Cercare un componente | `python3 scripts/mikilab_cli.py find --query ...` |
| Rimuovere un componente | `python3 scripts/mikilab_cli.py remove --name ...` |
| Controllare l'integrità della libreria | `python3 scripts/check_library.py` |
| Trovare componenti senza modello 3D | `python3 scripts/find_missing_3d_models.py` |
| Controllare orientamento modelli 3D | `python3 scripts/model_check.py --all` (o file `.kicad_mod`, `--render DIR`) |
