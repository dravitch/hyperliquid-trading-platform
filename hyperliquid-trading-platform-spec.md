# Plateforme de trading propriétaire — Hyperliquid / NautilusTrader (Python + Rust)

Statut : projet initial — MVP (**REV3.1 — baseline d'implémentation figée**)
Juridiction : Québec — **présumée admissible** à la date de vérification (seule l'Ontario est nommée dans les Restricted Persons, ToU Hyperliquid §1.6). Absence d'interdiction explicite ≠ validation juridique positive : à revalider contre les ToU Hyperliquid en vigueur avant toute activation mainnet.
Venue d'exécution : Hyperliquid (perpetuals)
Socle d'infrastructure : NautilusTrader (adapter Hyperliquid officiel, statut "active development", Rust + bindings Python)

---

## 1. Objectifs

### 1.1 Objectif stratégique (long terme)
Bâtir une plateforme de trading propriétaire, modulaire, capable d'exécuter plusieurs stratégies sur Hyperliquid (et potentiellement d'autres venues via Nautilus dans le futur), avec :
- un chemin d'exécution robuste (reconciliation d'ordres, gestion de position, funding) délégué à Nautilus plutôt que réécrit ;
- une **logique métier pure, testable sans Nautilus**, découplée de l'orchestration du framework (voir §2 — correction architecturale) ;
- aucun lock-in fort : Nautilus est consommé comme une dépendance derrière une couche d'orchestration fine, pas comme une architecture applicative complète.

### 1.2 Objectif MVP (immédiat)
Une seule stratégie, un seul actif, un seul mode de sortie double :

> **Ouvrir un short x3 sur BTC/USD (perpetual, Hyperliquid). Fermer la position dès que :**
> - **le RSI(14) en daily touche ≤ 20**, OU
> - **le prix atteint 60 000 USD** (stop-loss ou take-profit selon la position actuelle du prix — voir §5.1, direction à confirmer)

Cette stratégie est volontairement simple : elle sert à valider la chaîne complète (data → signal → ordre → fill → position → sortie) avant d'ajouter de la complexité.

---

## 2. Architecture

### 2.1 Correction architecturale : séparer logique métier et orchestration Nautilus

Version précédente : une couche "interfaces génériques" (`ExecutionVenue`/`MarketFeed`/`OrderGateway`) au-dessus de Nautilus. En pratique, une stratégie Nautilus **hérite** de `Strategy` et utilise directement `on_bar`, `submit_order`, `cache.position()` — le couplage au framework existe donc réellement à ce niveau, et prétendre l'éviter dès le MVP via des abstractions génériques est irréaliste.

La séparation qui compte réellement se situe une couche plus bas :

```text
Pure domain logic (Python pur, testable sans Nautilus)
─────────────────────────────────────────────────────
RsiExitRule
PriceExitRule
PositionSizing
RiskPolicy
StrategyState (machine à états)
        │
        ▼
NautilusStrategyAdapter (couplage assumé au framework)
─────────────────────────────────────────────────────
on_bar() / on_quote()
submit_order() / cache.position()
        │
        ▼
NAUTILUS CORE (Rust)
─────────────────────────────────────────────────────
Order Management System
Position / Account model
Reconciliation (REST ≠ WS ≠ local state)
Market data engine (bars, quotes, funding)
Backtest engine (mêmes sémantiques que le live)
        │
        ▼
HyperliquidDataClient / HyperliquidExecutionClient
        │
        ▼
Hyperliquid (REST + WebSocket, on-chain CLOB)
```

**Principe directeur révisé** : la fonction `should_exit(position, rsi, price, config) -> ExitDecision` doit pouvoir s'exécuter et se tester en dehors de tout contexte Nautilus. Seule la classe `ShortBtcRsiStrategy(Strategy)` — la couche d'orchestration — connaît l'API Nautilus.

### 2.2 Deux horloges distinctes, pas une seule

Erreur à éviter : surveiller le seuil de prix dans le même callback `on_bar` que le RSI daily. Si `bar` désigne la barre quotidienne, un franchissement intra-journalier de 60 000 $ (ex. 62 000 → 59 800 → clôture daily à 61 500) ne serait jamais détecté par une barre daily.

Deux flux séparés sont nécessaires :

