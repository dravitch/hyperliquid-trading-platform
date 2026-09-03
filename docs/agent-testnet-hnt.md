---
id: KB-HOME-HYPERLIQUID-003
type: KB
project: Hyperliquid-Nautilus-Trading
status: ACTIF
date: 2026-09-02
audience: [HOME]
scope: hyperliquid-testnet
maturity: EN_COURS
source_of_truth: documentation officielle Hyperliquid et dépôt dravitch/hyperliquid-trading-platform
nav_title: Agent testnet HNT
section: [Corpus, HOME, Hyperliquid]
tags: [operations, securite, documentation]
---

# KB-HOME-HYPERLIQUID-003 – Créer et autoriser l’API wallet HNT sur testnet

## But et limite

Cette procédure prépare un **API wallet**, aussi appelé **agent wallet**, distinct pour HNT sur
**Hyperliquid testnet uniquement**. Elle s’arrête après l’autorisation de l’agent, la configuration
de son adresse publique et le dry-run local du spike `updateLeverage`.

Elle ne demande pas la clé privée au runner, ne signe aucune mutation, ne modifie pas le levier,
ne soumet aucun ordre et n’active pas le mainnet.

## Trois notions à ne pas confondre

### Master account

Le master est le compte économique auquel appartiennent les soldes, positions, ordres et l’état
du compte. Son adresse publique doit alimenter :

```bash
HYPERLIQUID_ACCOUNT_ADDRESS=0x...
```

La documentation officielle avertit qu’une interrogation des données de compte avec l’adresse de
l’agent renvoie un résultat vide : les lectures économiques doivent employer l’adresse réelle du
master ou du sous-compte.

### API wallet / agent

L’agent est un wallet distinct que le master autorise à signer certaines actions en son nom. Son
adresse publique doit être différente de celle du master et alimente :

```bash
HYPERLIQUID_AGENT_ADDRESS=0x...
```

Sa clé privée pourra ultérieurement alimenter localement `HYPERLIQUID_TESTNET_PK`, lors d’un
mandat séparé et explicitement autorisé. Elle n’est ni nécessaire ni permise pour le dry-run de
ce document.

### Secret

La clé privée de l’agent est un secret. Elle **NE DOIT JAMAIS** être copiée dans :

- Git ;
- KBM ;
- une issue ou une PR GitHub ;
- des logs ;
- une conversation ;
- une capture d’écran publique.

La conserver dans un gestionnaire de secrets local ou, provisoirement, dans un fichier local
restreint et ignoré par Git. Ne jamais enregistrer sa valeur dans un article KBM. Ne pas utiliser
la clé privée du master comme clé du bot.

## Procédure opérateur testnet

### 1. Satisfaire le prérequis mainnet du faucet

La documentation officielle du faucet impose un dépôt mainnet préalable avec la **même adresse**
avant de permettre la réception des fonds testnet. L’observation actuelle
`accountValue = 0` du master mainnet ne permet donc pas encore de considérer ce prérequis rempli.

Suivre [KB-HOME-HYPERLIQUID-001](depot-et-creation-agent.md) pour la procédure opérateur de dépôt
mainnet. Ne pas la dupliquer ici. Ce dépôt sert uniquement à l’activation du master et à
l’éligibilité au faucet testnet : il n’autorise aucun runtime de trading mainnet, aucun ordre
mainnet et aucun changement de `HLTRADER_MAINNET_ENABLED=false`.

Vérifier que le master utilisé sur testnet est la même adresse que celle ayant satisfait le
prérequis mainnet avant de continuer.

### 2. Créditer effectivement le compte testnet

1. Ouvrir le faucet officiel : `https://app.hyperliquid-testnet.xyz/drip`.
2. Connecter le master dont le prérequis mainnet vient d’être vérifié.
3. Demander les fonds testnet, puis attendre leur disponibilité effective dans le compte testnet.
4. Ne pas créer l’agent tant que le compte testnet n’est pas effectivement disponible/crédité.

