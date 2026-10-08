# Panta Pulse — GO-LIVE (jour J, checklist) — NE RIEN FAIRE SANS LE GO DE MIKE

État actuel : **local uniquement**. Service systemd user `panta-pulse` sur 127.0.0.1:8090,
persistant (Linger=yes, Restart=always). Aucune exposition publique (pas de Caddy, pas de domaine).

## Le jour du GO
1. Confirmer la santé locale :
   - `curl -sS http://127.0.0.1:8090/healthz`
   - `curl -sS http://127.0.0.1:8090/api/signals | head -c 400`
2. Ajouter le bloc `/root/bounty/panta/deploy/Caddyfile.draft` au `/etc/caddy/Caddyfile` (depuis le terminal — chemin système).
3. `systemctl reload caddy`
4. Vérifier : `curl -sS https://panta.168-231-106-101.sslip.io/healthz`
5. Contrôler la mention « Powered by Panta » (critère Panta) : elle est dans le footer du front.
6. Mettre à jour le README (URL de démo) + le dossier de soumission Colosseum.

## Rappels
- Rien de public avant GO (règle projet : action publique = GO Mike).
- Le site expose des données catalogue publiques uniquement (pas de clé, pas d'écriture).
- Si un jour des écritures (quote/build) sont ajoutées : elles restent unsigned (le wallet de
  l'utilisateur signe) — vérifier le ToS avant toute soumission automatisée.
