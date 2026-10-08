# unstrikeable — design

> *The company that never goes on strike.*

Statut : **brouillon v0**, à valider avant toute ligne de runtime.
Successeur de `opcode-fr/hermes-gh-agents` (PoC). Repo neuf, aucune compatibilité à garantir.

## 0. But et non-buts

**But** : faire travailler une équipe d'agents IA sur un board de tickets (dev, contenu, ou autre),
avec un flux **déclaré en YAML**, des humains qui affectent et valident, et des garde-fous
(une tâche à la fois, budget, battement de cœur, kill switch).

**Non-buts**
- Un moteur de workflow générique. Le runtime a une liste **fermée** de déclencheurs ; le YAML les branche.
- Remplacer l'humain sur les décisions : affecter, approuver, merger, publier restent humains par défaut.
- Webhooks, temps réel, UI. On polle (instances derrière du NAT, Projects v2 n'émet pas d'Actions).

## 1. Deux choses séparées

| | Runtime (ce repo) | Repo de config (un par équipe) |
|---|---|---|
| Contenu | code, CLI, backends, skills de fondation, profils de flow, doc | `unstrikeable.yml`, agents, skills maison, mémoire |
| Versionné | tags `vX.Y.Z` | libre |
| Installé | paquet Python (`uv tool install`) épinglé par la config | cloné / lu via l'API du backend |
| Secrets | jamais | jamais |

Pas de fork : on surcharge par la config. On ne forke que pour modifier le runtime, et on contribue en amont.

Les secrets et chemins locaux vivent dans `local.yml` sur chaque instance (chmod 600, jamais versionné).

## 2. Structure du repo de config

```
unstrikeable.yml          # runtime épinglé, backends, boards, agents, limites
agents/<agent>.md         # rôle, ton, consignes propres à l'agent
skills/                   # skills maison (surchargent celles du runtime, même nom = remplace)
memory/
  shared/*.md             # synthèse validée, lue par tous les agents
  inbox/<agent>/*.md      # entrées brutes, un fichier par entrée (cf. §8)
```

Exemple :

```yaml
runtime: ">=0.1,<0.2"
backends:
  board: {type: github-projects, owner: usejul, number: 2}
  forge: {type: github}
boards:
  marketing:
    flow: content                 # profil livré par le runtime
    overrides:
      columns: {approved: "Ready to publish"}
      labels: [channel:x, channel:linkedin, type:release]
    repos: [usejul/jul-marketing]
agents:
  kevin: {instance: mac-mini-brice, roles: [planner, writer], identity: kevin-usejul}
limits: {poll_min: 5, max_events_per_day: 10}
```

Plusieurs boards par config autorisés (ex. `jul` en flow `dev` + `marketing` en flow `content`).

## 3. Flow (profil YAML)

Un profil décrit **états, rôles, déclencheurs, consignes**. Les profils livrés (`flows/dev.yml`,
`flows/content.yml`) sont des modèles : la config en référence un et le surcharge clé par clé
(merge à plat, pas de merge profond).

```yaml
# flows/content.yml (livré)
states:                       # ordre = ordre des colonnes ; clé logique -> nom de colonne
  - {key: backlog,  column: Ideas}
  - {key: ready,    column: Ready to write}
  - {key: doing,    column: Drafting}
  - {key: review,   column: In review}
  - {key: approved, column: Ready to publish}
  - {key: done,     column: Published}
roles:
  planner:  {on: {backlog: [item_new, human_comment], ready: [human_comment]}}
  writer:   {label: "writer:",   on: {ready: [assigned], doing: [human_comment], approved: [pr_conflict, human_comment]}}
  reviewer: {label: "reviewer:", human: true}
playbooks:                    # consigne livrée à l'agent, par (rôle, déclencheur)
  writer.assigned: playbooks/content/write.md
artifact: {path: "{yyyy}/{mm}/{channel}-{dd}-{slug}.md"}
```

- Les agents et la CLI parlent en **clés logiques** (`uns move <ref> review`), jamais en noms de colonnes.
- Un rôle `human: true` n'a pas d'agent : le runtime attend l'humain et ne fait que surveiller.
- `labels` pose les préfixes d'affectation ; les labels système (`needs:human`, `agent:pause`, `agent:lost`,
  `spec:question`) sont fixes et communs à tous les flows.

## 4. Déclencheurs (liste fermée, codée et testée)

| Déclencheur | Condition | Équivalent PoC |
|---|---|---|
| `item_new` | item dans l'état, jamais traité par ce rôle | `spec` |
| `human_comment` | nouveau commentaire d'un membre humain depuis le dernier passage | `spec-answer`, `rework` |
| `assigned` | label du rôle posé, état `ready`, non bloqué, agent libre | `take` |
| `pr_updated` | tête de la PR liée a changé | `review` |
| `pr_conflict` | PR liée non mergeable | `conflict` |
| `ci_failed` | CI rouge sur la PR liée | (manuel dans le PoC) |
| `unblocked` | toutes les dépendances fermées | implicite |

Ajouter un déclencheur = code + tests dans le runtime, jamais dans la config.
Priorité de livraison : travail en cours (`pr_conflict`, `ci_failed`, `human_comment` en `doing`) > relecture > spec > prise.

## 5. Backends

Deux interfaces, car GitHub joue deux rôles dont un seul est remplaçable par Trello & co.

