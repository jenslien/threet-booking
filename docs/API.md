# 3T booking-API – dokumentasjon

Undersøkt 2026-11-16 via innlogget sesjon på www.3t.no (Chrome), ved å lese
web-klientens JS-bundles (`/_next/static/chunks/8996-*.js`, `9478-*.js`) og
GraphQL-introspeksjon. Kun lese-operasjoner er kjørt. `CreateBooking` og
`CancelBooking` er **ikke** kjørt under undersøkelsen.

> Ingen tokens, cookies eller passord er lagret i dette dokumentet.

## 1. Endepunkt og autentisering

| | |
|---|---|
| Endepunkt | `POST https://europe-west3-tttno-396707.cloudfunctions.net/api/graphql` |
| Format | Standard GraphQL over JSON: `{"query": ..., "variables": {...}, "operationName": ...}` |
| Auth-header | `authorization: Bearer <access_token>` |
| Token-type | JWT (claims: `sub`, `exp`, `iat`, `roles`), levetid **7 dager** (`exp - iat = 604800`) |
| Introspeksjon | Aktivert |

### Hvor tokenet kommer fra i web-klienten

- www.3t.no er en Next.js-app med **NextAuth**. `GET https://www.3t.no/api/auth/session`
  (med NextAuth-sesjonscookie) returnerer
  `{ expires, user: { id, roles[], access_token, refresh_token, token, expires } }`.
- Apollo-klienten legger `user.access_token` i `authorization`-headeren.
- Refresh skjer server-side i NextAuth. Ved `RefreshTokenError` logges brukeren
  ut og sendes til `/logg-inn`. Det finnes ingen refresh-mutation i GraphQL-skjemaet.
- GraphQL-skjemaet har en innloggingsspørring:

  ```graphql
  query getAccessToken($password: String!, $username: String!) {
    AccessToken(password: $password, username: $username)   # returnerer String
  }
  ```

  **Verifisert 2026-11-16:** returnerer et access token som fungerer som Bearer
  mot API-et (testet med `python -m threet bookings`). For Home Assistant brukes
  brukernavn/passord fra config entry, med ny innlogging ved 401 eller utløpt `exp`.

### Uautentisert tilgang

Timeplanen (`BookableGroupActivities`) kan hentes **uten** token. Med token fylles
de personlige feltene (`slot.inWaitingList`, `cancel`) ut riktig.

## 2. Sentre

```graphql
query Centers {
  Centers(request: { bookingFilterType: GROUPACTIVITY }) { id slug page { title } }
}
```

| id | slug | navn |
|---|---|---|
| 1 | 3t-rosten | 3T-Rosten |
| 2 | 3t-leangen | 3T-Leangen |
| 3 | 3t-midtbyen | 3T-Midtbyen |
| 5 | 3t-steinkjer | 3T-Steinkjer |
| 6 | 3t-sluppen | 3T-Sluppen |
| 7 | 3t-levanger | 3T-Levanger |
| 8 | 3t-orkanger | 3T-Orkanger |
| 10 | 3t-byasen | 3T-Byåsen |
| 4542 | 3t-solsiden | 3T-Solsiden |
| 5587 | 3t-ilsvika | 3T-Ilsvika |
| 5588 | 3t-moholt | 3T-Moholt |
| 5819 | 3t-ranheim | 3T-Ranheim |
| 5820 | 3t-melhus | 3T-Melhus |
| 5860 | 3t-fossegrenda | 3T-Fossegrenda |
| 6247 | 3t-stjordal | 3T-Stjørdal |
| 7195 | 3t-lade | 3T-Lade |
| 7509 | 3t-teknostallen | 3T-Teknostallen |

Brukerens lagrede filter: `GetBookingFilters(bookingType: GROUPACTIVITY) { type value }`
→ f.eks. `[{type: CENTER, value: "1"}]`.

## 3. Hente gruppetimer

```graphql
query bookableActivities($filter: [FilterInput!]!, $take: Int, $skip: Int, $includeHistory: Boolean) {
  BookableGroupActivities(
    request: { filter: $filter, take: $take, skip: $skip, includeHistory: $includeHistory }
  ) {
    countBeforeFilters
    countAfterFilters
    all {
      id                 # <- dette er ID-en CreateBooking tar
      name
      start              # ISO 8601 UTC
      end
      bookableEarliest   # når booking åpner (UTC)
      bookableLatest     # siste bookingtidspunkt (start - 15 min)
      center { id slug page { title } }
      instructors { id slug page { title } }
      slot { total available hasWaitingList inWaitingList }
      cancel { isEnabled code }      # code f.eks. "TOO_LATE_TO_CANCEL"
      categories { id name }
      groupActivity { id page { title } }   # aktivitetstypen (f.eks. "Spinn 60"), IKKE bookbar ID
      externalMessage
      locations
    }
  }
}
```

