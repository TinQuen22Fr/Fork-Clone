// Service worker minimal : requis pour que le navigateur propose l'installation.
// Volontairement SANS cache applicatif : la forge parle a une API en direct, un
// cache mal invalide servirait un vieux bundle (piege deja rencontre en prod).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  // Réseau uniquement. En cas de coupure, on laisse le navigateur afficher son
  // erreur native plutôt que de servir une page périmée.
  return;
});
