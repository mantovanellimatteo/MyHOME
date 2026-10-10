# BTicino MyHome Modernized - Community Changelog & Feature Guide

Questo documento illustra nel dettaglio l'architettura tecnica, le nuove funzionalità introdotte e le ottimizzazioni di stabilità integrate nel componente personalizzato **MyHome** per **Home Assistant**, partendo dalla base ufficiale di **mantovanellimatteo** e arricchendola con l'intero ecosistema audio avanzato, la sintonizzazione radio FM RDS con loghi grafici, il Dynamic Proxy multi-room e i fix per l'anti-flooding del bus.

---

## Indice
1. [Caratteristiche Derivate da GreenGrassBlueOcean & Nostre Migliorie](#1-caratteristiche-derivate-da-greengrassblueocean--nostre-migliorie)
2. [Guida alla Configurazione del Decoder Pool (Dynamic Proxy)](#2-guida-alla-configurazione-del-decoder-pool-dynamic-proxy)
3. [Architettura Audio e Filodiffusione (WHO 22 / WHO 16)](#3-architettura-audio-e-filodiffusione-who-22--who-16)
4. [Sintonizzatore Radio FM F500, RDS e Auto-Deploy Loghi](#4-sintonizzatore-radio-fm-f500-rds-e-auto-deploy-loghi)
5. [Spegnimento Rapido a Singolo Tocco (Fast Turn-Off)](#5-spegnimento-rapido-a-singolo-tocco-fast-turn-off)
6. [Integrazione Citofonica ed Eventi Ducking (`myhome_intercom_event`)](#6-integrazione-citofonica-ed-eventi-ducking-myhome_intercom_event)
7. [Stabilità Gateway, Anti-Flooding e Workaround Socket di Comando](#7-stabilità-gateway-anti-flooding-e-workaround-socket-di-comando)
8. [Scansione Attiva del Bus per la Diffusione Sonora (WHO 22)](#8-scansione-attiva-del-bus-per-la-diffusione-sonora-who-22)
9. [Zero-Traffic Smart Reconnect e Risveglio Sincrono Radio](#9-zero-traffic-smart-reconnect-e-risveglio-sincrono-radio)
10. [Allineamento Formale al Protocollo OpenWebNet (Verificato con `openwebnet-mcp`)](#10-allineamento-formale-al-protocollo-openwebnet-verificato-con-openwebnet-mcp)
11. [Riconoscimento Pressione Fisica Frutti a Muro H4562 & Servizi Globali](#11-riconoscimento-pressione-fisica-frutti-a-muro-h4562--servizi-globali)

---

## 1. Caratteristiche Derivate da GreenGrassBlueOcean & Nostre Migliorie

Dall'eccellente lavoro del progetto *GreenGrassBlueOcean* abbiamo ereditato le fondamenta dell'architettura **Dynamic Proxy Multi-Room**, introducendo importanti evoluzioni architetturali:

### A. Dynamic Proxy Decoder Pool (`decoder_pool.py`)
* **Concetto**: Spesso in una casa domotica BTicino gli streamer moderni (es. Amazon Echo Dot, WiiM Mini, Linkplay, Raspberry HiFiBerry) sono in numero inferiore rispetto alle stanze cablate con la filodiffusione (ad esempio 1 o 2 streamer collegati agli ingressi AUX 2 e AUX 3 della matrice F441 per servire 5 o più zone).
* **Funzionamento**: Quando una stanza (es. Camera o Bagno) avvia una riproduzione multimediale (Spotify, Music Assistant, radio streaming via URL o servizio `play_media`), l'integrazione:
  1. Richiede un decoder libero al `DecoderPool`.
  2. Commuta automaticamente la matrice audio fisica BTicino sull'ingresso AUX corrispondente al decoder assegnato.
  3. Accende la zona amplificatore della stanza.
  4. Risveglia il decoder esterno se era in standby o spento.
  5. Inoltra il flusso audio e i metadati al decoder.
  6. Quando la stanza si spegne, il decoder viene rilasciato e torna disponibile per le altre stanze.

### B. Le Nostre Migliorie Rispetto all'Integrazione Originale
1. **Riscalatura Volume Non Lineare & Gain Staging**:
   - In BTicino il volume hardware è discreto su **31 passi** (`1` = minimo udibile, `31` = massimo volume).
   - In Home Assistant il volume è un numero decimale continuo da `0.0` a `1.0`.
   - Abbiamo introdotto una curva di compensazione logaritmica con parametro di **Pre-Gain configurabile**: evita che il decoder esterno saturi l'ingresso analogico della matrice BTicino o che il volume a valori bassi risulti impercettibile.
2. **Sincronizzazione Bidirezionale Istantanea**:
   - I controlli di volume dal bilanciere a muro BTicino aggiornano immediatamente lo slider di Home Assistant senza causare loop di retroazione (feedback loops).
3. **Gestione Fallback e Auto-Preemption**:
   - Se tutte le sorgenti AUX sono occupate, il sistema segnala chiaramente all'utente l'indisponibilità anziché bloccare il bus OpenWebNet.

---

## 2. Guida alla Configurazione del Decoder Pool (Dynamic Proxy)

L'integrazione mette a disposizione un **wizard guidato a due passaggi** nelle opzioni per configurare e instradare gli streamer esterni (Amazon Echo Dot, WiiM, Arylic, Linkplay, HiFiBerry, ecc.) collegati agli ingressi AUX della matrice audio BTicino (F441/F441M):

1. In Home Assistant, andare su **Impostazioni** ➔ **Dispositivi e Servizi** ➔ **MyHome (Modernized)**.
2. Cliccare su **Configura** (Options Flow) e selezionare **Configura Decoder Streaming (Dynamic Proxy)**.
3. **Passaggio 1: Modalità Instradamento Streamer (`decoders`)**:
   * **Sorgente Condivisa (Multiroom / Ingresso comune)**: Più stanze condividono dinamicamente gli streamer disponibili. Quando una stanza avvia la riproduzione (Spotify, radio web, TTS, Music Assistant), il primo decoder libero viene impegnato sulla relativa sorgente AUX e rilasciato automaticamente al termine dell'ascolto.
   * **Matrice Esclusiva (1 Streamer per Stanza)**: Mappatura punto-punto dedicata, dove specifici decoder sono vincolati in modo permanente a determinate zone/amplificatori.
4. **Passaggio 2: Configurazione Ingressi e Guadagno Streamer (`decoders_slots`)**:
   Fino a **4 Slot configurabili** corrispondenti agli ingressi fisici della matrice (AUX 1–4):
   * **Decoder Slot N (`media_player`)**: Selezionare l'entità Home Assistant corrispondente allo streamer collegato.
   * **Sorgente BTicino Slot N (1–4)**: Indicare l'ingresso AUX fisico della matrice (es. Sorgente `2` per AUX 2, Sorgente `3` per AUX 3).
   * **Pre-gain Slot N (Offset anti-fruscio 0–50%)**: Calibrazione del gain staging analogico. Elimina il fruscio di fondo degli stadi DAC sui punti sonori da incasso a basso volume, garantendo un'escursione ottimale del volume senza saturazioni o distorsioni.
5. Cliccare su **Invia**: Il decoder pool viene riavviato e aggiornato a caldo senza necessità di riavviare Home Assistant.

---

## 3. Architettura Audio e Filodiffusione (WHO 22 / WHO 16)

* **Amplificatori da Incasso H4562 (WHO 22)**: Gestione completa dello stato acceso/spento, volume e sorgente per ogni stanza cablata (indirizzi zonali OpenWebNet del tipo `3#Ambiente#Punto`).
* **Sistemi Stereo e Monocanale (WHO 16)**: Compatibilità con il comando di selezione sorgente standard BTicino.
* **Matrice Audio F441**: Commutazione sorgenti con sequenza a 3 trame asincrone verificate per garantire l'apertura corretta dei canali stereo.

---

## 4. Sintonizzatore Radio FM F500, RDS e Auto-Deploy Loghi

L'integrazione riconosce nativamente il sintonizzatore centrale BTicino **F500** (indirizzo `2#1`):

1. **Lettura Frequenza e Preset**:
   * Intercetta in tempo reale le frequenze trasmesse in MHz (es. `*#22*5#2#1*5*1*10150##` ➔ `101.5 MHz`).
   * Riconosce i preset salvati da **P1** a **P5** (`*#22*2#1*6*Preset##`).
2. **Auto-Wake sui Preset Radio**:
   * Se l'altoparlante di una stanza è spento o impostato su un ingresso AUX, cliccando su un preset FM (P1–P5) l'integrazione accende automaticamente l'amplificatore zonale, commuta la matrice su Tuner (`1`) e sintonizza la stazione desiderata in un unico gesto.
3. **Catalogo RDS Nazionale & Regionale (`radio_catalog.py`)**:
   * Mappatura automatica delle frequenze FM italiane sulle principali emittenti (RDS, Radio Deejay, RTL 102.5, Radio 105, Radio Nostalgia, Studio Più, ecc.).
   * Calcolo dinamico degli attributi `media_title`, `media_artist` (es. `Preset P5 • FM Stereo`) e `radio_stazione`.
4. **Bundling e Auto-Deployment dei 109 Loghi Out-of-the-Box**:
   * **Zero configurazione manuale per l'utente**: i **109 file grafici** ufficiali (107 PNG trasparenti + cover streaming Amazon e Spotify) sono inclusi direttamente nella cartella `logos/` dell'integrazione.
   * All'avvio del componente (`_async_ensure_radio_logos` in `__init__.py`), i file mancanti vengono copiati automaticamente e in background in `/config/www/loghi_radio/`, senza mai sovrascrivere eventuali personalizzazioni dell'utente.
   * **Percorso Web Configurabile (`radio_logos_path`)**: Nelle opzioni del sintonizzatore FM è possibile specificare un percorso personalizzato (default: `/local/loghi_radio`).
   * **Ricaricamento a caldo della cache**: Ogni modifica alle opzioni svuota e rigenera la cache dei loghi (`RadioCatalog.clear_cache()`), aggiornando i metadati delle entità all'istante.

---

## 5. Spegnimento Rapido a Singolo Tocco (Fast Turn-Off)

Sui sistemi BTicino con matrice F441 e amplificatori H4562, inviare il semplice comando `*22*0*WHERE##` spesso richiede due click poiché disconnette solo la sorgente o lascia l'amplificatore in pre-standby.

Abbiamo introdotto la sequenza di **spegnimento atomico combinato**:
1. Invio del telegramma di disconnessione sorgente: `*22*1#4#0*WHERE##`
2. Breve delay hardware calibrato a **80 millisecondi** per dare tempo al bus SCS di processare la trama.
3. Invio del telegramma di standby hardware dell'amplificatore: `*22*0#4#0*WHERE##`

**Risultato**: L'altoparlante si spegne istantaneamente e completamente al primo tocco da Home Assistant, senza pop audio e senza richiedere doppie pressioni.

---

## 6. Integrazione Citofonica ed Eventi Ducking (`myhome_intercom_event`)

L'integrazione intercetta il traffico OpenWebNet relativo alla videocitofonia e alla diffusione sonora:
* **Audio Ducking su Chiamata Citofonica**:
  Quando arriva una chiamata esterna o una chiamata intercomunicante, il sistema BTicino abbassa temporaneamente il volume della filodiffusione (`WHO 22` Dimensione `12`). L'integrazione cattura questo evento e scatena sul bus di Home Assistant l'evento **`myhome_intercom_event`** con payload `event: "ducking_start"`.
* **Ripristino Volume**:
  Al termine della conversazione viene emesso l'evento `event: "ducking_end"`.
* **Automazioni possibili**: Questo permette ad Home Assistant di mettere in pausa automaticamente Apple TV, streamer esterni o inviare notifiche luminose senza componenti aggiuntivi.

---

## 7. Stabilità Gateway, Anti-Flooding e Workaround Socket di Comando

Abbiamo allineato il componente ai fix ufficiali delle release **v1.3.1**, **v1.3.2**, **v1.4.0** e **v1.4.1** di Matteo Mantovanelli, aggiungendo una protezione mirata per il socket:

### A. Anti-Flooding Diagnostica Attuatori (da v1.3.1) - Risoluzione Doppio Click sulle Luci
* **Problema originario**: I frame periodici automatici degli attuatori (`WHO 1001` per luci e `WHO 1004` per termoregolazione) facevano accodare decine di query di stato anche per dispositivi e canali inesistenti. I comandi manuali dell'utente finivano in coda provocando ritardi fino a 2 minuti o mancata accensione al primo click.
* **Soluzione**:
  1. `_is_configured_device`: Le richieste di stato vengono inoltrate solo se il dispositivo è realmente configurato in Home Assistant.
  2. Throttling temporale di **60 secondi** massimo per ciascun indirizzo.
  3. **Zero retries** per le query di stato: se una richiesta di lettura fallisce, viene eliminata all'istante senza bloccare i comandi utente.

### B. Risoluzione Crash Loop su `OWNSignaling` (da v1.3.2)
* I frame tecnici di servizio (ACK `*#*1##`, NACK `*#*0##`, token SHA) sul canale di ascolto vengono filtrati all'ingresso, evitando l'errore fatale `AttributeError: 'OWNSignaling' object has no attribute '_who'` che faceva disconnettere e riconnettere il socket ogni secondo.

### C. Timing Bus SCS Ottimizzati per MyHomeServer1 (da v1.3.2)
* Timeout di scansione portato a 200ms con pausa inter-comando di 30ms, garantendo stabilità sul bus a 9600 baud.

### D. Controllo Percentuale Nativo Tapparelle (da v1.4.0 e v1.4.1)
* Gestione virtuale precisa del tempo di corsa per serrande/tapparelle (0-100%) con filtro antirimbalzo sui frame transitori degli attuatori.

### E. Nostro Workaround Mirato sul Socket di Comando (`ownd/connection.py`)
* **Problema**: I gateway BTicino chiudono silenziosamente la sessione comandi dopo 2 minuti di inattività. In assenza di timeout, la funzione `readuntil()` di Python rimaneva congelata per sempre in attesa dell'ACK, bloccando tutti i comandi successivi.
* **Soluzione**: Applicato un `asyncio.wait_for(..., timeout=3.0)` su `readuntil()` nel metodo `send()`. Se il gateway non risponde entro 3 secondi, il socket stale viene abbattuto e riaperto in 50ms, ritrasmettendo immediatamente il comando.
* **Eliminazione query non supportata `*#22*0##`**: Rimosso il tentativo di discovery globale della filodiffusione al riavvio, che generava l'errore `Could not send message *#22*0##` sui gateway MHS1.

---

## 8. Scansione Attiva del Bus per la Diffusione Sonora (WHO 22)

In precedenza, i dispositivi di diffusione sonora BTicino richiedevano lo sniffing passivo del bus per essere scoperti e aggiunti. Abbiamo implementato la scansione attiva diretta per WHO 22 (`discovery.py`):

1. **Interrogazione Dimensionale Mirata**:
   * Nel protocollo OpenWebNet, le interrogazioni generiche `*#22*WHERE##` ricevono sempre NACK (`*#*0##`).
   * La funzione `async_scan_sound_who22` esegue query dimensionali specifiche:
     - **Sorgenti Tuner FM (`2#S`)**: Interrogazione con Dimensione 6 (`*#22*2#S*6##`) per $S \in [1..4]$.
     - **Punti Sonori / Amplificatori (`3#A#P`)**: Interrogazione con Dimensione 12 (Volume `*#22*3#A#P*12##`) per Ambienti $A \in [1..9]$ e Punti sonori $P \in [1..9]$.
2. **Scansione Silenziosa e Non Invasiva**:
   * Poiché si utilizzano richieste di stato dimensionali (`*#`), i dispositivi rispondono via bus comunicando i parametri memorizzati **senza attivare gli amplificatori, senza commutare relè e senza emettere alcun suono**.
   * Non è richiesto che i dispositivi siano accesi: il microprocessore di bordo a 27V risponde anche in standby.
3. **Integrazione Sequenziale Sicura**:
   * La scansione audio viene eseguita in coda alle luci, tapparelle e termoregolazione, senza impatto sulle scansioni esistenti.
   * I dispositivi rilevati confluiscono automaticamente nella piattaforma `media_player`.
4. **Interfaccia Utente e Tempi Realistici**:
   * Aggiornato il testo esplicativo nel Config Flow (in italiano, inglese, francese e olandese) con stima realistica di **25–35 secondi**.

---

## 9. Zero-Traffic Smart Reconnect e Risveglio Sincrono Radio

Per eliminare ogni ritardo o percezione di mancata risposta al primo comando inviato dopo ore di inattività o dopo un riavvio di Home Assistant:

### A. Zero-Traffic Smart Reconnect (`gateway.py`)
* **Problema**: Il gateway MyHome Server 1 chiude silenziosamente la sessione comandi TCP dopo circa 120 secondi di inattività. Al primo comando dopo lungo silenzio, il socket vecchio attendeva i 3 secondi di timeout prima di accorgersi della chiusura, aggiungendo poi 1 secondo di backoff prima di riconnettersi.
* **Soluzione Zero-Traffic**:
  - Il worker traccia il tempo dell'ultimo comando con soglia di sicurezza a **90 secondi** (`IDLE_TIMEOUT_THRESHOLD = 90.0`).
  - Se il socket è rimasto inattivo per più di 90 secondi (o al primo comando dopo il boot o manutenzioni), il worker **rinnova preventivamente la sessione in circa 100 millisecondi** prima di spedire il pacchetto.
  - **Nessun ping periodico**: quando la casa dorme, non viaggia alcun byte sulla rete LAN né sul bus SCS, azzerando il rischio di saturazione buffer o collisioni.
  - **Zero Backoff su Primo Errore**: se si verifica un errore imprevisto, il backoff al primo tentativo utente è impostato a 0 per ritrasmettere subito.

### B. Risveglio Sincrono della Radio in Accensione (`media_player.py`)
* Nel metodo `async_turn_on()`:
  - Quando si accende una stanza impostata su sorgente Radio (`"Radio FM (Tuner)"`) e nessun'altra stanza sta già diffondendo la radio, l'integrazione esegue la sequenza completa:
    1. Accensione dell'amplificatore zonale di stanza (`*22*1#4#7*WHERE##`).
    2. Instradamento della matrice hardware su Sorgente 1 (`*22*2#4#7*5#2#1##`).
    3. Richiamo del preset/frequenza sul modulo FM F500 (`*#22*2#1*#6*<preset>##`).
  - L'audio parte **immediatamente fin dal primo tocco**, senza necessitare di cambi stazione o comandi secondari.

---

## 10. Allineamento Formale al Protocollo OpenWebNet (Verificato con `openwebnet-mcp`)

Grazie all'integrazione del server formale **`openwebnet-mcp`** (basato sulle specifiche PDF e tabelle dimensionali ufficiali BTicino/Legrand), sono state corrette diverse discrepanze e disallineamenti storici nel gestore messaggi di basso livello (`ownd/message.py`):

### A. Tapparelle e Automazione (WHO 2)
* **Correzione Dimensione 10 vs 11**: Il metodo `set_shutter_level` utilizzava erroneamente `*#2*WHERE*#11#001*LEVEL##` (che nel protocollo Legrand è la dimensione 11 per l'inclinazione lamelle frangisole). È stato allineato alla **Dimensione 10** (`*#2*WHERE*#10*LEVEL##`), che governa l'apertura percentuale assoluta (0–100%).
* **Introduzione Metodo `set_slat_angle`**: La gestione dell'inclinazione veneziane/frangisole è stata separata in un metodo dedicato conforme alla Dimensione 11.
* **Supporto Ibrido in `cover.py`**: Per le tapparelle con attuatori avanzati (`advanced: true`), Home Assistant invia ora la Dimensione 10 hardware istantanea, mantenendo il fallback a calcolo virtuale su tempo di corsa per i relè standard.

### B. Diffusione Sonora Tradizionale (WHO 16)
* **Correzione Telegrammi ON / OFF**: Il metodo `OWNSoundCommand.turn_on` inviava `*16*3*WHERE##` e `turn_off` inviava `*16*13*WHERE##`. Sono stati allineati ai codici standard **`WHAT = 1`** (`*16*1*WHERE##`) e **`WHAT = 0`** (`*16*0*WHERE##`), garantendo coerenza sia in invio che in ricezione con `media_player.py`.

### C. Pulsanti Scenari CEN (WHO 15)
* **Risoluzione Bug Inversione Tasto / Azione**: Nei frame standard CEN (`*15*WHAT*WHERE#BUTTON##`), il pulsante fisico è contenuto in `where_param` e il tipo di pressione in `what` (1=breve, 0=inizio lunga, 2=fine lunga, 3=heartbeat). Il parser `OWNCENEvent` estrae ora correttamente il numero reale del pulsante e la modalità di pressione, garantendo la compatibilità con i Blueprint di automazione.

### D. Chiusura Sessione Video (WHO 7)
* **Standardizzazione Terminazione Video**: Il comando `close_video` in `OWNAVCommand` è stato corretto dalla sintassi irregolare `*7*9**##` alla sintassi standard **`*7*0*WHERE##`** (`WHAT = 0`).

### E. Standardizzazione Fast Turn-Off in `OWNFilodiffusioneCommand` (WHO 22)
* Allineata la classe comandi `OWNFilodiffusioneCommand` per riflettere i telegrammi verificati `1#4#0` (disconnessione sorgente) e `0#4#0` (standby amplificatore).

---

## 11. Riconoscimento Pressione Fisica Frutti a Muro H4562 & Servizi Globali

### A. Riconoscimento Pressione Tasti Locali sui Moduli Audio H4562 (`media_player.py`)
* **Problema riscontrato**: Accendendo fisicamente un punto sonoro dal tasto basculante del frutto da incasso H4562, l'entità in Home Assistant rimaneva nello stato `OFF` o `IDLE` finché l'utente non interagiva da plancia Lovelace.
* **Analisi dei frame OpenWebNet**: L'azionamento manuale da frutto non genera il canonico telegramma di comando `*22*1*WHERE##`, bensì invia una sequenza di eventi fisici:
  - Notifica pressione tasto locale: `*22*9*5#WHERE##`
  - Notifica aggancio sorgente di default: `*22*22#4#A*5#WHERE##`
* **Soluzione**: Inserito nel gestore eventi di `media_player.py` il pattern matching su tali frame fisici. Al tocco del pulsante a parete, l'entità Home Assistant si commuta immediatamente su **`Riproduzione`** (`PLAYING`) e interroga asincronamente la matrice audio per mostrare il volume e la sorgente corretti.

### B. Fix Robustezza Gateway Lookup nei Servizi Globali (`__init__.py`)
* Nei servizi di diagnostica e configurazione (`myhome.send_message`, `myhome.scan_bus`, `myhome.export_to_yaml`), il recupero del gateway predefinito quando non specificato nel payload è stato reso resiliente: ora filtra rigorosamente le sole istanze `CONF_ENTITY` attive, evitando conflitti con flag interni del dizionario `hass.data[DOMAIN]`.

---

## 12. Priorità Frequenza Bus e Risoluzione Dinamica Preset (WHO 22 / F500)

### A. Priorità Assoluta alla Frequenza del Bus ed Eliminazione Disallineamenti (`media_player.py`)
* **Priorità Real-time al Bus**: L'indicazione della frequenza ora riflette fedelmente e prioritariamente quella effettiva letta dal bus OpenWebNet (`TUNER_STATE["freq"]` / `self._tuner_freq`), evitando assunzioni errate sui preset.
* **Risoluzione del Bug Fallback Preset Non Sintonizzati**: Risolta l'anomalia per cui la selezione di un preset non memorizzato (es. P2) ereditava la frequenza e il nome dell'emittente precedente (es. Radio 105 su P3).
* **Corrispondenza Dinamica dei Preset**: L'indice del preset attivo viene verificato e validato confrontando la frequenza reale del bus con il banco memorie effettivo (`TUNER_PRESETS`).

### B. Risoluzione Loghi ed Emittenti per Frequenze Libere e Non Salvate (`radio_catalog.py`)
* **Lookup Universale da Catalogo**: Il nome dell'emittente e il logo ufficiale da archivio locale (`/local/loghi_radio/`) vengono recuperati per qualsiasi frequenza rilevata dal sintonizzatore, inclusa la sintonia manuale e la ricerca automatica (Seek Up/Down), anche per stazioni non registrate nelle memorie P1–P5.
* **Campo `preset_num` in `StationInfo`**: Distingue automaticamente la visualizzazione tra emittenti su preset (`P<N>: <Frequenza> - <Emittente>`) e sintonia libera (`<Frequenza> - <Emittente>`).

### C. Supporto Nativo Dimensione 11 per Memorie Preset
* Introdotto il gestore della Dimensione 11 (`*#22*5#2#1*11*1*<FREQ>*<PRESET>##`) inviata dal modulo F500, garantendo la fedele sincronizzazione dei preset memorizzati nell'hardware ed evitando sovrascritture accidentali durante cambi frequenza o riavvii.

---

## 13. Risoluzione Follow-Me Multi-Room e Routing Sorgenti AUX / Spotify (`media_player.py`)

### A. Eliminazione Forzatura Tuner all'Accensione Zonale
* **Problema**: L'accensione di una stanza inviava come prima istruzione il telegramma hardware `*22*1#4#7*WHERE##` (sorgente 7 = Tuner Radio FM), provocando l'aggancio al sintonizzatore, l'eco del bus `#4#7`, il rilascio del decoder e la messa in pausa immediata di Spotify.
* **Risoluzione**: `async_turn_on()` determina ora **prima** la sorgente attiva desiderata (`_get_active_house_source()` o la sorgente memorizzata). Se la sorgente è un ingresso AUX (es. AUX 2 per Spotify/WiiM), invia direttamente `*22*1#4#{src_num}*WHERE##`, accendendo l'amplificatore sul canale desiderato senza toccare la Radio né interrompere la riproduzione in corso.
* Applicato lo stesso fix per le sorgenti AUX in `async_select_source()` e `async_play_media()`.

### B. Eredità Intelligente della Sorgente Attiva (Follow-Me & Multi-Room)
* `_get_active_house_source()` rileva ora in tempo reale sia se un'altra stanza BTicino sta suonando, sia se uno degli streamer configurati nel pool (es. WiiM, Spotify Connect, Echo Dot) è in stato `PLAYING` o `BUFFERING`, permettendo a qualsiasi nuova stanza di unirsi istantaneamente all'ascolto della casa.
* In caso di riassegnazione del decoder in modalità esclusiva (Follow-Me 1-to-1), il callback `_decoder_lost` provvede ora allo spegnimento fisico immediato dell'amplificatore della stanza precedente (`*22*1#4#0*OLD_WHERE##` e standby `0#4#0`).

### C. Esposizione Permanente dei Preset Radio (`radio_presets`)
* L'attributo `radio_presets` (P1–P5 con frequenze ed emittenti affiancate) viene esposto incondizionatamente in `extra_state_attributes` per qualsiasi sorgente o stato dell'entità, garantendo piena accessibilità da schede Lovelace, automazioni e integrità nel ripristino di stato dopo un riavvio di Home Assistant.