```text
Daily bar  →  RSI(14)  →  RSI <= 20 ?          (décision logicielle)
Quote/mark/trade stream  →  price threshold ?   (protection temps réel)
```

**Pour la protection de prix, préférer un ordre trigger natif reduce-only posé directement sur Hyperliquid** plutôt qu'une simple condition Python évaluée sur le flux de quotes. Nautilus supporte les ordres trigger et `reduce_only`. Avantage décisif : si le processus Python ou le VPS meurt, la protection reste active côté exchange.

**Correction (REV3) — invariant vérifiable plutôt que promesse temporelle** : "poser le trigger au moment du fill" n'est pas une garantie atomique dans le chemin Nautilus standard (fill reçu → callback → nouvelle requête de soumission → confirmation). Une fenêtre d'exposition sans protection existe entre le fill et la confirmation du trigger. Hyperliquid expose un mécanisme `normalTpsl` permettant en principe de grouper entrée + protection en une seule action, mais ce comportement dépend de l'adapter Nautilus dans la version épinglée — **à vérifier contre le changelog de la version pinée en Phase 0, pas à supposer acquis.**

La formulation correcte est donc un **invariant système falsifiable**, pas une intention temporelle :

> Une position ne doit jamais être considérée `OPEN` tant que la **quantité totale confirmée protégée côté venue** n'est pas égale à la **quantité nette confirmée de la position** (`protected_qty == actual_net_position_qty`) — pas seulement le dernier événement de fill reçu. Un ordre IOC peut accumuler plusieurs fills avant annulation du reliquat ; chaque nouveau fill doit donc déclencher soit un amend/replace du trigger existant, soit toute autre mécanique validée par l'adapter, pour que la protection reste égale à l'exposition réelle à tout instant. Toute divergence maintient l'état `PROTECTING` ; un échec de convergence déclenche `EMERGENCY_EXIT`. Une soumission atomique entrée+protection via `normalTpsl` est préférée lorsqu'elle est effectivement supportée et validée dans la version Nautilus épinglée.

---

## 3. Composants

| Composant | Rôle | Langage | Statut requis |
|---|---|---|---|
| `HyperliquidDataClient` | Flux marché (bars daily BTC, mark price, quotes) | Rust (adapter Nautilus) | Fourni par Nautilus |
| `HyperliquidExecutionClient` | Soumission/annulation d'ordres, fills, position, triggers reduce-only | Rust (adapter Nautilus) | Fourni par Nautilus |
| `RsiExitRule`, `PriceExitRule` | Règles de sortie pures, testables sans Nautilus | Python pur | À écrire (MVP) |
| `PositionSizing` | Calcul notional à partir de la config (indépendant du levier) | Python pur | À écrire (MVP) |
| `StrategyState` | Machine à états (voir §5.5) | Python pur | À écrire (MVP) |
| `ShortBtcRsiStrategy` | Orchestration Nautilus (couche couplée au framework) | Python (hérite `Strategy`) | À écrire (MVP) |
| `RiskGuard` | Garde-fous : notional max, levier max, emergency close | Python | À écrire |
| `ConfigLoader` | Paramètres externalisés (YAML/env) | Python | À écrire |
| `Recorder` | Journalisation structurée, snapshot d'état, export PnL | Python | À écrire |
| `BacktestRunner` | Exécution de la stratégie sur données historiques | Python (via Nautilus) | À écrire (config) |
| `LiveRunner` | Point d'entrée production (testnet puis mainnet), **réconciliation obligatoire au démarrage** | Python (via Nautilus) | À écrire (config) |
| API/agent wallet Hyperliquid | Signature des ordres avec pouvoirs limités (pas la clé du wallet principal) | — | Fourni par vous (voir §6) |

---

## 4. Structure du dépôt GitHub

