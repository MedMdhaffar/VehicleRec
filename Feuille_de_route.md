# Feuille de route — Plateforme d'analyse vidéo des véhicules

**Date : 15 juin 2026**
**Objet : visibilité et suivi du développement des modules d'analyse véhicule.**

---

## Objectif

Développer une plateforme de modules d'analyse vidéo dédiés aux véhicules. Chaque module est **indépendant, réutilisable et accessible par API** : il peut être utilisé directement par nos applications ou piloté par l'agent IA (orchestrateur). Tous les modules s'appuient sur une **base commune de détection des véhicules**, ce qui évite de redévelopper la même brique plusieurs fois et garantit des résultats cohérents.

---

## Principe de fonctionnement

- Chaque module reçoit une image (ou un flux vidéo) et renvoie **une image annotée + un rapport de données structuré**.
- Deux modes d'utilisation :
  1. **À la demande** — une application (ou l'agent IA) interroge le module et reçoit immédiatement le résultat.
  2. **Automatique** — en surveillance continue, le module envoie automatiquement ses alertes et résultats à l'application concernée.
- Les modules ne sont liés à aucune application précise : ils sont conçus pour être consommés par n'importe quel système, présent ou futur.

---

## Les modules

| Module | Rôle | Ce qu'il fournit |
|--------|------|------------------|
| **1. Classification des véhicules** | Identifier chaque véhicule | Type (voiture, camion, bus, van, moto, vélo) **et couleur** |
| **2. Reconnaissance de plaque** | Lire l'immatriculation | Numéro de plaque de chaque véhicule |
| **3. Analyse du trafic** | Surveiller la circulation et le stationnement | 3 fonctions (ci-dessous) |

**Le module 3 « Analyse du trafic » regroupe trois fonctions :**
- **Comptage directionnel** — compter les véhicules qui franchissent une ligne, en distinguant le sens (venant de droite / venant de gauche), par type de véhicule.
- **Durée de stationnement** — mesurer combien de temps un véhicule reste stationné dans une zone surveillée.
- **Stationnement interdit** — détecter tout véhicule stationné dans une zone interdite et générer une alerte.

---

## Ordre de livraison — priorité aux gains rapides

Conformément à la consigne (privilégier les gains rapides), les modules les plus simples et les plus rapides à livrer passent en premier :

1. **Classification des véhicules** *(gain rapide n°1)*
2. **Reconnaissance de plaque** *(gain rapide n°2)*
3. **Analyse du trafic** — module plus complexe (suivi des véhicules en temps réel sur le flux vidéo), livré en dernier.

---

## Étapes de réalisation

**Phase A — Classification des véhicules**
- Mise en place de la base technique commune (détection des véhicules).
- Entraînement du modèle de reconnaissance des types de véhicules, à partir de **données réelles de rue couvrant les 6 types (y compris les vans)**, puis testé sur un **second jeu de données indépendant** pour vérifier la fiabilité.
- Mise en service du module : type + couleur.

**Phase B — Reconnaissance de plaque**
- Détection et lecture des plaques d'immatriculation.
- *Préalable : confirmer le format de plaque visé (voir point d'attention).*

**Phase C — Analyse du trafic**
- Comptage directionnel.
- Durée de stationnement.
- Stationnement interdit.

**Phase D — Intégration et mise en production**
- Connexion aux applications et à l'agent IA.
- Fiabilisation, documentation et validation sur des images réelles du terrain.

---

## Point d'attention

Le **format des plaques** (par exemple les plaques tunisiennes, avec inscription en arabe) doit être confirmé **avant la Phase B**. Ce point est déterminant : il décide si une solution existante suffit, ou s'il faut entraîner un modèle spécifique sur des plaques locales (ce qui demande de collecter des images de plaques du terrain).

---

## État actuel

**Conception validée.** Démarrage de la **Phase A** : base technique commune + modèle de classification des véhicules.
