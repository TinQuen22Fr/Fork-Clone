import { Menu, X } from "lucide-react";
import UsageBadge from "./UsageBadge";
import PreviewButton from "@/components/PreviewButton";

/**
 * ChatHeader - barre superieure de la conversation (titre session, badge usage,
 * selecteur preview projet, label modele actif).
 * Extrait de Chat.jsx (Etape 3 - Phase C).
 */
export default function ChatHeader({
  onOpenSidebar,
  activeId,
  onCloseSession,
  onAbortRequest,
  activeConv,
  projectUrls,
  onProjectUrlSaved,
  usage,
  provider,
  autoChain,
  models,
  activeModel,
  modelOverride,
}) {
        <header className="border-b-2 border-white/10 px-3 sm:px-4 lg:px-8 py-3 sm:py-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <button
              className="lg:hidden btn-ghost flex-shrink-0"
              onClick={() => onOpenSidebar(true)}
              data-testid="open-sidebar-btn"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="min-w-0 flex flex-col gap-1">
              {activeId && (
                <button
                  className="self-start text-gray-500 hover:text-white hover:opacity-100 opacity-60 transition-all duration-150 cursor-pointer bg-transparent border-0 p-0 leading-none"
                  onClick={() => {
                    onAbortRequest();
                    onCloseSession();
                  }}
                  title="Retour au choix des projets"
                  data-testid="close-session-btn"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
              <div className="text-[10px] uppercase tracking-[0.3em] text-[#ffd700] font-bold">
                // active session
              </div>
              <div className="font-heading font-black truncate text-base sm:text-lg">
                {activeConv?.title || "No conversation selected"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0 min-w-0">
            {activeConv?.project && (
              <PreviewButton
                project={activeConv.project}
                previewUrl={
                  projectUrls[activeConv.project] ?? activeConv.preview_url ?? ""
                }
                onSaved={(url) =>
                  onProjectUrlSaved(activeConv.project, url)
                }
              />
            )}
            {usage && <UsageBadge usage={usage} />}
            <div
              className="text-[10px] sm:text-xs font-mono text-gray-500 hidden sm:block truncate max-w-[220px] lg:max-w-[420px] text-right"
              data-testid="active-model-label"
            >
            {provider === "auto" ? (
              <>
                AUTO:{" "}
                <span className="text-[#05d9e8]">
                  {autoChain.length
                    ? models.find((m) => m.id === autoChain[0])?.model ||
                      autoChain[0]
                    : "aucun provider configuré"}
                </span>
                {autoChain.length > 1 && (
                  <span className="text-gray-600">
                    {" "}
                    → {autoChain.slice(1).join(" → ")}
                  </span>
                )}
              </>
            ) : (
              <>
                {activeModel?.label || provider}:{" "}
                <span className="text-[#05d9e8]">
                  {modelOverride || activeModel?.model || "…"}
                </span>
                {modelOverride && (
                  <span className="text-[#ffd700]"> (choisi)</span>
                )}
                {activeModel && activeModel.available === false && (
                  <span className="text-[#ff2a6d]"> (non configuré)</span>
                )}
              </>
            )}
            </div>
          </div>
        </header>
}
