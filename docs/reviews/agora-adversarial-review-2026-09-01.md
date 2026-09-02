# Revue adversariale AGORA — jalon NautilusTrader

Date : 1 septembre 2026  
Expérience canonique : `AGO-EXP-2026-0026`  
Révision AGORA : `0c43e6d3923c22c6b4325a6e80325253a460a2e4`  
Providers : Claude Sonnet 5 / DeepSeek V4 Flash; juge Claude Sonnet 5

## Verdict

`NUANCED`, confiance `0.68`.

Le code constituait une base honnête de développement, mais pas une base suffisamment prouvée
pour un runner testnet. Le consensus AGORA exigeait des artefacts vérifiables : test moteur/cache
réel, gestion transactionnelle des exceptions, trace versionnée, traçabilité de l'exit et
interdiction d'une simple preuve booléenne de marge.

Ce rapport ne transforme pas le verdict en autorisation testnet. Il documente les corrections
effectuées et les inconnues restantes.

## Objections acceptées et corrigées

### Critique — cache position potentiellement absent au callback de fill

Correction : ajout d'un retry borné de synchronisation. Si la position reste invisible après
trois tentatives, transition vers `RECOVERY_REQUIRED`; aucun faux `OPEN`.

Preuve : un test avec le vrai `BacktestEngine` 1.231.0 montre que, dans son chemin déterministe,
la position short est déjà visible dans `on_order_filled`. Cette observation ne prouve pas l'ordre
live; le retry reste donc actif.

### Critique — exception pendant la soumission du trigger avant watchdog

Correction : le timer est désormais armé avant `submit_order` ou `modify_order`. Toute exception
synchrone annule le timer, passe à `EMERGENCY_EXIT`, persiste, puis demande le flatten.

Preuve : AT-05 injecte une exception et vérifie l'ordre causal exact des actions.

### Critique — exception de soumission d'entrée après persistance `ENTERING`

Correction : toute exception synchrone impose `RECOVERY_REQUIRED`, persiste l'état et interdit la
réentrée automatique.

Preuve : AT-06 injecte l'exception et vérifie le snapshot réellement persisté.

### Majeure — `exit_order` jamais assigné

Correction : l'adapter construit désormais lui-même l'ordre MARKET reduce-only, stocke son
`client_order_id`, persiste, puis le soumet avec le `position_id` Nautilus.

### Critique — preuve de marge réduite à un booléen

Correction : suppression de `venue_margin_verified`. L'entrée exige maintenant un reçu JSON
structuré lié au compte, réseau, instrument, mode, levier, source et horodatage. Un reçu expiré,
mal formé ou correspondant à un autre compte est refusé.

Limite : ce reçu est un artefact auditable, pas une signature cryptographique Hyperliquid. Le
runner qui interroge réellement la venue reste à écrire; en son absence, aucun reçu n'existe et
l'entrée reste bloquée.

### Critique — identité protectrice ambiguë au restart

Correction : la réconciliation inventorie les triggers reduce-only BUY ouverts. L'état devient
`STATE_CONFLICT` si l'identité journalisée ne correspond pas exactement et uniquement à l'ordre
venue observé. Une protection externe ou remplacée ne produit jamais silencieusement `OPEN`.

### Majeure — absence de format de trace falsifiable

Correction : ajout d'un schéma JSON Lines version 1, avec séquence causale, événement, état et
quantités exactes. Le contrat est dans `agora-test-contract-2026-09-01.md`.

## Objections nuancées ou rejetées

- L'idée d'un watchdog externe a été rejetée : elle ajoutait un composant sans preuve de fiabilité
  supérieure. La correction est locale à la frontière de soumission.
- L'absence d'atomicité `normalTpsl` n'est pas un défaut caché de notre code : elle était déjà
  explicitement documentée. Elle reste néanmoins un risque réel non éliminable dans 1.231.0.
- `exit_order` est classé majeur pour la traçabilité, pas critique pour l'exposition; sa correction
  est néanmoins incluse dans ce jalon.

## Preuves exécutées

- 40 tests réussis au moment de cette revue. La suite courante en compte 120; voir
  `PROGRESSION.md` pour les jalons postérieurs.
- Ruff réussi.
- `git diff --check` réussi.
- Test réel `BacktestEngine` avec message queue et observation du cache dans le callback de fill.
- Tests d'injection de faute pour entrée et protection.
- Tests de reçu de marge frais/périmé/mauvais compte.
- Test de conflit d'identité protectrice.
- Test du schéma de trace JSONL.

Deux `Pandas4Warning` proviennent de `BacktestEngine.run()` dans NautilusTrader 1.231.0. Ils
signalent une dette de compatibilité interne à la dépendance et ne sont pas masqués globalement.

## Risques encore ouverts — blocants avant testnet

1. Aucun runner ne produit encore le reçu de marge depuis une interrogation Hyperliquid réelle.
2. Le test backtest ne prouve pas l'ordre concurrent du moteur live.
3. Les partial fills successifs et rejet/timeout concurrents doivent encore être exercés avec des
   événements Nautilus complets, pas seulement au niveau domaine/orchestration.
4. La direction 60 000 USD et le notional 300 USDC restent non confirmés.
5. Aucun secret, compte testnet ou cycle de réconciliation REST/WS n'a été validé.

Décision : poursuivre le développement hors trading; runner testnet toujours bloqué.
