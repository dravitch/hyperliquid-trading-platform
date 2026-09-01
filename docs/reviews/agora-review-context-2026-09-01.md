# Dossier soumis à AGORA — revue adversariale du jalon Nautilus

## Question auditée

Le jalon actuel applique-t-il réellement les invariants de sécurité de la spécification et
constitue-t-il une base défendable pour poursuivre vers un runner testnet, sans prétendre qu'il
est déjà prêt à trader ? Identifier en priorité les défaillances susceptibles de créer une
position non protégée, une réentrée, un faux état `OPEN`, une réconciliation erronée ou un exit
dupliqué.

## Périmètre et preuves

- Python `>=3.12,<3.15`, `nautilus_trader==1.231.0`.
- 31 tests passent; Ruff passe.
- Aucun test live, aucun secret et aucun ordre testnet.
- `enable_order_submission=False` et `venue_margin_verified=False` par défaut.
- Le domaine pur couvre seuils, sizing, machine à états, journal et réconciliation.
- L'adapter est `src/hltrader/strategies/short_btc_rsi.py`.

## Fait vérifié sur `normalTpsl`

Le tag officiel NautilusTrader v1.231.0 reconnaît les brackets OTO comme `NormalTpsl`, mais
`submit_order_list` retire les enfants, les stocke dans `staged_brackets`, soumet le parent seul
avec `grouping: na`, puis soumet les enfants après réception d'un fill. Le test officiel
`test_submit_order_list_normal_tpsl_stages_children_until_parent_fill` vérifie une première action
à un seul ordre. L'atomicité entrée+protection est donc explicitement déclarée fausse.

## Machine à états

Transitions principales :

```text
NEVER_ENTERED -> ENTERING -> PROTECTING -> OPEN -> EXITING -> CLOSED_FINAL
                                  |                     ^
                                  -> EMERGENCY_EXIT ----|
STATE_CONFLICT et RECOVERY_REQUIRED bloquent l'action automatique.
```

`record_exposure(actual_qty, protected_qty)` accepte ENTERING, PROTECTING ou OPEN. Il choisit OPEN
si les quantités sont égales, sinon PROTECTING. `request_exit` prend un verrou et ne gagne que
depuis OPEN. `confirm_closed` n'accepte que EXITING ou EMERGENCY_EXIT.

## Adapter — démarrage

`on_start` charge l'instrument, vérifie que le bar type correspond, s'abonne aux quotes, appelle
`_restore_state`, bloque sur conflit/recovery, puis demande 30 barres daily. Le callback s'abonne
aux barres live et appelle `_maybe_enter` uniquement si warm-up et RSI sont prêts.

`_maybe_enter` refuse si l'état n'est pas NEVER_ENTERED, si l'un des deux verrous de soumission
est faux ou si aucun quote n'est en cache. Il calcule la quantité sur le bid, crée un MARKET SELL,
passe ENTERING, persiste, puis soumet.

## Adapter — fill et protection

Sur fill de l'entrée, `_converge_protection` lit la somme des positions short ouvertes depuis le
cache. Il lit la quantité du trigger protecteur uniquement si l'ordre cache est accepté et de type
STOP_MARKET ou MARKET_IF_TOUCHED. Il appelle `record_exposure(actual, protected)`. Si non couvert,
il crée un trigger BUY reduce-only ou modifie sa quantité, pose un timer de dix secondes et
persiste. Une acceptation/mise à jour relit position et trigger, appelle `confirm_protection`, puis
reconverge si les quantités divergent. Timeout/rejet -> EMERGENCY_EXIT -> `close_position` de
toutes les positions short.

Direction `above` -> STOP_MARKET. Direction `below` -> MARKET_IF_TOUCHED. Les triggers Hyperliquid
sont mark-price selon la documentation de l'adapter.

## Adapter — sorties

RSI Nautilus est normalisé 0..1 puis multiplié par 100. Sur barre daily et état OPEN, RSI <= 20
appelle `_request_close`; le gagnant atomique passe EXITING, persiste, puis appelle
`close_position(reduce_only=True)`. Un fill du trigger prix appelle `request_exit(PRICE)` puis
persiste. `on_position_closed` confirme CLOSED_FINAL, sinon marque un conflit.

## Journal et réconciliation

Le journal JSON est remplacé atomiquement avec fsync fichier+dossier. Il stocke run_id, state,
entry_order, exit_order, exit_reason et protective_order. Au restart, les IDs texte sont
reconstruits en ClientOrderId. L'exposition exchange est la somme des positions short ouvertes.
La protection est dérivée d'un seul ID protecteur journalisé et de son ordre dans le cache.

`reconcile` : sans journal et sans exposition -> NEVER_ENTERED; sans journal avec exposition ->
STATE_CONFLICT; journal CLOSED_FINAL sans exposition -> CLOSED_FINAL; CLOSED_FINAL avec exposition
-> STATE_CONFLICT; journal actif sans exposition -> RECOVERY_REQUIRED; sinon OPEN si protected ==
actual, PROTECTING sinon.

## Points explicitement non prouvés

1. L'ordre des mises à jour du cache Nautilus par rapport à `on_order_filled` n'est pas testé.
2. Aucun test avec Strategy enregistrée dans un moteur/cache réel.
3. Aucun test de timer simulé, rejet venue ou fills IOC successifs via événements Nautilus.
4. La preuve `venue_margin_verified` est un booléen de config, pas encore le résultat signé d'une
   requête venue.
5. Aucun runner live/backtest n'est câblé.
6. `exit_order` n'est actuellement jamais assigné par l'adapter.
7. La protection de restart dépend de l'ID journalisé; les ordres externes ou un remplacement de
   venue non reflété par cet ID pourraient être mal classés.
8. La direction 60 000 et le notional 300 ne sont pas confirmés par l'opérateur.
9. Le timer de protection est posé après l'appel `submit_order`/`modify_order`; une exception
   synchrone entre les deux n'est pas explicitement traitée.
10. La soumission d'entrée intervient après persistance ENTERING; une exception de soumission
    laisse un journal ENTERING sans exposition.

## Contrat demandé à la revue

Classer chaque objection en critique, majeure, mineure ou non fondée. Ne pas assimiler tests verts
à sûreté live. Proposer des corrections vérifiables et identifier les affirmations qui doivent
rester bloquées faute de preuve. La décision attendue concerne la poursuite du développement vers
un runner testnet, pas l'autorisation de trader sur testnet ou mainnet.