```text
hyperliquid-trading-platform/
├── README.md
├── LICENSE                          # licence propriétaire ou privée — voir note LGPL Nautilus
├── pyproject.toml                   # dépendances Python (nautilus_trader, pyyaml, etc.) — version pinée
├── .env.example                     # HYPERLIQUID_TESTNET_PK, HYPERLIQUID_VAULT, etc.
├── .gitignore                       # .env, __pycache__, données de backtest lourdes
│
├── config/
│   ├── venues/
│   │   └── hyperliquid.yaml         # config adapter (testnet/mainnet, rate limits)
│   ├── strategies/
│   │   └── short_btc_rsi.yaml       # symbole, levier, notional, seuils RSI/prix
│   └── risk/
│       └── default_risk.yaml        # notional max, levier max, emergency close
│
├── src/
│   └── hltrader/                    # PAS "platform" — collision avec le module stdlib Python
│       ├── __init__.py
│       ├── domain/                  # logique métier PURE — aucune dépendance Nautilus
│       │   ├── __init__.py
│       │   ├── exit_rules.py        # RsiExitRule, PriceExitRule, FIRST_TRIGGER_WINS
│       │   ├── sizing.py            # PositionSizing (notional explicite, indépendant du levier)
│       │   └── state_machine.py     # StrategyState : NEVER_ENTERED/ENTERING/OPEN/EXITING/CLOSED_FINAL
│       ├── strategies/
│       │   ├── __init__.py
│       │   └── short_btc_rsi.py     # ShortBtcRsiStrategy — orchestration Nautilus uniquement
│       ├── risk/
│       │   ├── __init__.py
│       │   └── guard.py             # RiskGuard
│       ├── indicators/
│       │   ├── __init__.py
│       │   └── rsi.py               # config de l'indicateur RSI daily + politique de warm-up
│       ├── persistence/
│       │   ├── __init__.py
│       │   └── run_journal.py       # journal minimal JSON (état, run_id, ordres) — voir §5.6
│       ├── runners/
│       │   ├── backtest.py          # point d'entrée backtest
│       │   └── live.py              # point d'entrée live — reconcile AVANT d'agir (voir §5.5)
│       └── observability/
│           ├── __init__.py
│           └── recorder.py          # logs structurés, export CSV/JSON du PnL
│
├── tests/
│   ├── domain/
│   │   ├── test_exit_rules.py       # tests déterministes SANS Nautilus
│   │   ├── test_sizing.py
│   │   └── test_state_machine.py    # y compris cas de restart/race condition
│   ├── test_risk_guard.py
│   └── fixtures/
│       └── btc_daily_sample.csv     # données synthétiques pour tests déterministes
│
├── data/
│   └── backtests/                   # données historiques téléchargées (gitignored si lourd)
│
├── scripts/
│   ├── download_historical_data.py  # récupération données Hyperliquid pour backtest
│   ├── check_testnet_faucet_eligibility.py  # voir §6.1 — éligibilité à vérifier, pas supposée
│   ├── run_backtest.sh
│   ├── run_live_testnet.sh
│   └── crash_restart_drill.sh       # simule kill -9 en position ouverte — voir Phase 3.5
│
└── docs/
    ├── architecture.md              # ce document, condensé
    ├── runbook.md                   # procédure démarrage/arrêt/incident
    └── decisions/                   # ADRs (Architecture Decision Records)
        └── 0001-nautilus-adoption-mince.md
```

---

## 5. MVP — Spécification fonctionnelle détaillée

### 5.1 Direction du seuil 60 000 $ — à confirmer avant codage

- **RSI(14) daily ≤ 20** : signal de survente extrême. Sur un short, c'est une sortie de protection (risque de rebond).
- **Seuil 60 000 $** : si BTC est actuellement sous 60 000 $, ce niveau à la hausse est un **stop-loss**. S'il est au-dessus, ce niveau à la baisse est un **take-profit**.

Le seuil et sa direction (`above` / `below`) restent configurables dans `short_btc_rsi.yaml`. **Confirmation requise avant le premier déploiement testnet.**

### 5.2 Les deux sorties ne forment pas un OCO classique

Un OCO (one-cancels-other) suppose deux ordres présents simultanément sur le venue. Ici on a :
```text
RSI exit   → décision logicielle (évaluée sur bar daily)
Price exit → trigger natif Hyperliquid reduce-only (posé sur le venue dès l'entrée)
```
C'est donc une **arbitration à double sortie**, nommée explicitement `exit_logic: FIRST_TRIGGER_WINS` plutôt que "OCO". La race condition (RSI et trigger prix qui se déclenchent quasi simultanément) doit transiter par un état atomique `EXITING` dans la machine à états (§5.5) pour garantir une seule intention de fermeture.

### 5.3 Paramètres MVP (`config/strategies/short_btc_rsi.yaml`)

