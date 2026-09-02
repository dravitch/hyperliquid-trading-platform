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

- 120 tests déterministes et d'intégration réussis.
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

La règle de déclenchement venue est désormais classée `DOCUMENTED_CONFIRMED` : les documentations
officielles Hyperliquid et NautilusTrader indiquent que les TP/SL et ordres conditionnels natifs
sont évalués contre le mark price. Deux preuves restent distinctement ouvertes : la fidélité de
notre `BacktestEngine` à cette sémantique et le lifecycle réel Nautilus/Hyperliquid sur testnet.

Le futur venue verifier public adoptera un reçu ternaire `VERIFIED`, `MISMATCH` ou
`UNVERIFIABLE`. L'absence de position BTC ne sera jamais interprétée comme une preuve de conformité
du levier ou du mode de marge. Les deux derniers résultats bloqueront l'entrée.

### Runner BacktestEngine — sémantique mark price

- Contrat explicite ajouté : `MARK_PRICE_EQUIVALENT_CONFIRMED`, `PROXY_USED` ou `UNVERIFIABLE`.
- Probe déterministe exécuté avec un quote restant sous le seuil et un `MarkPriceUpdate` le
  franchissant seul.
- NautilusTrader 1.231.0 transmet bien le mark price à la stratégie, mais le matching simulé ne
  déclenche pas le `STOP_MARKET` natif dans ce scénario.
- Verdict courant : `UNVERIFIABLE`; aucune équivalence venue n'est revendiquée.
- Un futur pont mark-price vers le matching sera obligatoirement déclaré `PROXY_USED`.
- Commande reproductible : `uv run hnt-backtest-probe`, avec rapport JSON atomique dans
  `artifacts/backtests/mark-price-semantics.json`.
- Quatre tests couvrent les trois verdicts et le comportement réel du moteur épinglé.

### Lifecycle concurrent de protection — jalon 3

- Fill #1 protégé puis fill #2 : retour immédiat `OPEN -> PROTECTING`, avec protection attendue
  calculée sur l'exposition cumulative.
- Fill #2 pendant acceptation pending : une acceptation ne couvrant que fill #1 ne peut pas ouvrir
  la stratégie; un nouveau resize reste requis.
- Courses acceptation/timeout : un seul état absorbant gagne, sans coexistence logique entre
  `OPEN` et `EMERGENCY_EXIT` et sans double flatten.
- Rejet pendant resize : passage à `EMERGENCY_EXIT`, conservation de la quantité sous-protégée et
  journalisation de la cause initiale malgré les rejets dupliqués.
- Fill tardif pendant `EMERGENCY_EXIT` : exposition relue et shortfall de flatten recalculé comme
  `actual_net_position_qty - outstanding_flatten_qty`.
- Un `PositionClosed` ne peut plus produire `CLOSED_FINAL` tant que l'exposition nette relue n'est
  pas nulle.
- Événements dupliqués : resize pending identique supprimé, rejet idempotent, clôture finale
  idempotente et aucun double flatten pour une exposition déjà couverte par un ordre pending.
- Les 24 permutations de fill, acceptation, timeout et rejet conservent l'invariant : jamais
  `OPEN` lorsque `protected_qty < actual_net_position_qty`.
- ADR 0003 ajouté pour documenter les quatre écarts architecturaux découverts avant correction.

### Restart et réconciliation venue-first — jalon 4

- L'intention `EXITING`, `EMERGENCY_EXIT` ou `RECOVERY_REQUIRED` survit désormais au restart au
  lieu d'être réduite implicitement à `OPEN` ou `PROTECTING`.
- Le journal persiste tous les identifiants d'ordres de sortie du run, avec migration transparente
  de l'ancien champ unique `exit_order`; aucune quantité économique persistée n'est crue au
  restart.
- L'outstanding flatten est reconstruit depuis les ordres BUY reduce-only réellement ouverts et
  leur `leaves_qty`, uniquement lorsque leur identité appartient sans ambiguïté au run.
- Le shortfall est calculé comme exposition short réelle moins quantité de sortie ouverte
  reconnue. Un ordre manquant reprend automatiquement exactement ce shortfall lorsqu'une intention
  `EXITING` ou `EMERGENCY_EXIT` est déjà journalisée.
- Tout ordre reduce-only étranger, toute identité ambiguë et tout outstanding supérieur à
  l'exposition produit `STATE_CONFLICT` sans nouvelle soumission.
- Une protection exacte peut reconstruire `OPEN`; une protection partielle reste `PROTECTING` et
  reprend la convergence; un trigger journalisé absent échoue fermé.
- Une exposition nulle avec une fermeture journalisée et aucune sortie pertinente ouverte produit
  et persiste `CLOSED_FINAL`; `RECOVERY_REQUIRED` n'est pas effacé sur la seule foi d'une position
  nulle.
- Deux restaurations successives de la même instance sont idempotentes.
- ADR 0004 documente la divergence initiale et la politique de reprise automatique limitée aux
  intentions de fermeture déjà persistées.
