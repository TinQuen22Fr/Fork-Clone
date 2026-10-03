/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiée
 * par la Free Software Foundation, soit la version 3, soit (à votre choix)
 * toute version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */

import React from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

/**
 * Garde-fou d'affichage : capture toute erreur de rendu d'un sous-arbre et
 * affiche un message lisible au lieu d'un ecran noir silencieux. Sans cela,
 * une ReferenceError ou une erreur d'un composant fait demonter tout React
 * (page noire sans aucune information).
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Visible dans la console pour le diagnostic, sans bloquer l'affichage.
    console.error("[ErrorBoundary]", error, info?.componentStack);
    this.setState({ info });
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.error) return this.props.children;
    const msg = String(this.state.error?.message || this.state.error || "");
    return (
      <div className="min-h-screen w-full flex items-center justify-center bg-[var(--bg-main)] text-white p-6">
        <div className="max-w-lg w-full border border-[#ff2a6d]/40 rounded-sm bg-white/[0.02] p-5 font-mono text-xs">
          <div className="flex items-center gap-2 text-[#ff2a6d] mb-3">
            <AlertTriangle className="w-4 h-4" />
            <span className="uppercase tracking-wide">Erreur d'interface</span>
          </div>
          <p className="text-gray-400 mb-3 leading-relaxed">
            Un composant a échoué au rendu. Le détail technique est masqué à
            l'écran mais disponible dans la console du navigateur.
          </p>
          <pre className="text-[11px] text-gray-500 bg-black/40 border border-white/5 rounded-sm p-2 overflow-auto max-h-40 whitespace-pre-wrap break-words">
            {msg}
          </pre>
          <button
            type="button"
            onClick={this.handleReload}
            className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 border border-[#05d9e8]/40 text-[#05d9e8] rounded-sm hover:bg-[#05d9e8]/10 transition-colors"
            data-testid="error-reload"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Recharger
          </button>
        </div>
      </div>
    );
  }
}
