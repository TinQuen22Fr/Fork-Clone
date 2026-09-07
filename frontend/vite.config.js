import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const devApiTarget = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8001";

  return {
    plugins: [react()],

    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },

    server: {
      host: true,
      port: 3000,
      allowedHosts: true,
      hmr: { clientPort: 443 },
      // Reproduit en dev la même origine qu'en prod derrière Nginx :
      // pas de CORS, pas de cookie cross-site à gérer.
      proxy: {
        "/api": {
          target: devApiTarget,
          changeOrigin: true,
          secure: false,
        },
      },
    },

    build: {
      outDir: "build", // chemin attendu par la config Nginx
      sourcemap: false,
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            markdown: ["react-markdown"],
            motion: ["framer-motion"],
          },
        },
      },
    },
  };
});
