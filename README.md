# Stack Miners

> **DE** | [EN](#english)

---

## Deutsch

Eine Home Assistant Custom Integration zur automatischen Steuerung mehrerer Bitcoin-Miner auf Basis von PV-Überschussstrom.

Die Integration überwacht einen Netz-Leistungssensor (z.B. Shelly 3EM) und schaltet Miner, die über die [hass-miner](https://github.com/tntvlad/hass-miner) Integration eingebunden sind, in einer konfigurierbaren Prioritätsreihenfolge ein und aus — je nachdem wie viel Überschussstrom verfügbar ist.

### Funktionsweise

- **Ereignisgesteuert**: Die Integration reagiert direkt auf jeden neuen Messwert des Netz-Sensors (kein Polling).
- **Gleitender Durchschnitt**: Kurzfristige Leistungsspitzen (z.B. Wolken) werden durch einen konfigurierbaren Messwert-Puffer geglättet.
- **Hysterese**: Ein einstellbarer Watt-Puffer verhindert schnelles Hin- und Herschalten an der Einschaltschwelle.
- **Mindestlaufzeiten**: Konfigurierbare Mindest-Ein- und Ausschaltdauer schützt die Miner-Hardware.
- **Prioritätsreihenfolge**: Miner werden in der festgelegten Reihenfolge eingeschaltet (Priorität 1 zuerst) und in umgekehrter Reihenfolge ausgeschaltet.
- **Master-Schalter**: Der automatische Betrieb kann jederzeit über einen HA-Schalter deaktiviert werden, ohne die Miner zu stoppen.

### Voraussetzungen

- Home Assistant 2026.4 oder neuer
- [hass-miner](https://github.com/tntvlad/hass-miner) Integration mit mindestens einem konfigurierten Miner
- Netz-Leistungssensor mit Vorzeichen (negativ = Einspeisung, positiv = Bezug) — z.B. Shelly Pro 3EM

### Installation

#### Über HACS (empfohlen)

1. [HACS](https://hacs.xyz) muss installiert sein.
2. In HA: **HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories**
3. URL eingeben: `https://github.com/chholzin/ha-stack-miners`  
   Kategorie: **Integration**
4. **Hinzufügen** klicken, dann die Integration „Stack Miners" in HACS suchen und installieren.
5. Home Assistant neu starten.

#### Manuell

1. Den Ordner `custom_components/stack_miners` in das Verzeichnis `config/custom_components/` der Home Assistant Instanz kopieren.
2. Home Assistant neu starten.

#### Integration einrichten

Unter **Einstellungen → Integrationen → Integration hinzufügen** nach „Stack Miners" suchen.

### Konfiguration

Die Integration wird vollständig über die HA-Benutzeroberfläche konfiguriert:

**Schritt 1 — Netz-Sensor & Einstellungen**

| Parameter | Beschreibung | Standard |
|---|---|---|
| Netz-Leistungssensor | Sensor-Entität (negativ = Einspeisung) | — |
| Hysterese (W) | Puffer um Schalten an der Schwelle zu vermeiden | 100 W |
| Gleitender Durchschnitt | Anzahl Messwerte für die Mittelwertbildung | 5 |
| Mindest-Einschaltdauer (s) | Wie lange ein Miner EIN bleiben muss | 60 s |
| Mindest-Ausschaltdauer (s) | Wie lange ein Miner AUS bleiben muss | 60 s |
| Simulationsmodus aktivieren | Erstellt Schalter und Schieberegler zur Simulation (optional) | Nein |

**Schritt 2 — Miner auswählen**

Die Integration erkennt automatisch alle über hass-miner eingebundenen Miner und zeigt sie zur Auswahl an.

**Schritt 3 — Miner konfigurieren**

Für jeden ausgewählten Miner werden Name (vorausgefüllt), Leistungsaufnahme in Watt und Priorität (1 = höchste) festgelegt.

### Exponierte Entitäten

| Entität | Beschreibung |
|---|---|
| `sensor.stack_miners_grid_power` | Aktueller Netz-Messwert (W) |
| `sensor.stack_miners_surplus_power` | Gleitender Überschuss-Durchschnitt (W) |
| `sensor.stack_miners_active_miners` | Anzahl aktiver Miner |
| `sensor.stack_miners_active_power` | Summe aktiver Miner-Leistung (W) |
| `sensor.stack_miners_total_hashrate` | Summierte Hashrate aller aktiven Miner (TH/s) |
| `sensor.stack_miners_mode` | Regler-Modus: `idle` / `running` |
| `switch.stack_miners_auto_control` | Automatische Steuerung ein/aus |
| `switch.stack_miners_simulation` | Simulationsmodus ein/aus *(optional)* |
| `number.stack_miners_simulation_surplus` | Simulierter Überschuss 0–10.000 W *(optional)* |

### Simulationsmodus

Wenn bei der Konfiguration die Option **Simulationsmodus aktivieren** ausgewählt wurde, stehen zwei zusätzliche Entitäten zur Verfügung:

- **Schalter** (`switch.stack_miners_simulation`): Aktiviert die Simulation. Solange dieser Schalter eingeschaltet ist, ignoriert die Integration den echten Netz-Sensor vollständig.
- **Schieberegler** (`number.stack_miners_simulation_surplus`): Stellt den simulierten PV-Überschuss in Watt ein (0–10.000 W). Jede Änderung löst sofort eine neue Schalterentscheidung aus.

Dies ermöglicht es, das Schaltverhalten der Integration zu testen, ohne auf echten PV-Überschuss angewiesen zu sein.

### Schaltlogik

```
Überschuss = -(Gleitender Durchschnitt Netzleistung)

Einschalten (Priorität nach Reihenfolge):
  Überschuss ≥ Miner-Leistung + Hysterese
  AND Miner war mindestens min_off_time Sekunden aus

Ausschalten (in umgekehrter Priorität):
  Überschuss < Verbleibende-Last - Hysterese
  AND Miner war mindestens min_on_time Sekunden an
```

Pro Evaluierungszyklus wird maximal eine Schaltaktion ausgeführt. Danach wartet die Integration auf den nächsten Sensor-Messwert.

### Tests ausführen

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

---

## English

<a name="english"></a>

A Home Assistant custom integration for automatic Bitcoin miner control based on PV surplus power.

The integration monitors a grid power sensor (e.g. Shelly 3EM) and switches miners — integrated via [hass-miner](https://github.com/tntvlad/hass-miner) — on and off in a configurable priority order depending on how much surplus power is available.

### How it works

- **Event-driven**: Reacts directly to every new reading from the grid sensor (no polling).
- **Rolling average**: Short power spikes (e.g. passing clouds) are smoothed by a configurable sample buffer.
- **Hysteresis**: A configurable watt buffer prevents rapid switching around the turn-on threshold.
- **Minimum run times**: Configurable minimum on/off durations protect miner hardware.
- **Priority order**: Miners are switched on in priority order (priority 1 first) and off in reverse order.
- **Master switch**: Automatic control can be disabled at any time via an HA switch without stopping the miners.

### Requirements

- Home Assistant 2026.4 or newer
- [hass-miner](https://github.com/tntvlad/hass-miner) integration with at least one configured miner
- Signed grid power sensor (negative = export, positive = import) — e.g. Shelly Pro 3EM

### Installation

#### Via HACS (recommended)

1. [HACS](https://hacs.xyz) must be installed.
2. In HA: **HACS → Integrations → ⋮ → Custom repositories**
3. Enter the URL: `https://github.com/chholzin/ha-stack-miners`  
   Category: **Integration**
4. Click **Add**, then search for "Stack Miners" in HACS and install it.
5. Restart Home Assistant.

#### Manual

1. Copy the `custom_components/stack_miners` folder into the `config/custom_components/` directory of your Home Assistant instance.
2. Restart Home Assistant.

#### Set up the integration

Go to **Settings → Integrations → Add Integration** and search for "Stack Miners".

### Configuration

The integration is configured entirely through the HA UI:

**Step 1 — Grid sensor & settings**

| Parameter | Description | Default |
|---|---|---|
| Grid power sensor | Sensor entity (negative = export) | — |
| Hysteresis (W) | Buffer to avoid switching at the threshold | 100 W |
| Rolling average samples | Number of readings to average | 5 |
| Minimum ON time (s) | How long a miner must stay on | 60 s |
| Minimum OFF time (s) | How long a miner must stay off | 60 s |
| Enable simulation mode | Creates a switch and slider for simulation (optional) | No |

**Step 2 — Select miners**

The integration automatically discovers all miners registered via hass-miner and presents them for selection.

**Step 3 — Configure miners**

For each selected miner, set the name (pre-filled), power consumption in watts, and priority (1 = highest).

### Exposed entities

| Entity | Description |
|---|---|
| `sensor.stack_miners_grid_power` | Current grid power reading (W) |
| `sensor.stack_miners_surplus_power` | Rolling average surplus power (W) |
| `sensor.stack_miners_active_miners` | Number of active miners |
| `sensor.stack_miners_active_power` | Sum of active miners' power draw (W) |
| `sensor.stack_miners_total_hashrate` | Combined hashrate of all active miners (TH/s) |
| `sensor.stack_miners_mode` | Controller mode: `idle` / `running` |
| `switch.stack_miners_auto_control` | Enable / disable automatic control |
| `switch.stack_miners_simulation` | Enable / disable simulation mode *(optional)* |
| `number.stack_miners_simulation_surplus` | Simulated surplus power 0–10,000 W *(optional)* |

### Simulation mode

If **Enable simulation mode** was selected during configuration, two additional entities are available:

- **Switch** (`switch.stack_miners_simulation`): Activates simulation. While on, the integration completely ignores the real grid sensor.
- **Slider** (`number.stack_miners_simulation_surplus`): Sets the simulated PV surplus in watts (0–10,000 W). Every change immediately triggers a new switching decision.

This allows testing the integration's switching behaviour without relying on real PV surplus.

### Switching logic

```
surplus = -(rolling average of grid power)

Turn ON (in priority order):
  surplus ≥ miner_power + hysteresis
  AND miner has been off for at least min_off_time seconds

Turn OFF (in reverse priority order):
  surplus < remaining_load - hysteresis
  AND miner has been on for at least min_on_time seconds
```

At most one switching action is taken per evaluation cycle. The integration then waits for the next sensor reading.

### Running tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```
