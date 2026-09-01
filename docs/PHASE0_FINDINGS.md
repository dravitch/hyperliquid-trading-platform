# PHASE0_FINDINGS.md

Preuve empirique du spike Phase 0, obtenue en inspectant directement le package
`nautilus_trader` installé (pas de credentials Hyperliquid utilisés — inspection de code
et de schéma uniquement). REV3.1 reste la baseline normative ; ce document ne la modifie
pas — il confirme ou infirme ses hypothèses. Toute divergence trouvée est signalée
explicitement plutôt que corrigée silencieusement dans la spec.

## Environnement vérifié

- `nautilus_trader==1.231.0` (installé via `pip install nautilus_trader`, wheel
  manylinux_2_35 x86_64, ~189 MB). **Version à pinner dans `pyproject.toml`.**
- Adapter Hyperliquid : module `nautilus_trader.adapters.hyperliquid`, exports incluant
  `HyperliquidDataClientConfig`, `HyperliquidExecClientConfig`,
  `HyperliquidLiveDataClientFactory`, `HyperliquidLiveExecClientFactory`,
  `HyperliquidInstrumentProvider`. Fichiers Python : `config.py`, `data.py` (39 KB),
  `execution.py` (73 KB), `providers.py`, `factories.py`, `enums.py`, `constants.py`.
  Le cœur (types, client HTTP/WS, enums métier) est en Rust via
  `nautilus_trader.core.nautilus_pyo3.hyperliquid`.

## Confirmations (REV3.1 tenait juste)

1. **`account_address` — une seule variable, pas deux par réseau.**
   `HyperliquidExecClientConfig.account_address` (docstring officielle) :
   > "The main account address when using an agent wallet (API sub-key). [...]
   > If `None` and no explicit `vault_address` is set, then will source the
   > `HYPERLIQUID_ACCOUNT_ADDRESS` environment variable."
   Confirme la correction demandée en REV3.1 §6.3 : `HYPERLIQUID_ACCOUNT_ADDRESS`
   (une seule variable), pas `HYPERLIQUID_TESTNET_ACCOUNT_ADDRESS` /
   `HYPERLIQUID_MAINNET_ACCOUNT_ADDRESS`. `account_address` peut aussi être passé
   explicitement par config si les adresses diffèrent par environnement.

2. **Clés de signature séparées par réseau, comme prévu.**
   `private_key` : "If `None` then will source the `HYPERLIQUID_PK` or
   `HYPERLIQUID_TESTNET_PK` environment variable based on `environment`." Idem pour
   `vault_address` → `HYPERLIQUID_VAULT` / `HYPERLIQUID_TESTNET_VAULT`.

3. **Le levier n'est pas géré par l'adapter.**
   Recherche exhaustive de `leverage`, `margin_mode`, `isolated`, `cross`,
   `set_leverage` dans `execution.py` : **zéro occurrence**. Confirme §5.3 REV3.1 :
   `desired_margin` est une précondition à vérifier côté venue, pas une configuration
   que l'adapter applique. La vérification `verify_before_entry` doit passer par
   l'API/interface Hyperliquid elle-même, pas par un paramètre Nautilus.

4. **`reduce_only` et triggers natifs sont bien supportés au niveau ordre.**
   `execution.py` : `reduce_only=order.is_reduce_only` transmis à la soumission
   (lignes ~705, ~919), `trigger_price` géré pour STOP_LIMIT / TRAILING_STOP_LIMIT /
   LIMIT_IF_TOUCHED. `HyperliquidConditionalOrderType` (Rust, pyo3) expose
   `STOP_LIMIT`, `STOP_MARKET`, `TAKE_PROFIT_LIMIT`, `TAKE_PROFIT_MARKET`,
   `TRAILING_STOP_LIMIT`, `TRAILING_STOP_MARKET`.

5. **`HyperliquidTpSl` existe bien dans le core Rust** — enum à deux variants `SL` / `TP`,
   utilisé pour taguer les ordres take-profit/stop-loss côté Hyperliquid.

## Divergence critique trouvée (justifie directement l'invariant REV3.1 §5.2)

6. **Les enfants trigger ne sont PAS confirmés atomiquement avec le parent, même via
   `SubmitOrderList`.** Commentaire du code source, `execution.py`,
   `_submit_order_list()` :
   > "Deferred trigger children are intentionally absent from pyo3_reports; they stay
   > SUBMITTED until the user-events stream delivers the accept."
   C'est une confirmation directe, dans le code, de la nécessité de l'invariant
   `PROTECTING` : même en soumettant entrée + protection comme une liste groupée, la
   confirmation d'acceptation du trigger arrive de façon asynchrone via le flux WS
   d'événements utilisateur — jamais garantie dans la même transaction que le fill
   parent. **REV3.1 §5.2 et §5.5 n'ont pas besoin d'être modifiés ; ce résultat les
   valide.**

## Confirmé documentairement sans credentials

- **Le mark price est la règle de déclenchement venue des TP/SL.** La documentation officielle
  Hyperliquid indique explicitement que les ordres TP/SL sont déclenchés par le mark price. La
  documentation NautilusTrader Hyperliquid indique également que tous les ordres conditionnels
  sont évalués contre le mark price. Cette règle est donc `DOCUMENTED_CONFIRMED` et ne nécessite
  plus une clé API pour être établie.
- Cette confirmation ne prouve pas encore que notre backtest reproduit la même sémantique, ni que
  le lifecycle live Nautilus 1.231.0 se comporte correctement face aux événements réels de la
  venue. Ces deux preuves restent séparées :

  ```text
  mark_price_as_venue_trigger_rule = DOCUMENTED_CONFIRMED
  our_backtest_implements_same_semantics = TO_PROVE
  live_adapter_and_venue_lifecycle = TO_PROVE_TESTNET
  ```

