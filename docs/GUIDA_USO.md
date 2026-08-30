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

Se un footprint non mostra il corpo 3D, quasi sempre è uno dei ~48 casi
noti "`${KISBLIB}` gap" (footprint importati da fornitori il cui file
STEP originale non era incluso nell'export) — vedi la sezione checkup o
`python3 scripts/check_library.py` per l'elenco. Non è un errore da
correggere subito: pad, courtyard e serigrafia restano corretti, manca
solo la resa 3D.

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

**Molti componenti insieme**: una sottocartella per componente dentro
una directory, poi:
```
python3 scripts/import_batch.py --source /percorso/batch_dir [--category power]
```

Tutti gli importer: rifiutano di sovrascrivere un componente esistente
(a meno di `--update`), deduplicano i footprint per contenuto (non per
nome file), rigenerano le lib-table da zero e appendono una riga a
`MANIFEST.csv` per ogni file toccato.

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
footprint esistente. Esce con `RESULT: OK` se non ci sono errori (i
warning sono lacune note e documentate, non bloccanti — vedi README §4).

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
| Aggiungere molti componenti | `python3 scripts/import_batch.py --source ...` |
| Aggiornare un componente esistente | aggiungi `--update` al comando di import |
| Cercare un componente | `python3 scripts/mikilab_cli.py find --query ...` |
| Rimuovere un componente | `python3 scripts/mikilab_cli.py remove --name ...` |
| Controllare l'integrità della libreria | `python3 scripts/check_library.py` |
| Trovare componenti senza modello 3D | `python3 scripts/find_missing_3d_models.py` |
