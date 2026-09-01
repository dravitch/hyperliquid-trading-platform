# Progression du projet

Dernière mise à jour : 1 septembre 2026

## État général

Le socle métier de la Phase 1 et un premier adapter `ShortBtcRsiStrategy` pour NautilusTrader
sont implémentés et testés localement. Aucun ordre réel ou testnet n'a été soumis. Le déploiement
demeure explicitement désactivé dans la configuration.

## Réalisé

### Initialisation du projet

- Création du paquet Python `hltrader` avec une structure `src/`.
- Ajout de `pyproject.toml` et du verrou de dépendances `uv.lock`.
- Ajout de `.gitignore`, `.env.example` et d'un README de démarrage.
- Ajout des configurations YAML pour la stratégie, le risque et Hyperliquid testnet.
- Séparation maintenue entre le domaine Python pur et la future orchestration NautilusTrader.

### Logique métier pure

- `RsiExitRule` : sortie lorsque le RSI est inférieur ou égal au seuil configuré.
- `PriceExitRule` : seuil inclusif configurable dans les directions `above` et `below`.
- `PositionSizing` : conversion d'un notional USDC fixe en quantité de base, arrondie vers le
  bas selon le pas autorisé par la venue. Le levier ne modifie pas le notional.
- Politique de warm-up du RSI exigeant le nombre de barres configuré et un indicateur initialisé.

### Machine à états et protection

- États `NEVER_ENTERED`, `ENTERING`, `PROTECTING`, `OPEN`, `EXITING`, `EMERGENCY_EXIT`,
  `CLOSED_FINAL`, `STATE_CONFLICT` et `RECOVERY_REQUIRED`.
- Arbitrage thread-safe `FIRST_TRIGGER_WINS` afin qu'un seul signal obtienne le droit de fermer.
- Prise en charge de partial fills successifs, y compris lorsqu'un nouveau fill arrive après la
  protection complète du fill précédent.
- Retour immédiat à `PROTECTING` lorsque l'exposition augmente et n'est plus entièrement couverte.
- Passage obligatoire par `EMERGENCY_EXIT` en cas d'échec de la protection.
- Absence de réentrée après `CLOSED_FINAL`.

### Risque, réconciliation et persistance

- `RiskGuard` fail-closed pour le notional maximum, le levier et le mode de marge.
- Vérification exacte de la marge isolée et du levier demandé avant autorisation d'entrée.
- Réconciliation donnant priorité à l'exposition constatée sur l'exchange.
- Détection d'un conflit si le journal indique une fermeture alors qu'une position subsiste.
- Détection d'une exposition sans journal local.
- Reconstruction des états `OPEN` ou `PROTECTING` selon la quantité réellement protégée.
- Journal JSON écrit atomiquement, avec `fsync`, pour résister aux interruptions de processus.
- Validation stricte de la configuration YAML du MVP.

### Tests et qualité

- 31 tests déterministes réussis.
- Scénarios couverts : seuils inclusifs, sizing, partial fills, double signal concurrent,
  protection rejetée, absence de réentrée, redémarrage, conflits journal/exchange, journal
  corrompu et désaccord de marge ou de levier.
- `ruff check` réussi.
- Compilation Python réussie.

### Intégration NautilusTrader — jalon 1

- Version stable épinglée : `nautilus_trader==1.231.0`.
- Contrat Python aligné sur la wheel : Python `>=3.12,<3.15`.
- `ShortBtcRsiConfig` et `ShortBtcRsiStrategy(Strategy)` ajoutés.
- Chargement de 30 barres daily, initialisation du RSI Nautilus et abonnement au flux live.
- Conversion explicite du RSI Nautilus, normalisé entre 0 et 1, vers l'échelle métier 0–100.
- Abonnement aux quotes exigé avant l'ordre marché Hyperliquid.
- Entrée short par notional fixe après warm-up et seulement si les deux verrous
  `enable_order_submission` et `venue_margin_verified` sont vrais.
- Création d'un trigger natif reduce-only après fill de l'entrée.
- Direction `above` mappée sur `STOP_MARKET`; direction `below` mappée sur
  `MARKET_IF_TOUCHED`.
- Redimensionnement du trigger lors de partial fills successifs.
- Confirmation d'acceptation exigée avant passage de `PROTECTING` à `OPEN`.
- Timeout de protection avec passage à `EMERGENCY_EXIT` et flatten reduce-only.
- Sortie RSI arbitrée par `FIRST_TRIGGER_WINS` et fermeture reduce-only.
- Persistance de l'identifiant du trigger protecteur dans le journal.

### Vérification de `normalTpsl`

Le tag officiel NautilusTrader `v1.231.0` a été inspecté. L'adapter reconnaît la structure
`NormalTpsl`, mais ne la transmet pas atomiquement à Hyperliquid : il envoie l'entrée seule avec
`grouping: na`, conserve les enfants localement, puis les soumet après le premier fill. Une
fenêtre d'exposition non protégée subsiste donc. Cette conclusion et ses conséquences sont
consignées dans `docs/decisions/0002-hyperliquid-normal-tpsl-v1.231.0.md`.

Commande de validation :

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
```

## Accès GitHub

- Dépôt : `git@github.com:dravitch/hyperliquid-trading-platform.git`.
- Visibilité : privée.
- Compte GitHub actif dans `gh` : `dravitch`.
- Clé publique : `/home/andrei/.ssh/id_ed25519_dravitch.pub`.
- Empreinte vérifiée : `SHA256:7Q0E8ngs3J/Cbf/uTOWsThowkrlyDd6dmmGSKqMkVu4`.
- Authentification SSH explicite réussie comme utilisateur GitHub `dravitch`.
- Accès Git au dépôt vérifié avec la clé privée correspondante et `IdentitiesOnly=yes`.
- Le dépôt distant ne contient actuellement aucune référence : il est vide et n'a pas encore de
  branche par défaut.

La clé SSH choisie par défaut sur cette machine reste celle de `symbioticode`. Les opérations Git
sur ce dépôt devront donc employer explicitement `id_ed25519_dravitch`, ou une entrée dédiée dans
`~/.ssh/config` devra être ajoutée.

## Prochaines étapes

1. Initialiser le dépôt Git local et publier le socle actuel sur une première branche distante.
2. Configurer durablement l'identité SSH `dravitch` pour ce dépôt.
3. Ajouter le runner Nautilus testable et la vérification directe du mode de marge/levier côté
   Hyperliquid avant d'autoriser l'entrée.
4. Ajouter des tests d'intégration avec cache, événements de fills successifs et timeout simulé.
5. Implémenter le runner de backtest et vérifier la sémantique mark-price du trigger.
6. Avant tout testnet, confirmer la direction du seuil de 60 000 USD, le notional de 300 USDC et
   le mode de marge isolée.

## Décisions et blocages ouverts

- Direction du seuil de prix : `above` est configuré provisoirement, mais doit être confirmé.
- Notional : 300 USDC est une valeur provisoire à confirmer.
- NautilusTrader est installé et épinglé, mais le runner live/testnet n'est pas encore câblé.
- L'API standard de stratégie ne prouve pas le mode de marge et le levier Hyperliquid; l'entrée
  reste bloquée jusqu'à l'ajout d'une vérification venue dédiée.
- Aucun secret Hyperliquid n'a été configuré et aucune connexion à la venue n'a été effectuée.
- Le statut juridique et les conditions Hyperliquid doivent être revérifiés avant le mainnet.