```yaml
strategy: ShortBtcRsiStrategy
venue: HYPERLIQUID
symbol: BTC-USD-PERP
side: SHORT

desired_margin:                 # précondition VOULUE — pas une preuve que Nautilus l'applique
  mode: isolated
  leverage: 3
  verify_before_entry: true     # query venue → actual leverage/margin mode → mismatch = FAIL CLOSED

position:
  sizing_mode: fixed_notional   # explicite — jamais fixed_notional ET equity_fraction en même temps
  notional_usdc: 300            # exemple — à définir selon le capital que vous acceptez de risquer

entry:
  mode: immediate               # MVP : entrée immédiate au démarrage (après réconciliation, voir §5.5)
  protective_trigger: at_fill   # le trigger reduce-only est posé au moment du fill d'entrée, pas après

exit:
  logic: FIRST_TRIGGER_WINS     # pas un OCO classique — voir §5.2
  rsi:
    period: 14
    bar_type: 1-DAY
    price_source: close
    warmup_bars: 30             # nombre de barres historiques à charger avant d'activer le trading
    threshold: 20
    condition: less_than_or_equal
  price_target:
    level: 60000
    direction: above            # ou "below" — À CONFIRMER avant déploiement (voir §5.1)
    trigger_price_source: mark  # les triggers Hyperliquid sont évalués sur le mark price, pas le last trade ni bid/ask
    execution: native_reduce_only_trigger   # posé sur Hyperliquid ; le backtest doit reproduire "mark price"
                                             # ou signaler explicitement qu'il utilise un proxy

risk:
  max_notional_usdc: 300        # remplace l'ancien "max_position_pct_equity" — imprécis et dangereux par défaut
  max_leverage: 3
  emergency_close_enabled: true
  reentry_after_exit: false     # pas de réentrée automatique en MVP
```

**Note REV3 — `leverage: 3` est une précondition, pas une configuration appliquée.** Nautilus indique que le levier Hyperliquid est géré via l'interface/API Hyperliquid elle-même, pas par l'adapter. La config locale exprime donc ce qui est *voulu* ; avant toute entrée, une requête `verify_before_entry` doit confirmer l'état réel du venue (levier + mode de marge) et bloquer l'entrée (`FAIL CLOSED`) en cas d'écart.

**Note REV3.1 — politique de partial fills successifs.** Les ordres marché Hyperliquid via Nautilus sont essentiellement des IOC agressifs : un fill peut être partiel, le reliquat annulé, **et plusieurs fills peuvent s'accumuler avant l'annulation du reliquat**. L'invariant correct n'est donc pas "protéger le dernier événement de fill" mais `protected_qty == actual_net_position_qty` en continu : fill #1 (0.003 BTC) → protection 0.003 ; fill #2 (+0.003 BTC, position à 0.006) → la protection doit converger vers 0.006, via amend/replace du trigger existant ou mécanisme équivalent validé par l'adapter. C'est cette convergence — pas un seul fill isolé — qui détermine la transition `PROTECTING → OPEN` (§5.5).

### 5.4 Politique de warm-up du RSI

Le RSI(14) daily n'est pas disponible instantanément au démarrage. Séquence obligatoire :
```text
startup → charger ≥ warmup_bars (30) barres daily historiques
        → initialiser le RSI
        → indicateur initialisé et stable ?
        → seulement alors activer le trading
```
La définition de la barre daily (clôture UTC, ou frontière native Hyperliquid) doit être identique entre backtest et live pour garantir la reproductibilité.

### 5.5 Machine à états (REV3) — protection comme état, conflits comme état

**Ne jamais supposer** `process started = new strategy run`. Le démarrage est une séquence de réconciliation, pas une ouverture automatique.

**États** (révisés pour intégrer l'invariant de protection §5.2 et la hiérarchie des sources d'état) :
```text
NEVER_ENTERED
     ↓
ENTERING
     ↓ fill confirmé
PROTECTING                          ← nouveau : exposition ouverte, trigger pas encore confirmé
     ├── trigger ACCEPTED (qty = qty réellement exécutée) → OPEN
     └── trigger rejeté / timeout → EMERGENCY_EXIT → CLOSED_FINAL
OPEN
     ↓ RSI exit ou price trigger déclenché
EXITING
     ↓
CLOSED_FINAL

STATE_CONFLICT / RECOVERY_REQUIRED  ← nouveau : voir hiérarchie des sources ci-dessous
```