Pour un login par courriel, la documentation prévient que Privy peut produire des adresses
différentes entre mainnet et testnet. Une adresse différente ne satisfait pas la condition
« même adresse » et doit être résolue avant de poursuivre.

### 3. Accéder au bon environnement

1. Ouvrir directement l’application officielle testnet :
   `https://app.hyperliquid-testnet.xyz/`.
2. Vérifier visuellement que l’environnement affiché est bien **testnet** avant toute signature.
3. Connecter le wallet destiné à être le master testnet.
4. Conserver `HLTRADER_MAINNET_ENABLED=false`.

La documentation officielle confirme l’hôte testnet via son faucet
`https://app.hyperliquid-testnet.xyz/drip`. Il faut relever l’adresse effectivement connectée au
testnet, sans la déduire par hypothèse de l’identité mainnet.

### 4. Identifier le master

1. Copier l’adresse publique du wallet actuellement connecté dans l’application testnet.
2. La comparer à l’adresse affichée par le wallet navigateur, caractère par caractère.
3. La noter localement comme `HYPERLIQUID_ACCOUNT_ADDRESS`.
4. Ne pas utiliser comme master l’adresse d’un agent existant.

L’exemple officiel `basic_agent.py` exige que l’adresse du wallet qui approuve soit égale à
l’adresse du compte. Sinon, l’agent serait autorisé pour le mauvais propriétaire et les actions
ultérieures échoueraient.

### 5. Créer un agent HNT distinct dans l’UI

Le SDK officiel confirme qu’un agent nommé peut être créé avec le frontend et que l’UI officielle
mainnet propose une page API. La documentation consultée ne garantit toutefois ni l’URL testnet de
cette page, ni les libellés actuels des menus et boutons.

**À confirmer dans l’UI actuelle** : le chemin exact vers la gestion des API wallets, le texte du
bouton de création et le libellé du bouton d’autorisation sur testnet.

Dans la zone de gestion des API wallets du testnet :

1. choisir la création d’un **nouvel** API wallet / agent ;
2. lui donner un nom dédié et non réutilisé, par exemple `HNT-Nautilus-testnet` ;
3. générer une nouvelle identité d’agent ;
4. relever séparément son adresse publique et sa clé privée ;
5. vérifier que l’adresse publique de l’agent diffère du master ;
6. interrompre la procédure si les deux adresses sont identiques ou si la clé privée n’est plus
   récupérable de manière sûre.

Hyperliquid recommande un agent distinct par processus et déconseille de réutiliser l’adresse
d’un agent désenregistré, car son état de nonce peut être élagué.

### 6. Autoriser l’agent pour le master

1. Toujours depuis l’application **testnet**, demander l’autorisation du nouvel agent.
2. Vérifier dans la demande de signature que l’environnement est `Testnet`, que
   `agentAddress` est exactement la nouvelle adresse et que le nom correspond.
3. Signer l’action d’autorisation avec le **master**, jamais avec l’agent.
4. Attendre une confirmation explicite de l’application et ne pas répéter aveuglément la
   signature en cas d’issue inconnue.

Au niveau API, l’action officielle est `approveAgent`; sur testnet son champ
`hyperliquidChain` vaut `Testnet`. Une réponse API réussie a le statut `ok`. Cette description
explique ce que l’UI doit faire : elle ne constitue pas une invitation à copier la clé privée du
master dans un script.

### 7. Identifier et ranger les trois valeurs

| Valeur | Origine | Destination autorisée maintenant |
|---|---|---|
| Adresse publique master | wallet connecté au testnet | `HYPERLIQUID_ACCOUNT_ADDRESS` dans `.env` local |
| Adresse publique agent | API wallet nouvellement créé | `HYPERLIQUID_AGENT_ADDRESS` dans `.env` local |
| Clé privée agent | affichage sécurisé lors de la création | gestionnaire de secrets local, jamais Git/KBM |

