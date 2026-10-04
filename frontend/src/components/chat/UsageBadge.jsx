import { Gauge } from "lucide-react";

export default function UsageBadge({ usage }) {
  const windows = [
    { key: "rolling", label: "5H" },
    { key: "weekly", label: "SEM" },
    { key: "monthly", label: "MOIS" },
  ].filter((w) => usage?.[w.key]);
  if (!windows.length) return null;

  const color = (p) =>
    p >= 90 ? "#ff2a6d" : p >= 70 ? "#ffd700" : "#05d9e8";

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 border-2 border-white/20 bg-black/40 px-2 py-1 max-w-full"
      title={
        "Forfait OpenCode Go — " +
        windows
          .map(
            (w) =>
              `${w.label} : ${Math.round(usage[w.key].percent)}% utilisé (reset ${new Date(
                usage[w.key].resetsAt
              ).toLocaleString("fr-FR")})`
          )
          .join(" · ")
      }
      data-testid="usage-badge"
    >
      <Gauge className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
      {windows.map((w) => {
        const pct = Math.min(100, Math.max(0, usage[w.key].percent || 0));
        return (
          <div key={w.key} className="flex items-center gap-1 shrink-0">
            <span className="text-[9px] font-mono text-gray-500">{w.label}</span>
            <div className="w-8 sm:w-10 h-1.5 bg-white/10 shrink-0">
              <div
                className="h-full transition-all"
                style={{ width: `${pct}%`, backgroundColor: color(pct) }}
              />
            </div>
            <span
              className="text-[9px] font-mono"
              style={{ color: color(pct) }}
              data-testid={`usage-${w.key}`}
            >
              {Math.round(pct)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}