**Deux sources d'état, hiérarchie explicite** :
```text
Hyperliquid/Nautilus  = réalité économique actuelle (source de vérité pour l'exposition)
run_journal            = intention et historique du run (nécessaire pour distinguer
                          NEVER_ENTERED de CLOSED_FINAL, ce que l'exchange seul ne dit pas)
```
Invariant : **exchange state > local journal** pour toute question d'exposition actuelle. Si le journal dit `CLOSED_FINAL` mais que l'exchange montre une position short BTC ouverte, la conclusion n'est **jamais** "rien à faire" — c'est un `STATE_CONFLICT` : trading désactivé, réconciliation/investigation manuelle requise, emergency flatten possible selon la politique définie, mais jamais une reprise automatique silencieuse.

Séquence de démarrage :
```text
START → connect → reconcile exchange state (via Nautilus, incl. ordres externes détectés)
      → comparer à run_journal → cohérent ? reconstruire StrategyState : sinon STATE_CONFLICT
      → seulement alors agir selon l'état reconstruit
```

### 5.6 Persistance minimale — avant testnet, pas repoussée en Phase 4

Un journal durable simple suffit pour le MVP (pas de base de données sophistiquée) :

```json
{
  "run_id": "...",
  "state": "CLOSED_FINAL",
  "entry_order": "...",
  "exit_order": "...",
  "exit_reason": "rsi_exit"
}
```
Ce journal doit exister **avant la Phase 2 (testnet)**, pas après le mainnet — sans lui, la machine à états du §5.5 n'a rien à reconstruire au redémarrage.

### 5.7 `ShortBtcRsiStrategy` — pseudo-code révisé (REV3)

```python
class ShortBtcRsiStrategy(Strategy):
    def on_start(self):
        # 1. reconcile: charger l'état réel (positions/ordres, incl. ordres externes) via Nautilus
        # 2. comparer à run_journal -> cohérent ? reconstruire StrategyState : STATE_CONFLICT (stop, alerte)
        # 3. si NEVER_ENTERED:
        #      - vérifier warmup RSI
        #      - verify_before_entry: query venue leverage/margin -> mismatch = FAIL CLOSED
        #      - entrer -> state = ENTERING
        # 4. si OPEN -> ne rien ré-ouvrir, se rebrancher sur le suivi existant
        # 5. si PROTECTING -> re-vérifier le trigger, ne jamais supposer OPEN sans confirmation
        # 6. si CLOSED_FINAL -> ne rien faire (pas de réentrée)
        ...

    def on_order_filled(self, event):
        # state = PROTECTING
        # actual_net_position_qty = cache.position(...).quantity  (pas event.confirmed_net_filled_qty isolément —
        #   plusieurs fills IOC peuvent s'accumuler avant annulation du reliquat)
        # soumettre/amend le trigger reduce-only pour que protected_qty converge vers actual_net_position_qty
        #   (idéalement via normalTpsl si validé pour la version épinglée)
        # attendre confirmation ACCEPTED avec protected_qty == actual_net_position_qty avant state = OPEN
        # si rejet/timeout/non-convergence -> emergency flatten reduce-only -> state = CLOSED_FINAL
        ...

    def on_bar(self, bar):
        # Barre DAILY uniquement : met à jour le RSI(14)
        # Si state == OPEN and RsiExitRule.triggered(rsi) -> state = EXITING -> close_position(reason="rsi_exit")
        ...

    def on_quote_or_trigger_event(self, event):
        # Le seuil de prix (mark price) est déjà protégé par un trigger natif reduce-only sur Hyperliquid.
        # Ce callback ne fait qu'observer/loguer le déclenchement, il ne le réplique pas en Python.
        ...

    def on_position_closed(self, event):
        # state = CLOSED_FINAL, persist dans run_journal, log, arrêt propre
        ...
```

### 5.8 Ce que le MVP ne fait PAS (volontairement)
- Pas de ré-entrée automatique après sortie.
- Pas de sizing dynamique — notional fixe défini en config.
- Pas de gestion multi-actifs.
- Pas de trailing stop.
- Pas d'optimisation de paramètres (hyperopt) — viendra après validation du plumbing.

---

## 6. Prérequis que vous devez apporter

