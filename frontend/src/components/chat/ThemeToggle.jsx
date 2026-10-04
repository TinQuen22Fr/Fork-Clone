import { Moon, Sun } from "lucide-react";

/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiee
 * par la Free Software Foundation, soit la version 3, soit (a votre choix)
 * toute version ulterieure. Il est distribue SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */
export default function ThemeToggle({ theme, onToggle }) {
  return (
    <div
      className="theme-toggle-group"
      data-testid="theme-toggle"
      title="Changer de thème"
    >
      <button
        type="button"
        className={`theme-toggle-btn ${theme === "dark" ? "active" : ""}`}
        onClick={() => theme !== "dark" && onToggle()}
        title="Thème sombre"
        data-testid="theme-toggle-dark"
      >
        <Moon className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        className={`theme-toggle-btn ${theme === "light" ? "active" : ""}`}
        onClick={() => theme !== "light" && onToggle()}
        title="Thème clair"
        data-testid="theme-toggle-light"
      >
        <Sun className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
