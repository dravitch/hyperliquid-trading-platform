# ADR 0003 — Lifecycle concurrent de protection et flatten

Date : 1 septembre 2026

## Écarts observés

L'audit précédant les tests du jalon a identifié quatre écarts dans l'orchestration existante :

- un fill d'entrée reçu pendant `EMERGENCY_EXIT` tentait encore de converger vers une protection;
- une acceptation tardive après timeout pouvait remplacer l'état d'urgence par `STATE_CONFLICT`;
- `PositionClosed` permettait `CLOSED_FINAL` sans relire la position nette réelle;
- des événements dupliqués pouvaient demander plusieurs fois le même redimensionnement pending.

## Décision

- `EMERGENCY_EXIT` est absorbant pour les confirmations, timeouts et rejets protecteurs tardifs.
- Un fill d'entrée tardif met à jour l'exposition d'urgence et relance la convergence vers zéro.
- La quantité de flatten est la différence entre l'exposition nette observée et les quantités de
  flatten déjà demandées mais non remplies.
- `CLOSED_FINAL` exige une observation explicite d'exposition nulle.
- La quantité de protection demandée est mémorisée afin d'éviter un amendement identique tant que
  sa confirmation est pending.

Ces règles ne modifient pas la stratégie financière REV3.1. Elles rendent falsifiable son
invariant de protection et de fermeture face aux événements concurrents ou dupliqués.