`FilterInput = { type: FilterTypes!, value: String! }`. Flere filtre med samme
`type` kan kombineres. Relevante `FilterTypes`:

| type | value-eksempel | merknad |
|---|---|---|
| `CENTER` | `"1"` | senter-id |
| `FROM_DATE` | `"2026-11-22"` | verifisert |
| `TO_DATE` | `"2026-11-22"` | verifisert, inklusiv (FROM=TO gir én dag) |
| `GROUP_ACTIVITY` | `"5574"` | `groupActivity.id` (antatt) |
| `INSTRUCTOR`, `CATEGORY`, `TIME_OF_DAY`, `IDS`, ... | | ikke testet |

Enkeltime: `BookableGroupActivity(request: { centerId: "1", id: "36400000" }) { ... }`.

### `bookableEarliest`

Observert: `bookableEarliest` = kl. 08:00 norsk tid, 7 kalenderdager før timen
(f.eks. time 2026-11-17 19:55 → `2026-11-10T07:00:00Z`). Dette stemmer med
bookingreglene. Bruk feltet direkte i stedet for å regne det ut selv.
`bookableLatest` = `start - 15 min`.

## 4. Mine bookinger

```graphql
query MyBookings($fromDate: Dayjs) {
  CustomerGroupActivityBookings(fromDate: $fromDate) {
    __typename
    ... on GroupActivityBooking {
      id activityId bookingType
      cancel { isEnabled code }
      bookableGroupActivity { id name start end center { id page { title } } }
    }
    ... on WaitingListBooking {
      id activityId bookingType waitingListPosition
      cancel { isEnabled code }
      bookableGroupActivity { id name start end center { id page { title } } }
    }
    ... on NoShowBooking {
      id activityId bookingType name status noShowType
      bookableGroupActivity { id name start }
    }
  }
}
```

- `fromDate` (`Dayjs`-skalar) tar en ISO 8601-streng.
- `id` = **booking-ID** (brukes ved avbestilling).
- `activityId` = `bookableGroupActivity.id` = ID-en som ble brukt i `CreateBooking`.
- `bookingType` observert: `"groupActivityBooking"`.
- Web-klienten henter også `CustomerServiceBookings` og `CustomerEventBookings`
  i samme spørring (PT/squash/arrangementer). Det er ikke relevant her.

## 5. Booking

```graphql
mutation CreateBooking($id: ID!) {
  CreateBooking(request: { groupActivityId: $id, allowWaitingList: true }) {
    __typename
    ... on GroupActivityBooking { id activityId }
    ... on WaitingListBooking  { id activityId waitingListPosition }
  }
}
```

`$id` = `BookableGroupActivities.all[].id`. Med `allowWaitingList: true` havner
man på venteliste hvis timen er full (`__typename == "WaitingListBooking"`).
Feilresponsene (for tidlig, maks antall bookinger osv.) er ikke kartlagt, fordi
det ville krevd et faktisk bookingforsøk.

## 6. Avbestilling

```graphql
mutation CancelBooking($bookingId: ID!, $activityStart: Dayjs!) {
  CancelBooking(request: { bookingId: $bookingId, activityStart: $activityStart }) {
    id name start slot { total available hasWaitingList inWaitingList } cancel { isEnabled code }
  }
}
```

Verifisert fra web-klientens kode (`9478-*.js`):
`variables: { bookingId: booking.id, activityStart: booking.bookableGroupActivity.start }`.
Returnerer `BookableGroupActivity` (fragmentet `BookingButton`). Sjekk
`cancel.isEnabled` før du avbestiller. Etter fristen er `code = "TOO_LATE_TO_CANCEL"`.

## 7. 3Ts bookingregler (fra /kundeservice/vilkar-og-regler/bookingregler)

- Gruppetimer kan bookes **opptil 7 dager i forveien fra kl. 08:00**.
- Maks **5 forhåndsbookede timer** samtidig.
- Avbestilling senest **2 timer** før start. Ellers no-show-gebyr på **60 kr**.
- Ventelisten er aktiv til 2 timer før start. Man rykker opp automatisk, og
  no-show-gebyret gjelder også da.
- Ved overlappende timer får man bare plass på den første man kommer inn på.

### Automatisert tilgang

Verken bookingreglene eller medlemsvilkårene sier noe om roboter, scripts eller
automatisert booking. `robots.txt` stenger ikke API-et (det ligger på et annet
domene). Det finnes ingen offentlig API-avtale. API-et er internt for web-appen
og kan endres uten varsel.
Anbefalinger:
- Book kun for egen konto, og bare timer du faktisk skal på (no-show koster 60 kr).
- Lav frekvens: ett kall rett etter `bookableEarliest`, med få og spredte retries.
  Integrasjonen henter bookinger hver time og timeplanen hver 6. time
  (ca. 28 kall i døgnet med ett senter).
- Vurder å spørre 3T kundeservice om de har innvendinger.