Pour ce mandat, ajouter **uniquement l’adresse publique** de l’agent au `.env` local :

```bash
HYPERLIQUID_AGENT_ADDRESS=0x...
```

Ne pas ajouter `HYPERLIQUID_TESTNET_PK`. Vérifier que `.env` est ignoré par Git et limiter ses
permissions si ce fichier contient déjà d’autres secrets locaux.

### 8. Vérifier publiquement le rôle de l’agent

L’endpoint public `userRole` est documenté et peut confirmer que l’adresse est reconnue comme
`agent` sur le testnet :

```bash
curl -sS https://api.hyperliquid-testnet.xyz/info \
  -H 'Content-Type: application/json' \
  --data '{"type":"userRole","user":"0x_ADRESSE_PUBLIQUE_AGENT"}'
```

La documentation officielle définit explicitement la réponse d’un agent :

```json
{"role":"agent","data":{"user":"0x_ADRESSE_PUBLIQUE_MASTER"}}
```

Le champ stable et documenté `data.user` doit correspondre exactement à
`HYPERLIQUID_ACCOUNT_ADDRESS`. Il constitue le lien public entre l’agent interrogé et son master.

**À confirmer dans l’UI actuelle** : l’affichage explicite du lien entre cet agent nommé et le
master testnet.

Une réponse `missing`, un format inattendu, un autre rôle ou un `data.user` différent du master
attendu n’est pas une preuve d’autorisation. Ne pas poursuivre dans ce cas. L’appel `userRole` est
public, mais coûte un poids API élevé ; ne pas le lancer en boucle.

## Validation locale avant dry-run

Charger les variables du `.env` dans le shell courant sans afficher leur valeur :

```bash
set -a
source .env
set +a

test -n "$HYPERLIQUID_ACCOUNT_ADDRESS" \
  && echo "account: set" \
  || echo "account: MISSING"

test -n "$HYPERLIQUID_AGENT_ADDRESS" \
  && echo "agent: set" \
  || echo "agent: MISSING"
```

Résultat exigé avant continuation :

```text
account: set
agent: set
```

Puis seulement lancer le dry-run :

```bash
uv run --with hyperliquid-python-sdk==0.24.0 \
  hnt-update-leverage-spike
```

Ne pas ajouter `--execute`. Ne pas définir
`HLTRADER_ALLOW_TESTNET_MARGIN_MUTATION=true`.

Ce dry-run peut interroger les métadonnées publiques du testnet, résoudre l’asset index de BTC,
construire le plan `updateLeverage` et écrire le rapport local :

```text
artifacts/testnet/update-leverage-spike.json
```

Il ne signe rien, ne change pas le levier et ne soumet aucun ordre.

## Checklist opérateur

- [ ] Le prérequis mainnet du faucet est satisfait avec le même master
- [ ] `HLTRADER_MAINNET_ENABLED=false` et aucun trading mainnet n’est autorisé
- [ ] Le compte testnet est effectivement crédité/disponible
- [ ] Je suis bien sur Hyperliquid testnet
- [ ] J’ai identifié le master account correct
- [ ] L’agent/API wallet possède une adresse différente du master
- [ ] L’agent est autorisé pour ce master
- [ ] J’ai copié uniquement son adresse publique
- [ ] La clé privée n’est dans aucun document ou dépôt
- [ ] `HYPERLIQUID_AGENT_ADDRESS` est renseigné localement
- [ ] `HYPERLIQUID_TESTNET_PK` reste vide pour le dry-run
- [ ] `HLTRADER_MAINNET_ENABLED=false`

## Sources officielles

- [API wallets et nonces](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)
- [Exchange endpoint et action approveAgent](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
- [Info endpoint et requête userRole](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Endpoint API testnet](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)
- [Faucet et particularités d’identité testnet](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet)
- [SDK Python officiel](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- [Exemple officiel basic_agent.py](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_agent.py)