- Dix-neuf nouveaux tests couvrent pending/partial/missing flatten, EXITING, protection exacte ou
  partielle, trigger absent, ordres ambigus, journal stale, clôture économique et migration du
  journal.

### Venue verifier ternaire — jalon 5

- Verifier public read-only séparé en transport `clearinghouseState`, parsing strict des preuves,
  classification pure et sérialisation du reçu consommable par la stratégie.
- Statuts explicites `VERIFIED`, `MISMATCH` et `UNVERIFIABLE`; seul `VERIFIED` satisfait le garde
  d'entrée existant de `ShortBtcRsiStrategy`.
- Preuves utilisées uniquement lorsqu'une position unique expose `coin`, `szi`,
  `leverage.type` et `leverage.value`; les nombres non finis, non positifs ou un levier non entier
  échouent fermés.
- Absence de position, champ manquant, réponse ambiguë, parsing invalide, erreur réseau, preuve
  stale ou future : `UNVERIFIABLE`, jamais succès implicite.
- Mode de marge ou levier observable différent, ainsi que contexte compte/environnement/instrument
  différent : `MISMATCH`.
- Le reçu lie valeurs attendues et observées, quantité de position, source, horodatage et raison;
  son aller-retour JSON conserve le contrat consommé par `_margin_is_verified()`.
- ADR 0005 documente la limite réelle de l'API : l'adresse et l'environnement sont liés à la
  requête mais non répétés dans la réponse, et aucun réglage préalable n'est observable sans
  position pertinente.
- 22 nouveaux tests couvrent les classifications, parsing, erreurs transport, fraîcheur,
  déterminisme, contexte et consommation du reçu.

### Bootstrap marge/levier one-shot — jalon 6

- Spike documentaire réalisé sur l'endpoint `exchange`, le SDK Python officiel et l'adapter
  NautilusTrader : l'action exacte est `updateLeverage` avec `asset`, `isCross` et un `leverage`
  entier.
- La réponse positive exacte `status=ok` / `response.type=default` arrive, selon la documentation
  Hyperliquid, après inclusion dans un bloc L1 committé et exécution de l'action. Elle prouve
  l'acceptation de la commande, pas un état pré-position relisible.
- Contrat séparé `BootstrapMarginReceipt` avec `CONFIGURED`, `MISMATCH` et `UNVERIFIABLE`; le sens
  du reçu public `MarginVerificationReceipt` reste inchangé.
- Binding exact du compte master, signer agent, environnement, session de processus,
  instrument/coin, index d'asset, mode cross/isolated, levier, nonce et type de réponse.
- Consommation atomique one-shot sous verrou de fichier pour un `client_order_id` précis,
  immédiatement avant la persistance de `ENTERING`; deux consommations concurrentes ne peuvent
  pas gagner.
- Politique restart fail-closed : une nouvelle session ne peut consommer un reçu créé pour la
  session précédente, même s'il n'avait pas encore été utilisé. Un reçu consommé n'est jamais
  réutilisable.
- Timeout, perte réseau ou réponse malformée restent `UNVERIFIABLE`; aucun retry automatique de
  mutation n'est introduit.
- L'intégration minimale du garde d'entrée accepte soit la preuve publique existante, soit la
  consommation réussie d'un bootstrap receipt. Les valeurs par défaut restent entièrement
  désactivées.
- ADR 0006 documente signature agent/master, sémantique de réponse, limites de relecture et
  politique de crash/restart.
- 22 nouveaux tests couvrent payload officiel, succès exact, mauvais mode/levier/contexte,
  rejet, ambiguïté réseau, réponse malformée, fraîcheur, concurrence, one-shot et restart.

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

1. Réaliser un spike testnet contrôlé de la seule mutation `updateLeverage` sur compte plat afin
   de confirmer empiriquement son acceptation par l'agent et son comportement pré-position.
2. Câbler ensuite un runner testnet fail-closed, maintenu désactivé sans agent et sans secrets.
3. Avant tout testnet, confirmer la direction du seuil de 60 000 USD, le notional de 300 USDC et
   le mode de marge isolée.

## Décisions et blocages ouverts

- Direction du seuil de prix : `above` est configuré provisoirement, mais doit être confirmé.
- Notional : 300 USDC est une valeur provisoire à confirmer.
- NautilusTrader est installé et épinglé, mais le runner live/testnet n'est pas encore câblé.
- L'API standard de stratégie ne prouve pas le mode de marge et le levier Hyperliquid. Le booléen
  de confiance a été supprimé. Le verifier public produit un reçu structuré, mais ne peut le
  classer `VERIFIED` qu'en présence d'une position qui expose réellement ces deux valeurs. Le
  bootstrap séparé peut autoriser une première entrée uniquement après réponse L1 positive exacte;
  la génération signée de ce reçu reste désactivée jusqu'au spike testnet contrôlé.
- Verdict AGORA `NUANCED` : aucune autorisation de runner testnet tant que les risques blocants du
  rapport de revue ne sont pas clos.
- Aucun secret Hyperliquid n'a été configuré et aucune connexion à la venue n'a été effectuée.
- Le statut juridique et les conditions Hyperliquid doivent être revérifiés avant le mainnet.