### 6.1 Comptes et accès
- **API/agent wallet Hyperliquid**, autorisé par votre wallet principal, plutôt que d'exposer directement la clé privée du wallet principal au runner :
  ```text
  master wallet → authorize → API wallet (pouvoirs limités) → bot
  ```
- Dépôt de collatéral sur Hyperliquid (USDC) — le montant définit votre exposition réelle une fois le levier x3 appliqué.
- Un compte Hyperliquid **testnet** avant tout déploiement mainnet. **Ne pas supposer le faucet librement accessible** : à vérifier au moment de l'implémentation (certaines conditions d'éligibilité peuvent s'appliquer selon l'état actuel de la documentation Hyperliquid) — d'où le script `check_testnet_faucet_eligibility.py` en Phase 0.

### 6.2 Environnement technique
- Python ≥ 3.11 (vérifier la version exacte requise par la release NautilusTrader courante).
- Rust toolchain (rustc + cargo) — nécessaire si vous compilez Nautilus depuis les sources plutôt que d'utiliser les wheels précompilées ; sinon `pip install nautilus_trader` suffit dans la majorité des cas.
- `pip install nautilus_trader` avec **version pinée** dans `pyproject.toml` (l'adapter Hyperliquid est en développement actif — ne pas monter de version sans relire le changelog).
- Espace disque : les wheels Nautilus peuvent peser 100–160 Mo.

### 6.3 Secrets (`.env`, jamais commité)

**Correction REV3.1 — obligatoire, pas un détail d'implémentation.** Avec un agent wallet, la clé de signature et l'adresse dont on interroge balances/positions/ordres sont deux choses différentes. Interroger l'adresse de l'agent au lieu de l'adresse du compte principal casse silencieusement la réconciliation (le système peut trader correctement tout en étant incapable de voir ce qu'il a tradé).

```text
API private key (agent wallet)  →  signing identity  →  ordres signés
master account address          →  balances, positions, ordres ouverts, WS user events
```

Nautilus expose `account_address` comme paramètre de `HyperliquidExecutionClientConfig`, pas nécessairement comme deux variables d'environnement standard distinctes par réseau. Si l'adresse maître est la même en testnet et mainnet, une seule variable `HYPERLIQUID_ACCOUNT_ADDRESS` suffit ; sinon, fournir `account_address` explicitement dans chaque config d'environnement plutôt que de supposer une convention de nommage `_TESTNET_`/`_MAINNET_` fournie nativement par l'adapter.

```
HYPERLIQUID_TESTNET_PK=...                # clé de signature de l'API/agent wallet testnet
HYPERLIQUID_ACCOUNT_ADDRESS=0x...         # adresse du wallet PRINCIPAL — commune aux deux réseaux si applicable ;
                                           # sinon dupliquer/paramétrer par config d'environnement (voir ci-dessus)
HYPERLIQUID_TESTNET_VAULT=...
HYPERLIQUID_MAINNET_PK=...                # à n'ajouter qu'après validation testnet complète
HYPERLIQUID_MAINNET_VAULT=...
```

