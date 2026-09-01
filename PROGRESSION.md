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

- 40 tests déterministes et d'intégration réussis.
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
- Entrée short par notional fixe après warm-up, si `enable_order_submission` est vrai et si un
  reçu récent de vérification venue correspond exactement au compte, réseau, instrument, mode de
  marge et levier configurés.
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

### Revue adversariale AGORA — jalon 2

- Revue réelle exécutée dans `/home/andrei/Projects/61_AGORA`.
- Expérience canonique : `AGO-EXP-2026-0026`.
- Verdict : `NUANCED`, confiance `0.68`.
- Rapport, dossier soumis et contrat de tests préenregistré archivés dans `docs/reviews/`.
- Le runner testnet demeure bloqué; la revue autorise uniquement la poursuite du développement
  sous conditions vérifiables.

Corrections issues de la revue :

- retry borné lorsque le fill arrive avant visibilité de la position dans le cache;
- `RECOVERY_REQUIRED` après échec synchrone de soumission de l'entrée;
- watchdog armé avant soumission ou modification du trigger;
- `EMERGENCY_EXIT` persistant après échec synchrone de protection;
- construction explicite et journalisation de l'ordre de sortie;
- suppression du booléen `venue_margin_verified` au profit d'un reçu structuré, lié au compte et
  limité dans le temps;
- conflit explicite si les ordres protecteurs réels ne correspondent pas uniquement à l'identité
  journalisée;
- trace d'audit JSONL versionnée et déterministe;
- test d'intégration avec le vrai `BacktestEngine` Nautilus 1.231.0.

Le test moteur démontre que le cache position est mis à jour avant `on_order_filled` dans le
backtest déterministe. Cette preuve ne s'étend pas au moteur live.

### Configuration locale des identités Hyperliquid

- Création d'un `.env` local ignoré par Git, permissions Unix `600`.
- Séparation explicite entre adresse du compte principal, adresse publique de l'agent et clé
  privée de l'agent.
- Le compte MetaMask principal est confirmé comme
  `0x4c017d1f234F331ba4cc0ad6A356fa325c252299`; cette adresse alimente désormais
  `HYPERLIQUID_ACCOUNT_ADDRESS` dans le `.env` local.
- Vérification directe de l'API mainnet : compte vide (`accountValue = 0`) et aucun agent actif
  (`extraAgents = []`) pour cette adresse.
- La tentative `ApproveAgent` qui réutilisait cette même adresse comme agent `Nautilus` est donc
  classée comme non enregistrée et ne fait plus partie de la configuration active.
- L'adresse `0x31d4bec9c5194177096fabb278d781327579459d`, issue d'une tentative précédente
  d'`ApproveAgent`, est retirée de la configuration active.
- Les clés privées testnet et mainnet restent vides.
- Verrou `HLTRADER_MAINNET_ENABLED=false` ajouté au modèle de configuration.
- `.env.example` enrichi sans valeur propre à l'opérateur.

L'adresse contient 40 caractères hexadécimaux après `0x` : c'est une adresse publique, pas une clé
privée. Un futur agent doit posséder une adresse distincte et sa clé privée doit rester secrète.

Le payload final `ApproveAgent` confirme `Nautilus valid_until 1803791713367`, soit une expiration
le 28 février 2027 à 05:15:13 UTC. La durée entre le nonce d'approbation et l'expiration est
d'environ 180 jours. Ces horodatages publics sont conservés dans le `.env` local pour audit.

Le dépôt d'activation doit créditer le compte principal Hyperliquid contrôlé par MetaMask. Une fois
le compte activé, un nouvel API wallet distinct devra être créé pour le bot. L'agent servira à
signer; le compte principal restera la source de vérité pour soldes, positions, ordres et événements.

Un guide opérateur complet est disponible dans `docs/wallet-setup.md`. Il explique comment copier
`HYPERLIQUID_ACCOUNT_ADDRESS` depuis les détails du compte MetaMask ayant signé `ApproveAgent`,
comment vérifier l'agent sur Hyperliquid et comment distinguer adresse publique, clé privée et
signature.

### Procédure KBM de dépôt et création d'agent

- Article `KB-HOME-HYPERLIQUID-001` ajouté dans KBM 2.0 sous `Corpus > HOME > Hyperliquid`.
- Dépôt opératoire fixé à 10 USDC natifs via Arbitrum, au-dessus du minimum officiel de 5 USDC.
- Contrôles documentés : compte MetaMask, réseau, token, ETH de gas, crédit API et absence de
  second dépôt tant que le premier n'est pas résolu.
- Création de l'agent `Nautilus` placée après confirmation d'un `accountValue` positif.
- Adresse d'agent obligatoirement distincte et secrets exclus de KBM et de Git.
- `HLTRADER_MAINNET_ENABLED=false` demeure obligatoire après cette activation administrative.

### Présentation HNT dans KBM

- Article `KB-HOME-HYPERLIQUID-002` ajouté sous `Corpus > HOME > Hyperliquid`.
- Vue d'ensemble documentée : vision, MVP, architecture, sorties, machine à états, limites de
  `normalTpsl`, garde-fous, état courant et prochaines étapes.
- Le document indique explicitement qu'aucun ordre testnet ou mainnet n'a encore été soumis et
  que les runners demeurent bloqués.

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
- La branche `main` est publiée et suit `origin/main`.

La clé SSH choisie par défaut sur cette machine reste celle de `symbioticode`. Les opérations Git
sur ce dépôt devront donc employer explicitement `id_ed25519_dravitch`, ou une entrée dédiée dans
`~/.ssh/config` devra être ajoutée.

## Prochaines étapes

1. Ajouter le runner de vérification directe du mode de marge/levier côté Hyperliquid qui produit
   le reçu structuré exigé par l'adapter.
2. Ajouter des tests d'intégration Nautilus avec événements de fills successifs et timeout/rejet
   concurrents.
3. Implémenter le runner de backtest et vérifier la sémantique mark-price du trigger.
4. Avant tout testnet, confirmer la direction du seuil de 60 000 USD, le notional de 300 USDC et
   le mode de marge isolée.

## Décisions et blocages ouverts

- Direction du seuil de prix : `above` est configuré provisoirement, mais doit être confirmé.
- Notional : 300 USDC est une valeur provisoire à confirmer.
- NautilusTrader est installé et épinglé, mais le runner live/testnet n'est pas encore câblé.
- L'API standard de stratégie ne prouve pas le mode de marge et le levier Hyperliquid. Le booléen
  de confiance a été supprimé; l'entrée exige un reçu structuré que seul le futur vérificateur
  venue pourra produire.
- Verdict AGORA `NUANCED` : aucune autorisation de runner testnet tant que les risques blocants du
  rapport de revue ne sont pas clos.
- Aucun secret Hyperliquid n'a été configuré et aucune connexion à la venue n'a été effectuée.
- Le statut juridique et les conditions Hyperliquid doivent être revérifiés avant le mainnet.
