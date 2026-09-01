# Configuration des portefeuilles Hyperliquid

Dernière mise à jour : 1 septembre 2026

## Les deux identités obligatoires

Le bot utilise deux identités différentes :

| Rôle | Variable | Nature | Usage |
|---|---|---|---|
| Compte principal | `HYPERLIQUID_ACCOUNT_ADDRESS` | Adresse publique | Soldes, positions, ordres ouverts et événements utilisateur |
| Agent `Nautilus` | `HYPERLIQUID_MAINNET_PK` | Clé privée secrète | Signature des ordres au nom du compte principal |

L'adresse publique de l'agent est conservée séparément dans
`HYPERLIQUID_AGENT_ADDRESS`. Elle sert à vérifier qu'une clé privée correspond bien à l'agent
autorisé; elle ne remplace jamais l'adresse du compte principal.

## Où trouver `HYPERLIQUID_ACCOUNT_ADDRESS`

Il s'agit de l'adresse publique du compte MetaMask qui était sélectionné au moment de signer le
message EIP-712 `ApproveAgent` pour l'agent `Nautilus`.

Dans MetaMask :

1. Ouvrir l'extension MetaMask utilisée pour se connecter à `https://app.hyperliquid.xyz`.
2. Vérifier que le compte qui a signé `ApproveAgent` est sélectionné.
3. Ouvrir le menu du compte, puis **Détails du compte**.
4. Utiliser **Copier l'adresse**.
5. Vérifier que la valeur commence par `0x` et contient 40 caractères hexadécimaux après `0x`.
6. Coller cette adresse dans le `.env` local :

   ```text
   HYPERLIQUID_ACCOUNT_ADDRESS=0x...
   ```

Changer de réseau dans MetaMask ne change normalement pas l'adresse d'un compte EOA : la même
adresse publique apparaît sur Ethereum, Arbitrum et les autres réseaux EVM. Ce qui importe est le
compte sélectionné ayant effectivement signé l'autorisation.

## Vérification croisée recommandée

Avant d'activer un runner :

1. Se connecter au site officiel Hyperliquid avec ce même compte MetaMask.
2. Ouvrir la page Portfolio/API et confirmer que l'agent nommé `Nautilus` apparaît.
3. Confirmer que l'adresse de l'agent affichée est :

   ```text
   0x4c017d1f234f331ba4cc0ad6a356fa325c252299
   ```

4. Confirmer que les soldes et positions visibles appartiennent bien au compte principal copié.
5. Laisser le bot désactivé si l'agent n'apparaît pas ou si le compte affiché diffère.

## Métadonnées de l'agent actuel

```text
Nom                 Nautilus
Adresse publique    0x4c017d1f234f331ba4cc0ad6a356fa325c252299
Approbation          2026-09-01 05:14:54 UTC
Expiration           2027-02-28 05:15:13 UTC
Durée                environ 180 jours
```

Le dépôt d'activation doit créditer le compte principal Hyperliquid connecté avec MetaMask,
jamais l'adresse publique de l'agent.

## Où trouver `HYPERLIQUID_MAINNET_PK`

Cette variable reçoit exclusivement la clé privée générée lors de la création de l'API wallet
`Nautilus`. Elle doit correspondre à l'adresse publique de l'agent ci-dessus.

Ne jamais y placer :

- la clé privée ou la phrase de récupération MetaMask;
- l'adresse publique `0x4c...2299`;
- une signature EIP-712;
- la clé d'un agent précédent ou expiré.

Une adresse publique contient `0x` suivi de 40 caractères hexadécimaux. Une clé privée EVM
contient habituellement `0x` suivi de 64 caractères hexadécimaux. Ne jamais publier cette dernière,
la copier dans un ticket, un chat, un commit ou une capture d'écran.

## État actuel du `.env`

- L'adresse et les dates publiques de l'agent sont documentées.
- `HYPERLIQUID_ACCOUNT_ADDRESS` reste vide jusqu'à copie de l'adresse MetaMask principale.
- `HYPERLIQUID_MAINNET_PK` reste vide jusqu'à récupération sécurisée de la clé privée de l'agent.
- `HLTRADER_MAINNET_ENABLED=false` reste obligatoire.
- Le fichier `.env` est ignoré par Git et protégé avec les permissions Unix `600`.

## Rotation et expiration

Lorsqu'un agent expire ou est remplacé :

1. arrêter le runner;
2. ne jamais réutiliser l'ancienne adresse/clé d'agent;
3. créer un nouvel agent;
4. mettre à jour l'adresse publique et la clé privée locales;
5. refaire les vérifications de correspondance et la recette testnet;
6. révoquer l'ancien agent si nécessaire.

Hyperliquid suit les nonces par signataire. Réutiliser une adresse d'agent désenregistrée peut
introduire des risques de rejeu après élagage de son état de nonce.
