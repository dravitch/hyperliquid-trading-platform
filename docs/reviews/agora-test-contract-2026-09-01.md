# Contrat de tests post-revue AGORA

Statut : assertions figées avant exécution des nouveaux tests.

## Schéma de trace

Chaque scénario d'intégration produit une suite JSON Lines conforme à la version `1` :

```json
{"schema_version":1,"sequence":1,"event":"...","state":"...","actual_qty":"...","protected_qty":"..."}
```

Champs obligatoires :

- `schema_version` : entier égal à `1` ;
- `sequence` : entier strictement croissant dans un scénario ;
- `event` : nom stable de l'événement observé ;
- `state` : état métier après l'événement ;
- `actual_qty` et `protected_qty` : chaînes décimales exactes.

Les traces de test sont déterministes. Les timestamps muraux sont exclus car ils empêchent une
comparaison reproductible; l'ordre causal est représenté par `sequence`.

## Assertions préenregistrées

| ID | Scénario | Assertion attendue | Point AGORA |
|---|---|---|---|
| AT-01 | Fill reçu avant visibilité de la position dans le cache | Aucun état `OPEN`; une nouvelle tentative de convergence est planifiée ou l'état devient `RECOVERY_REQUIRED`. | 1, 2 |
| AT-02 | Position visible avant callback de fill | Le trigger est soumis pour la quantité nette exacte et l'état reste `PROTECTING` jusqu'à acceptation. | 1 |
| AT-03 | Deux fills IOC successifs | Après le second fill, `actual_qty` augmente et l'état redevient `PROTECTING` jusqu'au resize accepté à quantité égale. | 3 |
| AT-04 | Timeout et rejet concurrents | Un seul passage logique vers `EMERGENCY_EXIT`; aucune exception de transition ne s'échappe. | 3 |
| AT-05 | Exception synchrone pendant submit/modify du trigger | Le timer était déjà armé; l'état devient `EMERGENCY_EXIT`, est persisté et un flatten reduce-only est demandé. | 9 |
| AT-06 | Exception synchrone de soumission de l'entrée après journal ENTERING | L'état persistant devient `RECOVERY_REQUIRED`; aucune réentrée automatique n'est possible. | 10 |
| AT-07 | Sortie RSI | L'identifiant de l'ordre de fermeture créé est persisté dans `exit_order`. | 6 |
| AT-08 | ID protecteur journalisé absent mais autre trigger protecteur ouvert | La réconciliation n'annonce jamais `OPEN` à partir du seul journal; elle inventorie les ordres réels et choisit `OPEN` uniquement si une couverture unique et exacte est démontrée, sinon `STATE_CONFLICT`. | 7 |
| AT-09 | Configuration hors tests | Aucun littéral/configuration de production ne peut fournir une preuve booléenne `venue_margin_verified=True`; une preuve venue structurée est requise. | 4 |

## Gate

Un échec de AT-01, AT-03, AT-04, AT-05, AT-06 ou AT-08 bloque le runner testnet. AT-07 est
majeur pour la traçabilité et doit être corrigé dans le même jalon. AT-09 interdit l'activation de
l'entrée tant que le vérificateur venue dédié n'existe pas.
