# ADR 0002 — Hyperliquid `normalTpsl` avec NautilusTrader 1.231.0

Date : 1 septembre 2026

## Décision

Épingler `nautilus_trader==1.231.0` et considérer `normalTpsl` comme **non atomique** dans le
chemin d'exécution Nautilus utilisé par le MVP.

`ShortBtcRsiStrategy` soumet donc l'entrée, puis crée ou redimensionne un trigger natif
reduce-only après chaque fill. La stratégie demeure en `PROTECTING` jusqu'à ce que la quantité
acceptée du trigger égale l'exposition short nette constatée. Un rejet ou un timeout déclenche
`EMERGENCY_EXIT`.

## Vérification du tag officiel

Le code du tag `v1.231.0` :

- reconnaît une liste bracket OTO + enfants reduce-only comme `NormalTpsl` ;
- retire cependant les enfants de la première soumission ;
- envoie l'entrée seule avec le grouping Hyperliquid `na` ;
- conserve les enfants dans `staged_brackets` ;
- active leur soumission seulement après réception d'un fill du parent.

Le test officiel
`test_submit_order_list_normal_tpsl_stages_children_until_parent_fill` vérifie précisément que
la première action envoyée contient uniquement le parent et utilise `grouping: na`.

## Conséquences

- Une fenêtre d'exposition non protégée existe entre le fill et l'acceptation du trigger.
- Le nom interne `NormalTpsl` ne doit pas être interprété comme une transaction atomique venue.
- Le bracket générique Nautilus n'est pas utilisé pour ce MVP à sortie RSI logicielle + un seul
  trigger prix natif.
- Chaque fill successif force une convergence de la protection vers la position nette totale.
- La stratégie reste désactivée par défaut et exige une preuve distincte de la marge/du levier.

## Réévaluation

Cette décision doit être revue avant toute montée de version NautilusTrader. Une future version
pourrait envoyer réellement `grouping: normalTpsl` ou modifier la politique de staging.
