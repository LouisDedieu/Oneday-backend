## ONEAAA-10: Implémenter ErrorCode enum et middleware

**Agent**: Backend Engineer
**Branch**: feature/ONEAAA-9-error-codes

### Analyse
- Fichiers impactés :
  - `models/errors.py` (nouveau) — Enum ErrorCode et réponses d'erreur
  - `main.py` — Ajout du middleware de formatting
  - `api/trips.py` — Mise à jour des erreurs
  - `api/cities.py` — Mise à jour des erreurs
  - `api/analyze.py` — Mise à jour des erreurs
  - `services/notification_service.py` — Utiliser ErrorCode
- Risques : Changement rétrocompatible si on garde les messages dans le format existant
- Dépendances : Aucune

### Plan
- [ ] Créer `models/errors.py` avec enum ErrorCode et classes de réponse
- [ ] Créer middleware de formatting dans `main.py`
- [ ] Mettre à jour les endpoints pour utiliser les nouveaux codes
- [ ] Utiliser ErrorCode dans notification_service.py
- [ ] python -m pytest
- [ ] ruff check .
- [ ] Commit & push
- [ ] Mettre à jour STATE_OF_THE_ART.md