Sources :

- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
- https://nautilustrader.io/docs/latest/integrations/hyperliquid/

## Non vérifié dans ce spike (nécessite test ou caractérisation supplémentaire)

- Comportement exact de `normalTpsl` / `grouping` côté API Hyperliquid elle-même (le
  code Python consulté est le client Nautilus, pas la doc API Hyperliquid brute) — la
  chaîne `normalTpsl` n'apparaît pas dans le code source Python de l'adapter ; elle
  pourrait être gérée entièrement côté Rust sans être exposée telle quelle. **À
  confirmer par un test testnet réel en Phase 3, pas par lecture de code seule.**
- Fidélité du runner `BacktestEngine` à la règle venue du mark price. La règle est documentée,
  mais le modèle de données injecté et le mécanisme de déclenchement du backtest doivent encore
  être démontrés par des tests.
- Lifecycle réel testnet du trigger : soumission, acceptation différée, fill, événements WS,
  timeout et comportement après redémarrage.
- Éligibilité au faucet testnet (nécessite un compte réel).
- Comportement réel de `verify_before_entry` — aucune primitive Nautilus dédiée trouvée
  pour interroger le levier/mode de marge actuel du compte ; il faudra probablement
  passer par une requête HTTP directe à l'API Hyperliquid (hors Nautilus) pour cette
  vérification. **Ceci est un écart d'implémentation à anticiper**, pas une divergence
  de conception — REV3.1 reste correcte sur le principe, mais le "comment" doit être
  précisé en Phase 1/3.

### Contrat fail-closed du venue verifier public

L'endpoint public `clearinghouseState` expose les positions et leur état de marge à partir de
l'adresse du compte. Cependant, l'absence de position BTC ne prouve pas que le levier et le mode
de marge préconfigurés sont observables ou conformes. Le reçu du verifier doit donc être ternaire :

```text
VERIFIED     état observé et conforme à la configuration attendue
MISMATCH     état observé et différent de la configuration attendue
UNVERIFIABLE preuve insuffisante, notamment si aucun état BTC pertinent n'est exposé
```

`MISMATCH` et `UNVERIFIABLE` interdisent tous deux l'entrée. Une position absente ne doit jamais
être convertie implicitement en succès. Le réglage du levier et les modifications de marge restent
des opérations privées signées via l'endpoint `exchange`, notamment l'action `updateLeverage`.

## Actions pour la suite de Phase 0

- [x] Épingler `nautilus_trader==1.231.0` dans `pyproject.toml`.
- [x] Caractériser le chemin `normalTpsl` de Nautilus 1.231.0 par inspection du commit source.
- [x] Confirmer documentairement que Hyperliquid utilise le mark price pour les TP/SL.
- [ ] Vérifier avec `BacktestEngine` que notre implémentation respecte cette sémantique.
- [ ] Vérifier sur testnet le lifecycle réel trigger, acceptation, fill et événements WS.
- [ ] Déterminer ce que `clearinghouseState` permet réellement de vérifier avant entrée sans
      position BTC.
- [ ] Implémenter le venue verifier HTTP avec les résultats `VERIFIED`, `MISMATCH` et
      `UNVERIFIABLE`.

## P0-HL-001 — Native TP/SL grouping capability: PARTIAL (mise à jour, source commit exact)

Vérification affinée contre le commit exact tagué `v1.231.0` (`27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`,
publié 2 août 2026 sur PyPI) — plus fiable que l'inspection `strings` initiale.

**Confirmé (couche Rust)** :
- `HyperliquidExecGrouping` (dans `http/models.rs`) expose bien trois valeurs :
  `Na` / `NormalTpsl` / `PositionTpsl`, sérialisées `na` / `normalTpsl` / `positionTpsl`.
  Le concept existe réellement dans le code, pas seulement comme chaîne isolée dans le
  binaire.
- `OrderBuilder::.grouping(Grouping::NormalTpsl)` existe et un test unitaire vérifie que
  la requête produite contient `grouping == "normalTpsl"`. La plomberie Rust sait donc
  construire et sérialiser ce grouping correctement.
- Les structures de trigger exposent `reduce_only`, `is_market`, `trigger_px`, `tpsl`
  (`TpSlRequest::Sl`/`Tp`).

**Non confirmé (couche Python publique)** :
- `py_submit_orders()` — l'API exposée pour `1.231.0` — accepte uniquement
  `submit_orders(orders)`, **sans paramètre `grouping`**. Rien dans le chemin Python
  `_submit_order_list → self._client.submit_orders(...)` ne sélectionne le grouping
  selon la forme du bracket.
- Confirmation externe indépendante : l'issue GitHub nautechsystems/nautilus_trader
  **#3810** documente précisément ce défaut — un chemin de soumission bracket/order-list
  aboutit à `grouping="na"` plutôt que `normalTpsl`, malgré l'existence du type Rust.

**Conclusion** : *capability exists ≠ strategy path uses it*. Il ne faut pas supposer
qu'un `OrderList`/bracket Nautilus utilise automatiquement `normalTpsl` avec cette
version. **L'invariant `PROTECTING → OPEN` de REV3.1 §5.2/§5.5 reste obligatoire et est
maintenant validé par deux sources indépendantes** (comportement observé dans
`_submit_order_list` et cette issue externe), pas seulement par prudence de conception.

**Vérifié en complément (API publique installée)** : `OrderFactory.stop_market()`
(`nautilus_trader.common.factories.OrderFactory`) accepte directement
`reduce_only=True` et `trigger_price` — le chemin `PROTECTING → native trigger → OPEN`
de REV3.1 est donc immédiatement implémentable via l'API standard, sans contourner
l'adapter ni dépendre de `normalTpsl`.