### 6.4 Décisions à prendre de votre côté avant le premier commit
1. **Direction du seuil 60 000 $** (stop ou target — voir §5.1).
2. **Notional exact en USDC** pour le MVP (pas un pourcentage d'equity — voir §5.3), capital que vous acceptez de risquer, pas votre capital total.
3. Confirmation : marge **isolated** (recommandé) plutôt que cross pour ce premier test.
4. Où héberger le `LiveRunner` en continu (poste personnel, VPS, serveur dédié) — Hyperliquid nécessite une connexion WebSocket stable en permanence pendant que la position est ouverte.
5. Mise en place de l'API/agent wallet (§6.1) plutôt que la clé du wallet principal.

---

## 7. Roadmap

### Phase 0 — Connectivité
- [ ] Créer le dépôt selon la structure §4
- [ ] Installer NautilusTrader (version pinée) + confirmer le statut exact de l'adapter Hyperliquid
- [ ] Vérifier dans le changelog de la version pinée le comportement réel de `normalTpsl` pour Hyperliquid (atomique ou non) — ne pas supposer, confirmer
- [ ] Configurer l'API/agent wallet testnet + `HYPERLIQUID_TESTNET_ACCOUNT_ADDRESS` (§6.3)
- [ ] Vérifier l'éligibilité au faucet testnet, obtenir le mock USDC
- [ ] Se connecter en lecture seule (market data uniquement) pour valider le flux de bars daily BTC et le mark price

### Phase 1 — Logique métier pure (sans Nautilus)
- [ ] Écrire `RsiExitRule`, `PriceExitRule`, `PositionSizing`, `StrategyState` dans `domain/`
- [ ] Tests unitaires déterministes, y compris les cas de restart et de déclenchement quasi simultané des deux sorties
- [ ] Aucune de ces briques ne doit importer Nautilus

### Phase 2 — Backtest
- [ ] Télécharger un historique daily BTC-USD-PERP suffisant
- [ ] Écrire `ShortBtcRsiStrategy` (orchestration Nautilus) + `RiskGuard`
- [ ] Backtest incluant warm-up RSI, funding, frais, entrée, sortie, absence de réentrée

### Phase 3 — Exécution testnet
- [ ] Déployer `LiveRunner` contre le testnet Hyperliquid
- [ ] Valider la reconciliation d'ordres (REST vs WS vs état local) sur un cycle complet ouverture→fermeture
- [ ] Valider l'invariant de couverture : toute exposition exécutée transite par `PROTECTING` et ne devient `OPEN` qu'après confirmation venue que `protected_qty == actual_net_position_qty` (pas "trigger posé au moment du fill" — promesse temporelle abandonnée en REV3.1)
- [ ] Faire tourner plusieurs jours pour observer la stabilité de la connexion WebSocket

### Phase 3.5 — Tests de défaillance (obligatoire avant mainnet)
- [ ] Tuer le process pendant qu'une position est ouverte, puis redémarrer → vérifier la réconciliation
- [ ] Redémarrer après une sortie déjà finalisée → vérifier l'absence de réentrée
- [ ] Simuler une perte réseau / déconnexion WebSocket prolongée
- [ ] Simuler des événements dupliqués et un déclenchement quasi simultané RSI/prix
- [ ] **Simuler des partial fills successifs de l'entrée** (fill #1 puis fill #2 avant annulation du reliquat) → vérifier que le trigger reduce-only converge à chaque fois vers `actual_net_position_qty` (amend/replace), pas seulement vers le dernier fill isolé
- [ ] Simuler un rejet/timeout du trigger reduce-only après un fill → vérifier le passage `PROTECTING → EMERGENCY_EXIT → CLOSED_FINAL`
- [ ] Simuler un désaccord entre `run_journal` et l'état réel de l'exchange → vérifier le passage en `STATE_CONFLICT` (jamais une reprise automatique silencieuse)
- [ ] Simuler un levier/mode de marge réel différent de `desired_margin` → vérifier `FAIL CLOSED` avant toute entrée

### Phase 4 — Mainnet, capital réel limité (canary)
- [ ] Démarrer avec le notional fixe défini en §5.3/§6.4, volontairement faible
- [ ] Surveillance manuelle rapprochée les premiers jours
- [ ] Journalisation complète pour post-mortem si la sortie ne se comporte pas comme prévu

### Phase 5 — Itération (post-MVP)
- [ ] Ajouter le sizing dynamique
- [ ] Étendre à d'autres paires ou à une deuxième stratégie
- [ ] Réévaluer périodiquement le statut de l'adapter Hyperliquid dans Nautilus

---

## 8. Notes de gouvernance du projet

- **Licence Nautilus (LGPL-3.0)** : compatible avec un usage propriétaire de la bibliothèque telle quelle. Si vous modifiez le code de Nautilus lui-même (pas seulement votre couche applicative), une revue des obligations LGPL est nécessaire avant toute distribution.
- **Surveillance de l'adapter Hyperliquid** : son statut "active development" signifie des changements de comportement possibles entre versions. Version pinée dans `pyproject.toml`, montée de version uniquement après lecture du changelog.
- **Réglementaire** : cette architecture suppose un résident du Québec, et le statut est **présumé admissible**, non confirmé juridiquement de façon positive. Elle ne doit pas être redéployée telle quelle pour un utilisateur en Ontario (Hyperliquid y est explicitement restreint) sans revalider l'ensemble du filtre légal. À revérifier contre les ToU Hyperliquid en vigueur avant toute activation mainnet.