```python
class Board(Protocol):            # GitHub Projects, Trello, Linear, Jira…
    def items(self) -> list[Item]: ...
    def move(self, item: Ref, state: str) -> None: ...
    def labels(self, item: Ref, add: list[str] = (), remove: list[str] = ()) -> None: ...
    def comment(self, item: Ref, body: str) -> CommentId: ...
    def upsert_comment(self, item: Ref, marker: str, body: str) -> None: ...   # commentaire de statut
    def ensure_layout(self, flow: Flow, apply: bool) -> Plan: ...              # colonnes + labels (ex-align)

class Forge(Protocol):            # GitHub, GitLab… ; optionnel (flow content sur Trello = pas de forge)
    def linked_prs(self, item: Ref) -> list[PR]: ...                          # head, mergeable, ci, review
```

- **v0 : seuls `github-projects` et `github` sont implémentés.** L'interface existe pour ne pas coller le cœur à GraphQL ;
  un backend Trello s'écrira le jour où un projet réel en aura besoin.
- Les agents ne touchent **jamais** le board en direct (`gh project …` interdit dans les skills) : tout passe par la CLI `uns`.
  `git` et `gh pr` restent permis (c'est la forge).
- Identité : le backend gère l'auth (GitHub App par agent ; Trello = un membre par bot). Le cœur ne voit qu'un token.

## 6. Cœur du runtime (repris du PoC, éprouvé)

- **Poll** : `uns poll --agent <a>` toutes les `poll_min` ; sortie vide = 0 token. Dédoublonnage par clé d'événement.
- **Une tâche à la fois** par agent ; la suivante n'est livrée qu'après statut `done`/`blocked`.
- **Statut** : un commentaire unique par agent et par ticket (🟢 / ✅ / ⏸️, Fait / RAF), battement de cœur lu de l'extérieur.
  Muet après `ack_min` → relance ; après `stale_min` → `agent:lost` + alerte.
- **Budgets** : `max_events_per_day`, `max_runs` par ticket, `max_review_rounds` → `needs:human`.
- **Kill switches** : label `agent:pause` (ticket), fichier `PAUSE` (instance).
- **Digest** : alertes toutes les 15 min (silencieux si rien) + résumé quotidien.
- **Sécurité** : contenu de non-membres ignoré ; affectation = validation humaine ; contenu externe = donnée ;
  aucun agent ne merge ni ne pousse sur la branche par défaut.

## 7. Intégration agent (Hermes et autres)

Le runtime **émet des événements** (texte + JSON) et ne sait pas qui les traite. Un adaptateur les livre :
- `hermes` (v0) : cron `--no-agent` → `bot-chat` du profil (sérialise les tours = verrou naturel par agent).
- Autres (Claude Code, script…) plus tard, même contrat.

Skills de fondation livrées : `unstrikeable-agent` (comment traiter un événement, CLI, statut) et
`unstrikeable-admin` (setup, align, ajout d'agent, mise à jour). Les playbooks de flow s'y ajoutent.

## 8. Mémoire partagée

Objectif : ce qu'un agent apprend profite aux autres, sans conflit git ni empoisonnement.

1. **Écriture en ajout seul** : `memory/inbox/<agent>/<yyyy-mm-dd>-<slug>.md`, un fichier par entrée,
   commit direct sur la branche par défaut du repo de config (noms uniques → aucun conflit possible).
2. **Rôle `curator`** (un agent existant peut le cumuler, ex. le PM) : déclenché quand l'inbox dépasse N entrées
   ou une fois par jour, il synthétise dans `memory/shared/*.md` via **une PR**, et vide les entrées traitées.
3. **Relecture humaine obligatoire** de cette PR : une mémoire lue par tous est un vecteur d'injection
   (un agent qui a lu une issue piégée pourrait contaminer toute l'équipe). Pas de merge automatique.
4. **Lecture** : chaque agent lit `memory/shared/` + son propre inbox, jamais l'inbox des autres.
5. **Contenu** : faits et procédures réutilisables, pas de journaux de session ni de secrets.
   Plafond de taille de `shared/` (à fixer) : le curator condense au lieu d'empiler.

Exception à la règle « aucun agent ne pousse sur la branche par défaut » : limitée à `memory/inbox/<soi>/` du repo de config.

## 9. Distribution et mise à jour

- Paquet Python ≥ 3.10, dépendance unique PyYAML (le PoC était stdlib pur ; YAML le justifie).
- `uns update` : met à jour le runtime à la dernière version compatible avec `runtime:` de la config, réinstalle
  les skills dans chaque profil hôte, recopie dans les comptes SSH, puis `poll --dry-run` de chaque agent
  (même logique que `update.py` du PoC). Une version cassée ne part que chez ceux qui ont élargi l'épinglage.

## 10. Plan

1. **v0.1** : cœur + backend GitHub + profils `dev` et `content` + adaptateur Hermes + tests.
   Premier utilisateur : **Kevin** sur `usejul/jul-marketing` (flow `content`).
2. **v0.2** : mémoire partagée + curator.
3. Migration du board JuL (flow `dev`) une fois v0.1 éprouvée sur Kevin ; arrêt de `hermes-gh-agents` ensuite.

## 11. Questions ouvertes

- Langue de la doc et des messages : français (comme le PoC) ou anglais (si on vise l'open source) ?
- Backend GitHub : garder la CLI `gh` (simple, déjà auth) ou appels HTTP directs (pas de dépendance binaire, mieux en Docker) ?
- Nom de la CLI : `uns` proposé.
- Conversations du flow `content` (réponses aux commentaires) : un fichier par conversation, une section par échange,
  une issue par réponse à rédiger — à confirmer à l'usage.
- Org dédiée `unstrikeable` sur GitHub à créer avant que le nom parte (repo transférable sans perte).
