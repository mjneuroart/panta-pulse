# Preuves S3 — assistant de création + démo (08/10/2026)

Tous les fichiers viennent d'une exécution réelle (headless Chrome `--headless=new` / appels API live).

- `s3-desk-top.png` — le desk : brief IA, signaux, début du catalogue.
- `s3-create-quote-build.png` — assistant de création : quote (fee 50 USDC + event PDA) puis build (tx unsigned + fingerprint) réussis via l'UI.
- `s3-desk-bottom.png` — grille marchés + footer « Powered by Panta ».
- `s3-quote-response.json` — réponse brute du quote Panta (`createId`, `paymentUsdc` 50.00, `expectedEventPda`, expiry).
- `s3-build-response.json` — réponse brute du build Panta (transaction tronquée, `recentBlockhash`, `buildFingerprint`, `derived.event`).
- `../demo/panta-pulse-demo.mp4` — vidéo 33 s (screencast CDP) : catalogue → signaux → formulaire → quote live → build live.

Vérifications par exécution :

- `scripts/demo_headless.py --check` : **PASS** (2 passes) — flux quote+build via l'UI réelle ; contrôle : aucune clé API dans le DOM (`pk_live`/`pk_test` absents).
- `scripts/demo_headless.py --diag` : flux complet avec capture réseau des réponses `/api/create/*`.
- OCR vidéo (tesseract) : « Creation fee 50.00 USDC (liquidity 10.00 + platform 40.00) », « Transaction (base64) », « Step 3 — sign + broadcast in your wallet (blockhash valid ~60s) ».

Note : les marchés de démo ne sont jamais créés on-chain — la chaîne s'arrête au build unsigned ; la signature appartient au wallet de l'utilisateur.
