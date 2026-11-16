# threet-booking

Home Assistant-integrasjon for automatisk booking av gruppetimer hos 3T, pluss
en frittstående Python-klient og et kommandolinjeverktøy for testing.

- API-dokumentasjon: [docs/API.md](docs/API.md)
- Integrasjon: `custom_components/threet/`
- API-klient (uten HA-avhengigheter): `custom_components/threet/threet_api/`

## Installere i Home Assistant

1. Kopier `custom_components/threet` til `<config>/custom_components/threet`.
2. Start Home Assistant på nytt.
3. *Innstillinger → Enheter og tjenester → Legg til integrasjon → 3T Treningssenter*,
   logg inn med e-post og passord fra 3t.no.
4. Trykk *Konfigurer* på integrasjonen for å legge til faste timer:
   - **Forslag fra historikken min**: timer du har booket minst 2 ganger siste år.
   - **Velg fra timeplanen**: senter → ukedag → huk av timer.
   - **Fjern faste bookinger**.

## Slik virker det

En fast time er en ukentlig regel (time, senter, ukedag, klokkeslett). Hvert
15. minutt henter integrasjonen dine bookinger og timeplanen for sentrene du har
regler på. For hver regel finnes neste forekomst, og et kall planlegges til
`bookableEarliest` (kl. 08:00 sju dager før), med opptil 3 nye forsøk
(5 s, 20 s og 60 s). Er bookingen allerede åpen når regelen legges til, bookes
timen med en gang.

En time som har vært booket, enten av integrasjonen eller av deg, blir aldri
booket på nytt automatisk etter at den er avbestilt.

### Entiteter

Per fast time (egen enhet «3T fast: …»):

| Entitet | Funksjon |
|---|---|
| `switch …_aktiv` | Av = pause, på = fortsett (gir også feilede forsøk en ny sjanse) |
| `switch …_hopp_over_neste` | Hopper over neste forekomst og nullstilles av seg selv etterpå |
| `sensor …_status` | planlagt / bookes nå / booket / venteliste / pauset / hoppes over / avbestilt / feilet / booking stengt / ingen time funnet |
| `sensor …_neste_time` | Starttidspunkt for neste forekomst |
| `sensor …_booking_apner` | Når bookingen åpner |

Konto (enhet «3T»):

| Entitet | Funksjon |
|---|---|
| `calendar.3t_bookinger` | Bookinger og planlagte automatiske bookinger |
| `sensor.3t_kommende_bookinger` | Antall (maks 5), full liste i attributter |
| `sensor.3t_booking_1..6` | Kommende bookinger i tidsrekkefølge |
| `button.3t_avbestill_booking_1..6` | Avbestiller tilsvarende booking (utilgjengelig etter fristen) |

Tjenester: `threet.book` (`activity_id`) og `threet.cancel` (`booking_id`).
Hendelser: `threet_booked` og `threet_booking_failed`, som kan brukes til varsler.

### Avbestillingsknapp med bekreftelse (dashbord)

```yaml
type: tile
entity: sensor.3t_booking_1
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.3t_avbestill_booking_1
  confirmation:
    text: Avbestille denne timen?
```

### Varsel ved booking

```yaml
automation:
  - alias: 3T booket
    triggers:
      - trigger: event
        event_type: threet_booked
      - trigger: event
        event_type: threet_booking_failed
    actions:
      - action: notify.mobile_app_min_telefon
        data:
          message: >
            {{ trigger.event.data.rule }}:
            {{ 'feilet: ' ~ trigger.event.data.error if trigger.event.event_type.endswith('failed')
               else ('venteliste' if trigger.event.data.waiting_list else 'booket') }}
```

## Kommandolinjeverktøy

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m threet centers
.venv/bin/python -m threet classes --center 1 --from 2026-11-22 --anon
.venv/bin/python -m threet suggest --since 2025-11-16      # krever innlogging
```

Innlogging skjer med `THREET_USERNAME=...` (passordet spørres om i et vanlig
terminalvindu) eller `THREET_ACCESS_TOKEN=...`. `book` og `cancel` krever `--confirm`.

## Tester

```sh
uv venv .venv-ha --python 3.13 && uv pip install --python .venv-ha/bin/python -r requirements-test.txt
.venv-ha/bin/python -m pytest
```

Testene bruker en simulert klient og gjør ingen kall mot 3T.
